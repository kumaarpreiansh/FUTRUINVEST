"""Investor Profile Service.

Two ideas run through this module.

First, progressive profiling. Fifty questions on screen one is how you lose an
investor. Ten questions gets a usable plan; the rest are asked when they start
to matter, and always with a reason attached ("you want to retire in 15 years,
but I don't know what you've already saved for it").

Second, profile versioning. A recommendation is only defensible against the
profile that produced it. Every material edit bumps the version, and the
version is stamped on every recommendation and every audit row.
"""

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from .errors import InsufficientData
from .money import money

# Tier 1 is the whole of onboarding. Everything else is earned later.
TIER_1 = (
    "age", "monthly_income", "monthly_expenses", "current_savings",
    "existing_investments", "total_loan_emi", "emergency_fund",
    "investment_experience", "primary_goals", "primary_horizon_years",
)

TIER_2 = (
    "dependants", "employment_type", "income_stability", "expected_income_growth_pct",
    "life_insurance_cover", "health_insurance_cover", "outstanding_loan_amount",
    "existing_sips", "marginal_tax_rate_pct", "spouse_income",
)

TIER_3 = (
    "existing_mutual_funds", "other_assets", "liquidity_needs_next_12m",
    "retirement_age", "expected_inheritance", "employer_retirement_corpus",
    "risk_tolerance_answers", "past_market_behaviour", "tax_regime",
)

EXPERIENCE_LEVELS = ("NONE", "BEGINNER", "INTERMEDIATE", "EXPERIENCED")
INCOME_STABILITY = ("VOLATILE", "VARIABLE", "STABLE", "VERY_STABLE")

PROFILE_STALE_DAYS = 365
PROFILE_STALE_DAYS_HIGH_IMPACT = 180


@dataclass
class InvestorProfile:
    investor_id: str

    # Tier 1
    age: int = None
    monthly_income: Decimal = None
    monthly_expenses: Decimal = None
    current_savings: Decimal = None
    existing_investments: Decimal = None
    total_loan_emi: Decimal = None
    emergency_fund: Decimal = None
    investment_experience: str = None
    primary_goals: list = field(default_factory=list)
    primary_horizon_years: float = None

    # Tier 2
    dependants: int = None
    employment_type: str = None
    income_stability: str = None
    expected_income_growth_pct: float = None
    life_insurance_cover: Decimal = None
    health_insurance_cover: Decimal = None
    outstanding_loan_amount: Decimal = None
    existing_sips: Decimal = None
    marginal_tax_rate_pct: float = None
    spouse_income: Decimal = None

    # Tier 3
    existing_mutual_funds: list = field(default_factory=list)
    other_assets: dict = field(default_factory=dict)
    liquidity_needs_next_12m: Decimal = None
    retirement_age: int = None
    expected_inheritance: Decimal = None
    employer_retirement_corpus: Decimal = None
    risk_tolerance_answers: dict = field(default_factory=dict)
    past_market_behaviour: str = None
    tax_regime: str = None

    # Bookkeeping
    version: int = 1
    updated_on: date = None
    kyc_status: str = "NOT_STARTED"       # NOT_STARTED | IN_PROGRESS | VALIDATED | REJECTED
    consents: dict = field(default_factory=dict)

    def __post_init__(self):
        self.updated_on = self.updated_on or date.today()
        for name in ("monthly_income", "monthly_expenses", "current_savings",
                     "existing_investments", "total_loan_emi", "emergency_fund",
                     "life_insurance_cover", "health_insurance_cover",
                     "outstanding_loan_amount", "existing_sips", "spouse_income",
                     "liquidity_needs_next_12m", "expected_inheritance",
                     "employer_retirement_corpus"):
            value = getattr(self, name)
            if value is not None:
                setattr(self, name, money(value))

    # ── versioning ───────────────────────────────────────────────────────────
    def update(self, **changes):
        """Any material change bumps the version. Recommendations pin a version."""
        material = False
        for key, value in changes.items():
            if not hasattr(self, key):
                raise InsufficientData(f"unknown profile field {key!r}")
            if getattr(self, key) != value:
                material = True
            setattr(self, key, value)
        if material:
            self.version += 1
            self.updated_on = date.today()
        self.__post_init__()
        return self

    def fingerprint(self):
        """Stable hash of the profile — pinned into the audit trail so a later
        reader can prove which profile a recommendation was computed from."""
        payload = {k: str(v) for k, v in sorted(asdict(self).items())
                   if k not in ("consents",)}
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]

    # ── derived quantities ───────────────────────────────────────────────────
    @property
    def monthly_surplus(self):
        if self.monthly_income is None or self.monthly_expenses is None:
            return None
        emi = self.total_loan_emi or Decimal("0")
        return money(self.monthly_income - self.monthly_expenses - emi)

    @property
    def savings_rate_pct(self):
        surplus = self.monthly_surplus
        if surplus is None or not self.monthly_income:
            return None
        return float(surplus) / float(self.monthly_income) * 100.0

    @property
    def emergency_fund_months(self):
        if self.emergency_fund is None or not self.monthly_expenses:
            return None
        return float(self.emergency_fund) / float(self.monthly_expenses)

    @property
    def emi_to_income_pct(self):
        if self.total_loan_emi is None or not self.monthly_income:
            return None
        return float(self.total_loan_emi) / float(self.monthly_income) * 100.0

    @property
    def net_worth(self):
        assets = sum(float(x or 0) for x in (
            self.current_savings, self.existing_investments, self.emergency_fund,
            self.employer_retirement_corpus,
        ))
        assets += sum(float(v) for v in (self.other_assets or {}).values())
        return money(assets - float(self.outstanding_loan_amount or 0))

    @property
    def years_to_retirement(self):
        if self.age is None:
            return None
        return max(0, (self.retirement_age or 60) - self.age)

    # ── completeness ─────────────────────────────────────────────────────────
    def missing(self, tier=None):
        fields = {1: TIER_1, 2: TIER_2, 3: TIER_3}
        pool = fields[tier] if tier else TIER_1 + TIER_2 + TIER_3
        out = []
        for name in pool:
            value = getattr(self, name, None)
            if value is None or value == [] or value == {}:
                out.append(name)
        return out

    def completeness_pct(self, tier=1):
        fields = {1: TIER_1, 2: TIER_2, 3: TIER_3}[tier]
        have = sum(1 for f in fields if f not in self.missing())
        return round(have / len(fields) * 100, 1)

    @property
    def is_minimally_complete(self):
        return not self.missing(tier=1)

    def is_stale(self, high_impact=False):
        limit = PROFILE_STALE_DAYS_HIGH_IMPACT if high_impact else PROFILE_STALE_DAYS
        return (date.today() - self.updated_on) > timedelta(days=limit)


