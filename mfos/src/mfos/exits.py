"""Exit Intelligence.

Mutual funds are not positions with stop-losses. A price fall is not an exit
condition; it is the cost of the returns being sought. So this engine has no
price-based exit at all.

There are exactly six reasons to leave a scheme, and each one is a change in
circumstance or in the scheme, never a change in the market:

  GOAL       — the money is needed, or the goal is met and the risk has no purpose
  RISK       — the portfolio's risk no longer suits the investor
  THESIS     — the scheme is no longer the thing that was bought
  ALLOCATION — the asset class no longer belongs in this plan
  QUALITY    — the scheme has deteriorated on the evidence, persistently
  TAX_AWARE  — a planned, tax-efficient withdrawal is due

Anything else is a fund review, not an exit.
"""

from dataclasses import dataclass, field

from .money import fmt_inr

EXIT_TYPES = ("GOAL_EXIT", "RISK_EXIT", "THESIS_EXIT", "ALLOCATION_EXIT",
              "QUALITY_EXIT", "TAX_AWARE_EXIT")


@dataclass
class ExitAssessment:
    scheme_code: str
    goal_id: str
    exit_type: str = None
    triggered: bool = False
    urgency: str = "NONE"          # NONE | LOW | MEDIUM | HIGH
    rationale: list = field(default_factory=list)
    counter_arguments: list = field(default_factory=list)
    recommended_route: str = None
    confidence: float = 0.0

    def as_dict(self):
        return {
            "scheme_code": self.scheme_code,
            "goal_id": self.goal_id,
            "exit_type": self.exit_type,
            "triggered": self.triggered,
            "urgency": self.urgency,
            "why": self.rationale,
            "what_argues_against": self.counter_arguments,
            "recommended_route": self.recommended_route,
            "confidence_pct": round(self.confidence * 100),
            "state": "EXIT_REVIEW" if self.triggered else "HOLD",
            "note": (
                "An exit review is a prompt to decide, with the numbers in front of you. "
                "Nothing is sold without your explicit instruction."
            ),
        }


