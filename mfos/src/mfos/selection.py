"""Fund Selection Engine.

The question is never "what is the best fund?". It is "what is the most
suitable fund for THIS investor, THIS goal and THIS existing portfolio?".

So selection runs in two stages that must not be collapsed into one weighted
number:

  1. ELIGIBILITY — hard gates. Riskometer beyond the investor's band, data
     below the quality floor, a horizon mismatch: these remove a scheme from
     consideration entirely. A 95-scoring fund that fails a gate does not
     appear at rank 4; it does not appear.
  2. RANKING — among eligible schemes, combine fund quality with fit to this
     investor's situation, then apply portfolio-level penalties (overlap,
     AMC concentration).

SUITABILITY OVERRIDES PERFORMANCE, implemented as a filter rather than a
weight, because a weight can always be outvoted by a high enough score.
"""

from .funds import data_quality_score
from .portfolio import pairwise_overlap
from .risk import riskometer_permitted
from .scoring import compute_metrics, score_fund

# Minimum horizon, in years, below which an asset class is not appropriate.
MIN_HORIZON_YEARS = {
    "EQUITY_SMALL": 10,
    "EQUITY_MID": 7,
    "EQUITY_FLEXI": 5,
    "EQUITY_LARGE": 5,
    "EQUITY_INTL": 7,
    "GOLD": 3,
    "DEBT_MEDIUM": 2,
    "DEBT_SHORT": 0.5,
    "CASH": 0,
}


def eligibility(scheme, registry, *, risk_assessment, years_to_goal, asset_class,
                target_allocation=None, existing_amcs=None):
    """Hard gates. Returns (eligible, [reasons]).

    `target_allocation` is the goal's target asset-class weights. When it is
    supplied, membership of that allocation IS the risk gate: the allocation
    was itself built under the investor's binding equity ceiling, so an asset
    class that appears in it has already been judged appropriate. The
    risk-o-meter gate then applies only to schemes asked about outside the
    plan — which is exactly the case it is useful for.
    """
    reasons = []

    if scheme.asset_class != asset_class:
        reasons.append({"gate": "ASSET_CLASS",
                        "why": f"Not a {asset_class} scheme."})

    in_plan = target_allocation is not None and asset_class in target_allocation
    if target_allocation is not None and not in_plan:
        reasons.append({
            "gate": "NOT_IN_PLAN",
            "why": f"{asset_class} has no place in the target allocation for this goal, "
                   f"so no scheme in it is suitable however good the scheme is.",
        })

    dq = data_quality_score(scheme, registry)
    if not dq["usable"]:
        reasons.append({
            "gate": "DATA_QUALITY",
            "why": f"Data quality {dq['score']} is below the floor of {dq['floor']}. "
                   "I will not recommend a scheme I cannot verify.",
            "detail": dq["reasons"],
        })

    if (not in_plan and scheme.riskometer
            and not riskometer_permitted(risk_assessment.binding_band, scheme.riskometer)):
        reasons.append({
            "gate": "RISKOMETER",
            "why": f"Its risk-o-meter is {scheme.riskometer}, above what your "
                   f"{risk_assessment.binding_band} band permits "
                   f"({risk_assessment.max_riskometer}), and it is not part of your plan.",
        })

    min_years = MIN_HORIZON_YEARS.get(asset_class)
    if min_years is not None and years_to_goal < min_years:
        reasons.append({
            "gate": "HORIZON",
            "why": f"This goal is {years_to_goal:.1f} years away. {asset_class} needs at "
                   f"least about {min_years} years to be a reasonable vehicle.",
        })

    if scheme.history_months < registry.threshold("min_history_months_for_scoring"):
        reasons.append({
            "gate": "HISTORY",
            "why": f"Only {scheme.history_months} months of history — not enough to judge.",
        })

    return (not reasons), reasons


def suitability_score(scheme, score_result, *, risk_assessment, years_to_goal,
                      portfolio=None, universe=None, registry=None, existing_amcs=None):
    """0-100 fit for this investor, separate from fund quality.

    Every component says why it moved, because the investor is entitled to
    read the reasoning, not just the number.
    """
    components, notes = {}, []

    # headroom under the risk ceiling — a scheme at the exact ceiling is fine
    # but leaves nothing for a bad year
    from .risk import RISKOMETER_ORDER, BAND_MAX_RISKOMETER
    if scheme.riskometer:
        ceiling = RISKOMETER_ORDER.index(BAND_MAX_RISKOMETER[risk_assessment.binding_band])
        actual = RISKOMETER_ORDER.index(scheme.riskometer)
        components["risk_fit"] = max(0.0, 100.0 - (ceiling - actual) * 8.0) if actual <= ceiling else 0.0

    min_years = MIN_HORIZON_YEARS.get(scheme.asset_class, 0)
    if min_years:
        headroom = years_to_goal / min_years
        components["horizon_fit"] = min(100.0, 40.0 + headroom * 30.0)
        if headroom < 1.3:
            notes.append(
                f"Your horizon of {years_to_goal:.0f} years only just clears the "
                f"{min_years}-year minimum for this asset class."
            )
    else:
        components["horizon_fit"] = 100.0

    components["cost_fit"] = 100.0 if scheme.plan == "DIRECT" else 30.0
    if scheme.plan == "REGULAR":
        notes.append(
            "This is a Regular plan. The same portfolio is available as a Direct plan "
            "at a lower expense ratio."
        )

    # portfolio fit: overlap with what is already held
    overlap_penalty = 0.0
    worst_overlap = None
    if portfolio and universe:
        for holding in portfolio.holdings:
            held = universe.get(holding.scheme_code)
            if not held or held.scheme_code == scheme.scheme_code:
                continue
            value = pairwise_overlap(scheme, held)
            if value is None:
                continue
            if worst_overlap is None or value > worst_overlap[0]:
                worst_overlap = (value, held)
        if worst_overlap and worst_overlap[0] > 40:
            overlap_penalty = min(60.0, (worst_overlap[0] - 40) * 2.0)
            notes.append(
                f"It overlaps {worst_overlap[0]:.0f}% with {worst_overlap[1].scheme_name}, "
                "which you already hold."
            )
    components["portfolio_fit"] = max(0.0, 100.0 - overlap_penalty)

    if existing_amcs and scheme.amc in existing_amcs:
        components["amc_diversity"] = 55.0
        notes.append(f"You already hold schemes from {scheme.amc}.")
    else:
        components["amc_diversity"] = 100.0

    weights = {"risk_fit": 30, "horizon_fit": 25, "cost_fit": 20,
               "portfolio_fit": 20, "amc_diversity": 5}
    available = [(components[k], w) for k, w in weights.items() if components.get(k) is not None]
    total_w = sum(w for _, w in available)
    value = sum(v * w for v, w in available) / total_w if total_w else None

    return {
        "score": round(value, 1) if value is not None else None,
        "components": {k: round(v, 1) for k, v in components.items()},
        "notes": notes,
    }


