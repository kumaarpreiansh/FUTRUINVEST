"""Financial Health Score.

A 0-100 score over ten dimensions, each scored independently and each paired
with a concrete action. The score's job is not to grade the investor; it is to
show which single thing to fix next.

Deliberate design choice: the score is capped when a foundation is missing. An
investor with no emergency fund and no health cover cannot have a "good"
financial health score no matter how well their equity portfolio has done,
because the portfolio is what will be liquidated when something goes wrong.
"""

from dataclasses import dataclass, field


def _band(value, breakpoints):
    """breakpoints: [(threshold, score)] descending by threshold."""
    if value is None:
        return None
    for threshold, score in breakpoints:
        if value >= threshold:
            return score
    return 0


@dataclass
class Dimension:
    key: str
    label: str
    score: float
    weight: float
    finding: str
    action: str
    available: bool = True

    def as_dict(self):
        return {
            "key": self.key, "label": self.label,
            "score": (round(self.score) if self.available else None),
            "weight": self.weight, "finding": self.finding, "action": self.action,
        }


def _emergency_fund(p):
    months = p.emergency_fund_months
    if months is None:
        return Dimension("emergency_fund", "Emergency fund", 0, 15,
                         "Not known yet.", "Tell me what you hold in cash or liquid funds.", False)
    score = _band(months, [(6, 100), (4, 82), (3, 68), (2, 50), (1, 30), (0, 10)])
    if months >= 6:
        finding = f"{months:.1f} months of expenses set aside. This is the layer that stops a bad month from becoming a bad decade."
        action = "Nothing needed. Keep it in a liquid or ultra-short scheme, not in equity."
    else:
        gap = (6 - months) * float(p.monthly_expenses)
        finding = f"{months:.1f} months of expenses set aside; six is the working target."
        action = f"Build roughly ₹{gap:,.0f} more before increasing equity contributions."
    return Dimension("emergency_fund", "Emergency fund", score, 15, finding, action)


def _savings_rate(p):
    rate = p.savings_rate_pct
    if rate is None:
        return Dimension("savings_rate", "Savings rate", 0, 15, "Not known yet.",
                         "Tell me your monthly income and expenses.", False)
    score = _band(rate, [(30, 100), (25, 90), (20, 78), (15, 62), (10, 45), (5, 25), (0, 8)])
    finding = f"You save about {rate:.0f}% of income after EMIs."
    action = ("Hold this rate and let step-ups do the rest."
              if rate >= 20 else
              "Raising the savings rate moves your goals more than any fund choice will.")
    return Dimension("savings_rate", "Savings rate", score, 15, finding, action)


def _debt(p):
    ratio = p.emi_to_income_pct
    if ratio is None:
        return Dimension("debt", "Debt burden", 0, 12, "Not known yet.",
                         "Tell me your total monthly EMI.", False)
    score = _band(-ratio, [(-10, 100), (-20, 88), (-30, 72), (-40, 50), (-50, 28), (-100, 8)])
    finding = f"EMIs take about {ratio:.0f}% of your income."
    if ratio > 40:
        action = "Reduce high-cost debt before adding market risk; guaranteed interest saved beats uncertain return earned."
    elif ratio > 25:
        action = "Manageable, but avoid taking on further EMIs while goals are underfunded."
    else:
        action = "Comfortable. No action."
    return Dimension("debt", "Debt burden", score, 12, finding, action)


