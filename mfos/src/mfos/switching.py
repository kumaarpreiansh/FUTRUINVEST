"""Switch Intelligence.

A switch is not a free move between two rows in a table. It is a redemption
(taxable, possibly load-bearing), a gap out of the market, and a reset of the
holding period — paid today, in exchange for an improvement that is expected,
uncertain, and spread over years.

So the engine computes a net figure:

    switching benefit = expected improvement
                      - tax cost
                      - exit load
                      - transaction friction
                      - behavioural and strategic risk

and recommends a switch only when what is left over is material AND the
alternative is genuinely better rather than merely different.

The expected improvement is deliberately conservative. Score differences are
converted to an expected annual advantage using a published, low conversion
factor, because a 10-point score gap does not reliably deliver 10 points of
return, and pretending otherwise is how platforms end up churning portfolios.
"""

from dataclasses import dataclass, field
from decimal import Decimal

from .money import fmt_inr, money, rupees

# How much annual return advantage one point of combined-score difference is
# assumed to be worth. Deliberately small. Cost differences are treated
# separately and at full value because they are certain.
SCORE_POINT_TO_ANNUAL_BPS = 3.0

# Years over which the improvement is credited. Beyond this the assumption
# that today's quality difference persists is not defensible.
BENEFIT_HORIZON_YEARS = 5.0

# Charged against any switch, for the market gap between redemption and
# re-investment plus the reset of the holding period.
FRICTION_BPS = 15.0


@dataclass
class SwitchAnalysis:
    from_scheme: str
    to_scheme: str
    holding_value: Decimal
    expected_improvement: Decimal
    cost_advantage_annual: Decimal
    quality_advantage_annual: Decimal
    tax_cost: Decimal
    exit_load: Decimal
    friction_cost: Decimal
    net_benefit: Decimal
    breakeven_years: float = None
    recommendation: str = "HOLD"
    confidence: float = 0.0
    reasons: list = field(default_factory=list)
    against: list = field(default_factory=list)
    tax_detail: dict = field(default_factory=dict)

    def as_dict(self):
        return {
            "from": self.from_scheme,
            "to": self.to_scheme,
            "holding_value": str(self.holding_value),
            "expected_improvement_over_horizon": str(self.expected_improvement),
            "of_which_cost_advantage_per_year": str(self.cost_advantage_annual),
            "of_which_quality_advantage_per_year": str(self.quality_advantage_annual),
            "tax_cost": str(self.tax_cost),
            "exit_load": str(self.exit_load),
            "friction_cost": str(self.friction_cost),
            "net_benefit": str(self.net_benefit),
            "breakeven_years": self.breakeven_years,
            "recommendation": self.recommendation,
            "confidence_pct": round(self.confidence * 100),
            "why": self.reasons,
            "what_could_make_this_wrong": self.against,
            "tax_detail": self.tax_detail,
            "disclaimer_ids": ["DISC-PROJECTION-001", "DISC-PAST-PERF-001"],
        }


