"""Mutual Fund Data Layer.

Three jobs:

1. Hold a normalised scheme record — one shape, whatever the feed looked like.
2. Reconcile the same field arriving from several sources, by a published
   hierarchy, and record when the sources disagree instead of silently taking
   the first one.
3. Score data quality, so that "we don't have good enough data on this scheme"
   is a first-class outcome the pipeline can act on. Below the floor, the
   scheme is not scored, not recommended and not shown as if it were known.

Single-vendor dependence is the most common quiet failure in Indian fintech
data: one feed, no cross-check, a stale expense ratio, and a recommendation
built on it. Hence the hierarchy and the disagreement flags.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

# Published source hierarchy. Lower number wins a disagreement.
SOURCE_HIERARCHY = {
    "SEBI": 1,
    "GOI": 2,
    "INCOME_TAX_DEPT": 2,
    "AMFI": 3,
    "AMC_SID": 4,          # scheme information document / addendum
    "AMC_FACTSHEET": 5,
    "RTA": 6,              # CAMS / KFin
    "EXCHANGE": 7,         # BSE StAR MF / NSE MFSS
    "VENDOR": 8,
    "DERIVED": 9,          # computed by us from a higher source
    "USER_SUPPLIED": 10,
}

MANAGEMENT_STYLES = ("ACTIVE", "PASSIVE")
PLANS = ("DIRECT", "REGULAR")


@dataclass
class Observation:
    """One field, from one source, at one time. Never overwritten in place."""

    value: object
    source: str
    as_of: datetime
    url: str = None

    @property
    def rank(self):
        return SOURCE_HIERARCHY.get(self.source, 99)

    def age_hours(self, now=None):
        now = now or datetime.utcnow()
        return (now - self.as_of).total_seconds() / 3600.0


class Field:
    """All observations of one field, with a resolution policy."""

    def __init__(self, name, tolerance_pct=0.5):
        self.name = name
        self.tolerance_pct = tolerance_pct
        self.observations = []

    def add(self, value, source, as_of, url=None):
        self.observations.append(Observation(value, source, as_of, url))
        return self

    def resolve(self):
        """Highest-ranked source wins; ties broken by recency."""
        if not self.observations:
            return None
        return sorted(self.observations, key=lambda o: (o.rank, -o.as_of.timestamp()))[0]

    @property
    def value(self):
        obs = self.resolve()
        return obs.value if obs else None

    def disagreement(self):
        """Do independent sources materially disagree on a numeric field?"""
        numeric = [o for o in self.observations if isinstance(o.value, (int, float))]
        if len(numeric) < 2:
            return None
        values = [float(o.value) for o in numeric]
        lo, hi = min(values), max(values)
        if lo == 0:
            return None
        spread = (hi - lo) / abs(lo) * 100
        if spread <= self.tolerance_pct:
            return None
        return {
            "field": self.name,
            "spread_pct": round(spread, 3),
            "sources": [{"source": o.source, "value": o.value, "as_of": o.as_of.isoformat()}
                        for o in numeric],
        }

    def independent_sources(self):
        return len({o.source for o in self.observations})

    def freshest_age_hours(self, now=None):
        obs = self.resolve()
        return obs.age_hours(now) if obs else None


@dataclass
class Scheme:
    """A normalised scheme record. Fields that are None are unknown — and the
    engines treat unknown as unknown, never as zero."""

    scheme_code: str
    scheme_name: str
    amc: str
    plan: str = "DIRECT"                  # DIRECT | REGULAR
    option: str = "GROWTH"
    sebi_category: str = None             # e.g. EQUITY_FLEXI_CAP
    asset_class: str = None               # maps to capital market assumptions
    tax_class: str = None                 # EQUITY_ORIENTED etc — see tax.py
    management_style: str = "ACTIVE"
    benchmark_code: str = None
    riskometer: str = None
    launch_date: date = None

    nav: float = None
    nav_date: date = None
    aum_cr: float = None
    expense_ratio_pct: float = None
    exit_load_bands: list = field(default_factory=list)

    fund_manager: str = None
    manager_since: date = None
    manager_changes_5y: int = 0

    holdings: dict = field(default_factory=dict)      # {security: weight_pct}
    sector_allocation: dict = field(default_factory=dict)
    market_cap_allocation: dict = field(default_factory=dict)
    top10_weight_pct: float = None
    portfolio_turnover_pct: float = None
    illiquid_weight_pct: float = None

    nav_history: list = field(default_factory=list)   # [(date, nav)]
    benchmark_history: list = field(default_factory=list)

    # commercial fields — present so they can be DISCLOSED, and deliberately
    # unreachable from the scoring code (see prohibited_inputs in the ruleset).
    distributor_commission_pct: float = None
    amc_relationship: str = None

    fields: dict = field(default_factory=dict)        # name -> Field, provenance
    flags: list = field(default_factory=list)

    # ── provenance ───────────────────────────────────────────────────────────
    def observe(self, name, value, source, as_of, url=None, tolerance_pct=0.5):
        f = self.fields.setdefault(name, Field(name, tolerance_pct))
        f.add(value, source, as_of, url)
        if hasattr(self, name):
            setattr(self, name, f.value)
        return self

    def provenance(self, name):
        f = self.fields.get(name)
        if not f:
            return None
        obs = f.resolve()
        return {
            "field": name, "value": obs.value, "source": obs.source,
            "as_of": obs.as_of.isoformat(), "url": obs.url,
            "independent_sources": f.independent_sources(),
        }

    def disagreements(self):
        return [d for d in (f.disagreement() for f in self.fields.values()) if d]

    @property
    def history_months(self):
        if len(self.nav_history) < 2:
            return 0
        return int((self.nav_history[-1][0] - self.nav_history[0][0]).days / 30.44)

    @property
    def is_passive(self):
        return self.management_style == "PASSIVE"


# ── data quality ─────────────────────────────────────────────────────────────

# Fields that must exist before a scheme can be scored at all.
CRITICAL_FIELDS = ("nav", "expense_ratio_pct", "sebi_category", "riskometer", "benchmark_code")
IMPORTANT_FIELDS = ("aum_cr", "top10_weight_pct", "fund_manager", "manager_since", "holdings")


def data_quality_score(scheme, registry, now=None):
    """0-100, with the reasons attached.

    Below registry.threshold('min_data_quality_score') the scheme is excluded
    from recommendations. That is DATA QUALITY OVERRIDES AI, implemented.
    """
    now = now or datetime.utcnow()
    reasons = []
    score = 100.0

    # completeness
    missing_critical = [f for f in CRITICAL_FIELDS if getattr(scheme, f, None) in (None, "", [])]
    if missing_critical:
        penalty = len(missing_critical) * 18.0
        score -= penalty
        reasons.append({"issue": "MISSING_CRITICAL", "fields": missing_critical,
                        "penalty": -penalty})
    missing_important = [f for f in IMPORTANT_FIELDS if getattr(scheme, f, None) in (None, "", [], {})]
    if missing_important:
        penalty = len(missing_important) * 5.0
        score -= penalty
        reasons.append({"issue": "MISSING_IMPORTANT", "fields": missing_important,
                        "penalty": -penalty})

    # freshness, per field class
    staleness_limits = registry.threshold("max_data_staleness_hours")
    checks = (("nav", "nav"), ("holdings", "portfolio_holdings"),
              ("expense_ratio_pct", "expense_ratio"))
    for field_name, limit_key in checks:
        f = scheme.fields.get(field_name)
        if not f:
            continue
        age = f.freshest_age_hours(now)
        limit = staleness_limits.get(limit_key)
        if age is not None and limit and age > limit:
            penalty = min(25.0, (age - limit) / limit * 20.0)
            score -= penalty
            reasons.append({"issue": "STALE", "field": field_name,
                            "age_hours": round(age, 1), "limit_hours": limit,
                            "penalty": -round(penalty, 1)})

    # source quality
    for field_name in ("nav", "expense_ratio_pct", "aum_cr"):
        f = scheme.fields.get(field_name)
        if not f:
            continue
        obs = f.resolve()
        if obs and obs.rank >= SOURCE_HIERARCHY["VENDOR"]:
            score -= 6.0
            reasons.append({"issue": "LOW_RANK_SOURCE", "field": field_name,
                            "source": obs.source, "penalty": -6.0})
        if f.independent_sources() < 2 and field_name == "nav":
            score -= 8.0
            reasons.append({"issue": "SINGLE_SOURCE", "field": field_name, "penalty": -8.0})

    # cross-source disagreement
    for d in scheme.disagreements():
        penalty = min(20.0, d["spread_pct"] * 4)
        score -= penalty
        reasons.append({"issue": "SOURCE_DISAGREEMENT", **d, "penalty": -round(penalty, 1)})

    # history
    if scheme.history_months < registry.threshold("min_history_months_for_scoring"):
        score -= 10.0
        reasons.append({"issue": "SHORT_HISTORY", "months": scheme.history_months,
                        "penalty": -10.0})

    # internal consistency
    if scheme.holdings:
        total = sum(scheme.holdings.values())
        if not (95.0 <= total <= 105.0):
            score -= 10.0
            reasons.append({"issue": "HOLDINGS_DO_NOT_SUM", "total_pct": round(total, 2),
                            "penalty": -10.0})
    if scheme.nav is not None and scheme.nav <= 0:
        score -= 40.0
        reasons.append({"issue": "IMPLAUSIBLE_NAV", "value": scheme.nav, "penalty": -40.0})

    score = max(0.0, min(100.0, score))
    floor = registry.threshold("min_data_quality_score")
    return {
        "score": round(score, 1),
        "usable": score >= floor,
        "floor": floor,
        "reasons": reasons,
        "freshest_nav_hours": (scheme.fields["nav"].freshest_age_hours(now)
                               if "nav" in scheme.fields else None),
        "independent_source_count": max(
            [f.independent_sources() for f in scheme.fields.values()] or [0]
        ),
    }


def require_usable(scheme, registry):
    from .errors import DataQualityFailure
    dq = data_quality_score(scheme, registry)
    if not dq["usable"]:
        raise DataQualityFailure(
            f"I don't have sufficient verified data on {scheme.scheme_name} to make a "
            "recommendation about it.",
            detail={"scheme_code": scheme.scheme_code, "data_quality": dq},
        )
    return dq


class SchemeUniverse:
    """The investable set, and the peer groups within it."""

    def __init__(self, schemes=()):
        self.schemes = {s.scheme_code: s for s in schemes}

    def add(self, scheme):
        self.schemes[scheme.scheme_code] = scheme
        return self

    def get(self, code):
        return self.schemes.get(code)

    def __len__(self):
        return len(self.schemes)

    def __iter__(self):
        return iter(self.schemes.values())

    def peers(self, scheme):
        """Same SEBI category only. Cross-category comparison is prohibited."""
        return [s for s in self.schemes.values()
                if s.sebi_category == scheme.sebi_category and s.scheme_code != scheme.scheme_code]

    def category(self, sebi_category):
        return [s for s in self.schemes.values() if s.sebi_category == sebi_category]

    def usable(self, registry):
        return [s for s in self.schemes.values() if data_quality_score(s, registry)["usable"]]