def _insurance(p):
    if p.life_insurance_cover is None and p.health_insurance_cover is None:
        return Dimension("insurance", "Insurance adequacy", 0, 12, "Not known yet.",
                         "Tell me your life and health cover.", False)
    annual_income = float(p.monthly_income or 0) * 12
    life = float(p.life_insurance_cover or 0)
    health = float(p.health_insurance_cover or 0)
    needs_life = (p.dependants or 0) > 0
    life_multiple = (life / annual_income) if annual_income else 0

    parts, actions = [], []
    life_score = 100
    if needs_life:
        life_score = _band(life_multiple, [(10, 100), (7, 80), (5, 60), (3, 35), (0, 10)])
        parts.append(f"life cover is {life_multiple:.1f}x annual income")
        if life_multiple < 10:
            actions.append("Term cover of roughly 10x annual income is the usual starting point where dependants exist.")
    health_score = _band(health, [(1000000, 100), (500000, 78), (300000, 55), (100000, 30), (0, 5)])
    parts.append(f"health cover is ₹{health:,.0f}")
    if health < 500000:
        actions.append("A medical event with thin cover is the most common reason long-term goals get liquidated.")

    score = (life_score + health_score) / 2 if needs_life else health_score
    return Dimension("insurance", "Insurance adequacy", score, 12,
                     "Currently " + " and ".join(parts) + ".",
                     " ".join(actions) or "Cover looks adequate for your situation.")


def _investment_rate(p):
    if p.monthly_income is None or p.existing_sips is None:
        return Dimension("investment_rate", "Investment rate", 0, 10, "Not known yet.",
                         "Tell me what you currently invest each month.", False)
    rate = float(p.existing_sips) / float(p.monthly_income) * 100 if p.monthly_income else 0
    score = _band(rate, [(25, 100), (20, 90), (15, 76), (10, 58), (5, 35), (0, 10)])
    surplus = p.monthly_surplus
    idle = float(surplus or 0) - float(p.existing_sips)
    finding = f"You invest about {rate:.0f}% of income each month."
    action = (f"About ₹{idle:,.0f} of monthly surplus is not yet committed to a goal."
              if idle > 5000 else "Your surplus is largely committed. Consider a step-up rather than a new scheme.")
    return Dimension("investment_rate", "Investment rate", score, 10, finding, action)


def _diversification(p, portfolio_stats=None):
    if not portfolio_stats:
        return Dimension("diversification", "Diversification", 0, 10, "No portfolio loaded yet.",
                         "Import or add your existing schemes.", False)
    overlap = portfolio_stats.get("max_pairwise_overlap_pct")
    amc = portfolio_stats.get("max_amc_pct")
    funds = portfolio_stats.get("fund_count", 0)
    score = 100.0
    findings, actions = [], []
    if overlap is not None and overlap > 40:
        score -= min(35, (overlap - 40) * 1.5)
        findings.append(f"two of your funds overlap {overlap:.0f}%")
        actions.append("Overlapping funds add cost and admin without adding diversification.")
    if amc is not None and amc > 35:
        score -= min(20, (amc - 35))
        findings.append(f"{amc:.0f}% sits with one AMC")
    if funds > 10:
        score -= min(20, (funds - 10) * 4)
        findings.append(f"you hold {funds} schemes")
        actions.append("Beyond about eight schemes you are buying the market at a higher fee.")
    elif funds and funds < 2:
        score -= 15
        findings.append("a single scheme carries all the risk")
    return Dimension("diversification", "Diversification", max(0.0, score), 10,
                     ("Portfolio looks reasonably spread." if not findings
                      else "Currently " + ", ".join(findings) + "."),
                     " ".join(actions) or "No action needed.")


def _liquidity(p):
    if p.current_savings is None or p.monthly_expenses is None:
        return Dimension("liquidity", "Liquidity", 0, 8, "Not known yet.",
                         "Tell me your accessible savings.", False)
    liquid = float(p.current_savings) + float(p.emergency_fund or 0)
    months = liquid / float(p.monthly_expenses) if p.monthly_expenses else 0
    score = _band(months, [(9, 100), (6, 85), (4, 65), (2, 40), (0, 15)])
    need = float(p.liquidity_needs_next_12m or 0)
    finding = f"About {months:.1f} months of expenses are accessible without selling investments."
    action = ("Known needs in the next year should be moved out of market-linked schemes now."
              if need > liquid else "Adequate for known needs.")
    return Dimension("liquidity", "Liquidity", score, 8, finding, action)