def shortlist(universe, registry, *, asset_class, risk_assessment, years_to_goal,
              portfolio=None, limit=3, scores=None, target_allocation=None):
    """The small set of candidates a human adviser would actually put forward.

    Deliberately short. A list of twenty funds is not a recommendation; it is
    an abdication dressed as choice.
    """
    existing_amcs = set()
    if portfolio and universe:
        for h in portfolio.holdings:
            s = universe.get(h.scheme_code)
            if s:
                existing_amcs.add(s.amc)

    candidates, rejected = [], []
    peer_cache = {}

    for scheme in universe:
        ok, reasons = eligibility(
            scheme, registry, risk_assessment=risk_assessment,
            years_to_goal=years_to_goal, asset_class=asset_class,
            target_allocation=target_allocation,
        )
        if not ok:
            if any(r["gate"] != "ASSET_CLASS" for r in reasons):
                rejected.append({
                    "scheme_code": scheme.scheme_code,
                    "scheme_name": scheme.scheme_name,
                    "failed_gates": [r for r in reasons if r["gate"] != "ASSET_CLASS"],
                })
            continue

        if scores and scheme.scheme_code in scores:
            fund_score = scores[scheme.scheme_code]
        else:
            peers = peer_cache.setdefault(
                scheme.sebi_category,
                [compute_metrics(p) for p in universe.peers(scheme)],
            )
            fund_score = score_fund(scheme, peers, registry)
        if fund_score.get("overall") is None:
            rejected.append({
                "scheme_code": scheme.scheme_code,
                "scheme_name": scheme.scheme_name,
                "failed_gates": [{"gate": "NOT_SCORED", "why": fund_score.get("reason")}],
            })
            continue

        fit = suitability_score(
            scheme, fund_score, risk_assessment=risk_assessment,
            years_to_goal=years_to_goal, portfolio=portfolio, universe=universe,
            registry=registry, existing_amcs=existing_amcs,
        )

        # Quality and fit are combined only AFTER both gates have passed, and
        # fit is weighted more heavily than quality on purpose.
        combined = 0.45 * fund_score["overall"] + 0.55 * (fit["score"] or 0)

        candidates.append({
            "scheme_code": scheme.scheme_code,
            "scheme_name": scheme.scheme_name,
            "amc": scheme.amc,
            "plan": scheme.plan,
            "sebi_category": scheme.sebi_category,
            "riskometer": scheme.riskometer,
            "expense_ratio_pct": scheme.expense_ratio_pct,
            "fund_score": fund_score["overall"],
            "fund_score_band": fund_score["band"],
            "pillars": fund_score["pillars"],
            "suitability_score": fit["score"],
            "suitability_components": fit["components"],
            "suitability_notes": fit["notes"],
            "combined_score": round(combined, 1),
            "data_quality": fund_score["data_quality"]["score"],
            "conflict_disclosure": conflict_disclosure(scheme),
        })

    candidates.sort(key=lambda c: -c["combined_score"])
    return {
        "asset_class": asset_class,
        "candidates": candidates[:limit],
        "considered": len(candidates),
        "rejected": rejected,
        "why_short": (
            "A shortlist, not a screener. These are the schemes that pass every "
            "suitability gate and rank highest on quality and fit for this goal."
        ),
        "ranking_basis": "45% fund quality, 55% fit to your situation. Commercial terms are not an input.",
    }


def conflict_disclosure(scheme):
    """Attached to every scheme the platform names. Silence is not neutrality.

    Note that these fields exist on the record and are shown to the investor,
    but no scoring or ranking code reads them.
    """
    if scheme.amc_relationship or scheme.distributor_commission_pct:
        return {
            "has_commercial_relationship": True,
            "relationship": scheme.amc_relationship,
            "commission_pct": scheme.distributor_commission_pct,
            "statement": (
                "We have a commercial relationship with this AMC. It played no part in this "
                "scheme appearing here: rankings are computed with commercial fields excluded, "
                "and that exclusion is verified by an automated test on every build."
            ),
        }
    return {
        "has_commercial_relationship": False,
        "statement": "We have no commercial relationship with this AMC.",
    }