def assess(ctx, *, scheme_code, goal_id, signal=None, tax_estimate=None):
    """Which of the six exits, if any, applies.

    Evaluated in order of how decisive the reason is. A goal that has arrived
    outranks a scheme that has deteriorated: the money is needed either way.
    """
    result = ExitAssessment(scheme_code=scheme_code, goal_id=goal_id)

    years = ctx.get("years_to_goal")
    projected = ctx.get("projected_corpus")
    target = ctx.get("inflation_adjusted_target")
    need_months = ctx.get("declared_liquidity_need_within_months")

    # 1. GOAL — the money is needed, or the goal is already funded
    if need_months is not None and need_months <= 6:
        result.exit_type = "GOAL_EXIT"
        result.triggered = True
        result.urgency = "HIGH"
        result.confidence = 0.92
        result.rationale.append(
            f"You have told us you need this money within {need_months} months. "
            "Money with a known near-term use does not belong in a market-linked scheme."
        )
        result.recommended_route = (
            "Move to a liquid or ultra-short scheme in stages, oldest units first for "
            "tax purposes, completing before the money is needed."
        )
    elif (projected is not None and target is not None and years is not None
          and projected >= target and years <= 3):
        result.exit_type = "GOAL_EXIT"
        result.triggered = True
        result.urgency = "MEDIUM"
        result.confidence = 0.85
        result.rationale.append(
            "This goal is already funded on current projections and is close. The reason "
            "you were taking this risk has gone — staying invested now risks the goal to "
            "chase a return you no longer need."
        )
        result.recommended_route = "De-risk in tranches along the published glide path."

    # 2. ALLOCATION — the asset class no longer belongs here
    elif years is not None and ctx.get("min_years_for_asset_class") is not None \
            and years < ctx["min_years_for_asset_class"]:
        result.exit_type = "ALLOCATION_EXIT"
        result.triggered = True
        result.urgency = "MEDIUM"
        result.confidence = 0.8
        result.rationale.append(
            f"This goal is {years:.1f} years away, below the "
            f"{ctx['min_years_for_asset_class']}-year minimum for this asset class. "
            "The holding is now a timing bet, not an investment."
        )
        result.recommended_route = "Shift to the stability sleeve over the next two to three months."

    # 3. RISK — the investor's capacity has changed
    elif ctx.get("risk_capacity_band_dropped") and ctx.get("holding_equity_pct", 0) > 0:
        result.exit_type = "RISK_EXIT"
        result.triggered = True
        result.urgency = "MEDIUM"
        result.confidence = 0.75
        result.rationale.append(
            "Your capacity to carry risk has fallen since this was bought "
            f"({ctx.get('previous_capacity_band')} to {ctx.get('capacity_band')}). "
            "The holding did not change; your ability to hold it did."
        )
        result.recommended_route = "Reduce to the new band's ceiling; there is no need to exit entirely."

    # 4. THESIS — the scheme is no longer what was bought
    elif ctx.get("scheme_material_change") or (ctx.get("mandate_drift_score") or 0) > 0.35:
        result.exit_type = "THESIS_EXIT"
        result.triggered = True
        result.urgency = "MEDIUM"
        result.confidence = 0.78
        detail = ctx.get("scheme_material_change_detail") or "the portfolio no longer matches the mandate"
        result.rationale.append(
            f"The scheme has materially changed: {detail}. You bought a specific mandate; "
            "that is not what you now hold."
        )
        result.recommended_route = "Review a replacement in the same category before redeeming."

    # 5. QUALITY — persistent, multi-signal deterioration
    elif signal is not None and signal.state in ("SWITCH_REVIEW", "EXIT_REVIEW") \
            and len(signal.fired) >= 3:
        result.exit_type = "QUALITY_EXIT"
        result.triggered = True
        result.urgency = "LOW"
        result.confidence = 0.65
        result.rationale.extend(t["description"] for t in signal.fired)
        result.rationale.append(
            "These are independent signals observed over multiple evaluations, not one bad period."
        )
        result.recommended_route = (
            "Treat this as a switch review rather than a redemption: stay invested in the "
            "asset class while changing the scheme."
        )

    # 6. TAX_AWARE — a planned withdrawal is due
    elif ctx.get("planned_withdrawal_due"):
        result.exit_type = "TAX_AWARE_EXIT"
        result.triggered = True
        result.urgency = "LOW"
        result.confidence = 0.7
        result.rationale.append(
            "A planned withdrawal is due. Sequencing it across financial years and using "
            "the annual long-term exemption reduces the tax on the same amount of money."
        )
        result.recommended_route = "Redeem in tranches sized to the annual exemption where the timeline allows."

    if not result.triggered:
        result.rationale.append(
            "None of the six exit conditions applies. Nothing about your goal, your "
            "circumstances or this scheme has changed enough to justify selling."
        )
        result.confidence = 0.8
        return result

    # Counter-arguments are mandatory on every triggered exit.
    if tax_estimate is not None:
        result.counter_arguments.append(
            f"Redeeming realises about {fmt_inr(tax_estimate.total_tax)} in tax"
            + (f" plus {fmt_inr(tax_estimate.exit_load)} of exit load"
               if tax_estimate.exit_load else "")
            + ". That money leaves the portfolio permanently."
        )
    if ctx.get("months_since_last_action_on_holding", 999) < 12:
        result.counter_arguments.append(
            "This holding was already acted on within the last year. Repeated changes are "
            "usually a sign the plan is being managed by mood, not evidence."
        )
    result.counter_arguments.append(
        "If the underlying reason is a fall in markets rather than a change in your "
        "circumstances or in the scheme, this is not an exit condition."
    )
    return result


def sequencing_plan(lots, annual_exemption, current_nav):
    """Redeem in an order and size that minimises avoidable tax.

    Two ideas: oldest units first, because they are more likely to qualify for
    long-term treatment; and split across financial years where the timeline
    allows, so more than one annual exemption is available.
    """
    ordered = sorted(lots, key=lambda l: l.purchase_date)
    tranches, current, gain_in_tranche = [], [], 0.0
    for lot in ordered:
        lot_gain = float(lot.units) * (float(current_nav) - float(lot.cost_per_unit))
        if gain_in_tranche + lot_gain > float(annual_exemption) and current:
            tranches.append({"lots": current, "estimated_gain": round(gain_in_tranche, 2)})
            current, gain_in_tranche = [], 0.0
        current.append(lot)
        gain_in_tranche += lot_gain
    if current:
        tranches.append({"lots": current, "estimated_gain": round(gain_in_tranche, 2)})
    return {
        "tranches": len(tranches),
        "detail": [
            {"tranche": i + 1, "units": str(sum(l.units for l in t["lots"])),
             "estimated_gain": t["estimated_gain"],
             "note": "Sized to sit within one year's long-term exemption where possible."}
            for i, t in enumerate(tranches)
        ],
        "caveat": (
            "Splitting across financial years only helps if the money is not needed sooner. "
            "Never delay a redemption you actually need in order to save tax."
        ),
    }