def _goal_preparedness(goal_projections):
    if not goal_projections:
        return Dimension("goal_preparedness", "Goal preparedness", 0, 10, "No goals set yet.",
                         "Add at least one goal — it is what everything else is measured against.", False)
    probs = [p.probability for p in goal_projections]
    avg = sum(probs) / len(probs)
    weakest = min(goal_projections, key=lambda p: p.probability)
    return Dimension("goal_preparedness", "Goal preparedness", avg * 100, 10,
                     f"Average goal probability across {len(probs)} goals is {avg * 100:.0f}%.",
                     f"The weakest is currently at {weakest.probability * 100:.0f}%. "
                     "Contribution level, not fund choice, is usually the lever.")


def _portfolio_risk(risk_assessment=None):
    if not risk_assessment:
        return Dimension("portfolio_risk", "Risk alignment", 0, 5, "Risk profile not complete.",
                         "Finish the risk questions.", False)
    if risk_assessment.get("conflict"):
        return Dimension("portfolio_risk", "Risk alignment", 55, 5,
                         risk_assessment["conflict_summary"],
                         "The binding constraint is your capacity, not your appetite.")
    return Dimension("portfolio_risk", "Risk alignment", 90, 5,
                     "Your appetite, capacity and requirement are broadly consistent.",
                     "No action needed.")


def _tax_efficiency(p, portfolio_stats=None):
    if p.marginal_tax_rate_pct is None:
        return Dimension("tax_efficiency", "Tax efficiency", 0, 3, "Tax rate not known yet.",
                         "Tell me your marginal rate so tax figures can be computed rather than skipped.", False)
    score = 70.0
    notes = []
    regular_pct = (portfolio_stats or {}).get("regular_plan_pct")
    if regular_pct is not None:
        if regular_pct > 50:
            score -= 25
            notes.append(f"{regular_pct:.0f}% of your holdings are in Regular plans")
        else:
            score += 15
    return Dimension("tax_efficiency", "Tax efficiency", max(0.0, min(100.0, score)), 3,
                     "; ".join(notes) or "No obvious tax leakage detected.",
                     "Direct plans hold the same portfolio at a lower expense ratio."
                     if (regular_pct or 0) > 50 else "No action needed.")


def score_financial_health(profile, *, goal_projections=None, portfolio_stats=None,
                           risk_assessment=None):
    """The 0-100 score, its breakdown, and the one thing to do next."""
    dims = [
        _emergency_fund(profile),
        _savings_rate(profile),
        _debt(profile),
        _insurance(profile),
        _investment_rate(profile),
        _diversification(profile, portfolio_stats),
        _liquidity(profile),
        _goal_preparedness(goal_projections),
        _portfolio_risk(risk_assessment),
        _tax_efficiency(profile, portfolio_stats),
    ]

    scored = [d for d in dims if d.available]
    if not scored:
        return None
    total_weight = sum(d.weight for d in scored)
    raw = sum(d.score * d.weight for d in scored) / total_weight

    # Foundation cap: no amount of investing performance offsets a missing base.
    caps = []
    ef = next((d for d in dims if d.key == "emergency_fund"), None)
    ins = next((d for d in dims if d.key == "insurance"), None)
    if ef and ef.available and ef.score < 50:
        caps.append(("Emergency fund below three months", 70))
    if ins and ins.available and ins.score < 40:
        caps.append(("Insurance cover materially thin", 75))
    capped = min([raw] + [c[1] for c in caps])

    weakest = min((d for d in scored if d.weight >= 8), key=lambda d: d.score, default=scored[0])

    return {
        "score": round(capped),
        "uncapped_score": round(raw),
        "caps_applied": [{"reason": r, "cap": c} for r, c in caps],
        "coverage_pct": round(total_weight / sum(d.weight for d in dims) * 100),
        "breakdown": [d.as_dict() for d in dims],
        "next_action": {
            "dimension": weakest.label,
            "why": weakest.finding,
            "do": weakest.action,
        },
        "unknowns": [d.key for d in dims if not d.available],
        "band": (
            "Strong" if capped >= 80 else
            "Solid" if capped >= 65 else
            "Needs work" if capped >= 50 else
            "Fragile"
        ),
    }