# ── progressive profiling ────────────────────────────────────────────────────

# Each entry says WHY the field is being asked. A question without a reason is
# an interrogation; a question with one is a conversation.
FIELD_IMPACT = {
    "age":                       (100, "Everything downstream depends on how long your money has to work."),
    "monthly_income":            (100, "It sets what you can realistically contribute."),
    "monthly_expenses":          (100, "It sets both your surplus and the size of your emergency fund."),
    "emergency_fund":            (95,  "Without it, the first shock forces you to sell investments at the worst time."),
    "primary_horizon_years":     (95,  "Horizon, not risk appetite, decides how much equity is appropriate."),
    "total_loan_emi":            (85,  "Debt servicing sets a hard ceiling on how much risk you can carry."),
    "existing_investments":      (85,  "I can't tell you what to add until I know what you already hold."),
    "dependants":                (80,  "Dependants lower your risk capacity regardless of your risk appetite."),
    "income_stability":          (80,  "Variable income needs a larger buffer and a gentler equity path."),
    "marginal_tax_rate_pct":     (75,  "Some schemes are taxed at your slab rate — without it I can't give you a tax figure at all."),
    "existing_mutual_funds":     (75,  "Overlap between funds is the most common hidden risk in Indian portfolios."),
    "health_insurance_cover":    (70,  "An uninsured medical event is the most common reason investors liquidate long-term goals."),
    "life_insurance_cover":      (70,  "If others depend on your income, cover comes before investing."),
    "current_savings":           (65,  "It tells me how much of the goal is already funded."),
    "outstanding_loan_amount":   (60,  "It affects your net worth and your capacity to take risk."),
    "liquidity_needs_next_12m":  (60,  "Money needed within a year should not be in a market-linked scheme."),
    "retirement_age":            (55,  "It sets the length of the retirement runway."),
    "investment_experience":     (50,  "It changes how much I explain, not what I recommend."),
    "risk_tolerance_answers":    (50,  "It measures how much volatility you can live with, which is separate from how much you can afford."),
    "existing_sips":             (45,  "Existing commitments count towards your goals."),
    "expected_income_growth_pct": (40, "It decides how fast your SIP can step up."),
    "past_market_behaviour":     (40,  "What you did in the last fall predicts the next one better than a questionnaire."),
    "employer_retirement_corpus": (40, "EPF and NPS often cover more of retirement than people assume."),
    "primary_goals":             (90,  "Without a goal there is nothing to be suitable for."),
    "employment_type":           (35,  "It affects income stability and tax."),
    "spouse_income":             (30,  "Household capacity, not individual capacity, is what carries a goal."),
    "other_assets":              (30,  "Property and gold already in the household change what the portfolio needs to do."),
    "tax_regime":                (30,  "It changes the tax treatment of some decisions."),
    "expected_inheritance":      (20,  "Only relevant if it is reasonably certain."),
}


def next_questions(profile, limit=3, context=None):
    """The next few questions worth asking, most valuable first.

    `context` lets the caller raise the priority of what the current screen
    actually needs — e.g. a redemption screen must have the marginal tax rate.
    """
    context = context or {}
    ranked = []
    for name in profile.missing():
        impact, reason = FIELD_IMPACT.get(name, (10, "It improves the accuracy of your plan."))
        impact += context.get(name, 0)
        ranked.append({"field": name, "impact": impact, "why_asking": reason})
    ranked.sort(key=lambda q: -q["impact"])
    return ranked[:limit]


def blocking_gaps(profile, required_fields):
    """Fields this specific decision cannot proceed without.

    Returns the gaps rather than raising, so the caller can decide between
    asking the investor and halting.
    """
    return [f for f in required_fields if getattr(profile, f, None) in (None, [], {})]


def require(profile, required_fields, purpose):
    gaps = blocking_gaps(profile, required_fields)
    if gaps:
        raise InsufficientData(
            f"I don't have enough about you to {purpose}.",
            detail={
                "missing": gaps,
                "questions": [
                    {"field": g, "why_asking": FIELD_IMPACT.get(g, (0, ""))[1]} for g in gaps
                ],
            },
        )
    return True