def analyse_switch(*, current_scheme, current_score, candidate_scheme, candidate_score,
                   holding_value, tax_estimate=None, years_to_goal=None,
                   registry=None, signal=None):
    """Price a proposed switch. Returns a full, explainable comparison."""
    value = money(holding_value)
    value_f = float(value)

    # Cost advantage is certain and recurring — the only part of the case that
    # does not depend on a forecast.
    cost_gap_pct = 0.0
    if current_scheme.expense_ratio_pct is not None and candidate_scheme.expense_ratio_pct is not None:
        cost_gap_pct = current_scheme.expense_ratio_pct - candidate_scheme.expense_ratio_pct
    cost_advantage_annual = value_f * cost_gap_pct / 100.0

    # Quality advantage is a forecast, so it is discounted hard.
    score_gap = (candidate_score or 0) - (current_score or 0)
    quality_advantage_annual = value_f * (score_gap * SCORE_POINT_TO_ANNUAL_BPS) / 10000.0

    annual_gain = cost_advantage_annual + quality_advantage_annual
    expected_improvement = annual_gain * BENEFIT_HORIZON_YEARS
    if years_to_goal is not None:
        expected_improvement = annual_gain * min(BENEFIT_HORIZON_YEARS, max(0.0, years_to_goal))

    tax_cost = float(tax_estimate.total_tax) if tax_estimate else 0.0
    exit_load = float(tax_estimate.exit_load) if tax_estimate else 0.0
    friction = value_f * FRICTION_BPS / 10000.0

    net = expected_improvement - tax_cost - exit_load - friction
    breakeven = None
    if annual_gain > 0:
        breakeven = round((tax_cost + exit_load + friction) / annual_gain, 1)

    reasons, against = [], []

    if cost_gap_pct > 0.15:
        reasons.append(
            f"The candidate costs {cost_gap_pct:.2f} percentage points less each year "
            f"(about {fmt_inr(rupees(cost_advantage_annual))} a year on this holding). Cost is the "
            "only advantage that is certain in advance."
        )
    elif cost_gap_pct < -0.15:
        against.append(
            f"The candidate costs {abs(cost_gap_pct):.2f} points MORE per year — "
            f"about {fmt_inr(rupees(abs(cost_advantage_annual)))} a year of certain drag against an "
            "uncertain improvement."
        )

    if score_gap >= 8:
        reasons.append(
            f"The candidate scores {score_gap:.0f} points higher on quality and fit "
            f"({candidate_score:.0f} vs {current_score:.0f})."
        )
    elif score_gap < 8:
        against.append(
            f"The score gap is only {score_gap:.0f} points. That is inside the noise of any "
            "scoring method — it is not evidence that the alternative is better."
        )

    if signal is not None:
        for t in signal.fired:
            reasons.append(t["description"])

    if tax_cost > 0:
        against.append(
            f"Switching realises a gain today: about {fmt_inr(rupees(tax_cost))} in tax that you would "
            "not otherwise pay this year. Your money leaves the market to pay it."
        )
    if exit_load > 0:
        against.append(f"An exit load of about {fmt_inr(rupees(exit_load))} still applies to some units.")
    if breakeven is not None and breakeven > 3:
        against.append(
            f"On these assumptions the switch takes about {breakeven} years just to recover "
            "its own costs."
        )
    against.append(
        "A fund manager change, a reversal in the market regime, or the candidate's recent "
        "record being luck rather than skill would each undermine this case."
    )

    threshold = value_f * 0.01          # 1% of the holding, over the horizon
    if net > threshold and score_gap >= 8:
        recommendation = "SWITCH_REVIEW"
        confidence = min(0.9, 0.5 + min(0.3, net / max(value_f, 1) * 4) + (0.1 if cost_gap_pct > 0.3 else 0))
    elif net > 0 and cost_gap_pct > 0.4:
        recommendation = "SWITCH_REVIEW"
        confidence = 0.62
        reasons.append(
            "This case rests on the cost difference, not on a performance forecast."
        )
    else:
        recommendation = "HOLD"
        confidence = 0.7
        reasons.append(
            "The expected improvement does not cover the cost of making the change. "
            "Staying put is the better decision on these numbers."
        )

    return SwitchAnalysis(
        from_scheme=current_scheme.scheme_code,
        to_scheme=candidate_scheme.scheme_code,
        holding_value=value,
        expected_improvement=rupees(expected_improvement),
        cost_advantage_annual=rupees(cost_advantage_annual),
        quality_advantage_annual=rupees(quality_advantage_annual),
        tax_cost=rupees(tax_cost),
        exit_load=rupees(exit_load),
        friction_cost=rupees(friction),
        net_benefit=rupees(net),
        breakeven_years=breakeven,
        recommendation=recommendation,
        confidence=confidence,
        reasons=reasons,
        against=against,
        tax_detail=tax_estimate.as_dict() if tax_estimate else {},
    )


def compare_side_by_side(current_scheme, current_result, candidate_scheme, candidate_result):
    """The comparison table the investor actually sees before deciding."""
    def row(label, a, b, better="higher"):
        return {"metric": label, "current": a, "candidate": b, "better": better}

    cm = current_result.get("metrics", {})
    km = candidate_result.get("metrics", {})
    return [
        row("Overall score", current_result.get("overall"), candidate_result.get("overall")),
        row("Consistency", current_result["pillars"].get("consistency"),
            candidate_result["pillars"].get("consistency")),
        row("Risk", current_result["pillars"].get("risk"), candidate_result["pillars"].get("risk")),
        row("Cost", current_result["pillars"].get("cost"), candidate_result["pillars"].get("cost")),
        row("Expense ratio %", cm.get("expense_ratio_pct"), km.get("expense_ratio_pct"), "lower"),
        row("Max drawdown", cm.get("max_drawdown"), km.get("max_drawdown"), "lower"),
        row("Sharpe", cm.get("sharpe"), km.get("sharpe")),
        row("Downside capture", cm.get("downside_capture"), km.get("downside_capture"), "lower"),
        row("Rolling 3y win rate", cm.get("rolling_win_3y"), km.get("rolling_win_3y")),
        row("Plan", current_scheme.plan, candidate_scheme.plan, "DIRECT"),
        row("AMC", current_scheme.amc, candidate_scheme.amc, "n/a"),
    ]
