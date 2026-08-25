"""Risk Engine.

Three different questions, routinely collapsed into one by the industry:

  CAPACITY    — how much loss can this investor absorb without their life
                changing? Financial fact. Derived, not asked.
  TOLERANCE   — how much volatility can they live through without selling?
                Psychological. Asked, and cross-checked against what they
                actually did last time markets fell.
  REQUIREMENT — how much risk does the plan need? Arithmetic, from the goal.

They frequently disagree, and the disagreement is the useful output. A single
"Aggressive" label destroys that information, so this engine never emits one.

The binding band is min(capacity, tolerance). Requirement never raises it:
if the plan needs more return than capacity permits, the plan changes, not
the risk. That is the whole point of separating the three.
"""

from dataclasses import dataclass, field

BANDS = ("CONSERVATIVE", "CAUTIOUS", "MODERATE", "GROWTH", "AGGRESSIVE")
BAND_INDEX = {b: i for i, b in enumerate(BANDS)}

# Maximum equity exposure permitted by each band. Policy, and the hard ceiling
# the portfolio engine may not exceed.
BAND_MAX_EQUITY_PCT = {
    "CONSERVATIVE": 20,
    "CAUTIOUS": 40,
    "MODERATE": 60,
    "GROWTH": 80,
    "AGGRESSIVE": 90,
}

# Risk-o-meter ceiling per band — used for schemes an investor asks about
# OUTSIDE their plan, and as a suitability score input.
#
# It is deliberately NOT the primary risk control. In the Indian market almost
# every equity scheme, large-cap funds included, is labelled Very High, so the
# risk-o-meter does not discriminate within equity. Treating it as a hard gate
# would make equity uninvestable for every band below Aggressive, which is
# absurd. The control that actually binds is BAND_MAX_EQUITY_PCT applied to
# the portfolio, plus the horizon rules in selection.py.
BAND_MAX_RISKOMETER = {
    "CONSERVATIVE": "MODERATE",
    "CAUTIOUS": "MODERATELY_HIGH",
    "MODERATE": "HIGH",
    "GROWTH": "VERY_HIGH",
    "AGGRESSIVE": "VERY_HIGH",
}

RISKOMETER_ORDER = ("LOW", "LOW_TO_MODERATE", "MODERATE", "MODERATELY_HIGH", "HIGH", "VERY_HIGH")


def _band_from_score(score):
    if score >= 80:
        return "AGGRESSIVE"
    if score >= 62:
        return "GROWTH"
    if score >= 44:
        return "MODERATE"
    if score >= 26:
        return "CAUTIOUS"
    return "CONSERVATIVE"


# ── capacity ─────────────────────────────────────────────────────────────────

def assess_capacity(profile, horizon_years=None):
    """Derived from facts, never from a question about feelings."""
    horizon = horizon_years if horizon_years is not None else (profile.primary_horizon_years or 0)
    factors = []
    score = 50.0

    months = profile.emergency_fund_months
    if months is not None:
        delta = min(18.0, (months - 3) * 4.0) if months >= 3 else max(-25.0, (months - 3) * 8.0)
        score += delta
        factors.append({
            "factor": "Emergency fund",
            "value": f"{months:.1f} months",
            "effect": round(delta, 1),
            "why": "A buffer is what lets you leave investments alone during a fall.",
        })

    emi = profile.emi_to_income_pct
    if emi is not None:
        delta = 8.0 if emi < 15 else (0.0 if emi < 30 else -min(25.0, (emi - 30) * 1.2 + 8))
        score += delta
        factors.append({"factor": "Debt servicing", "value": f"{emi:.0f}% of income",
                        "effect": round(delta, 1),
                        "why": "Fixed obligations must be met whatever markets do."})

    if profile.dependants is not None:
        delta = -min(15.0, profile.dependants * 5.0)
        score += delta
        factors.append({"factor": "Dependants", "value": str(profile.dependants),
                        "effect": round(delta, 1),
                        "why": "More people relying on this money lowers how much of it can be at risk."})

    if profile.income_stability:
        delta = {"VOLATILE": -18.0, "VARIABLE": -8.0, "STABLE": 6.0, "VERY_STABLE": 12.0}.get(
            profile.income_stability, 0.0)
        score += delta
        factors.append({"factor": "Income stability", "value": profile.income_stability,
                        "effect": round(delta, 1),
                        "why": "Volatile income and volatile assets compound each other."})

    rate = profile.savings_rate_pct
    if rate is not None:
        delta = min(12.0, (rate - 15) * 0.6) if rate >= 15 else max(-15.0, (rate - 15) * 1.0)
        score += delta
        factors.append({"factor": "Savings rate", "value": f"{rate:.0f}%",
                        "effect": round(delta, 1),
                        "why": "A high savings rate can rebuild a loss; a low one cannot."})

    delta = min(20.0, max(-25.0, (horizon - 7) * 2.5))
    score += delta
    factors.append({"factor": "Horizon", "value": f"{horizon:.0f} years", "effect": round(delta, 1),
                    "why": "Time is the only thing that reliably converts volatility into return."})

    score = max(0.0, min(100.0, score))
    return {
        "dimension": "CAPACITY",
        "score": round(score, 1),
        "band": _band_from_score(score),
        "factors": factors,
        "basis": "Derived from financial facts in the profile, not from a questionnaire.",
    }


# ── tolerance ────────────────────────────────────────────────────────────────

TOLERANCE_QUESTIONS = {
    "drop_reaction": {
        "prompt": "Your portfolio falls 25% over three months. What do you actually do?",
        "options": {"SELL_ALL": 0, "SELL_SOME": 20, "HOLD": 65, "BUY_MORE": 90},
    },
    "acceptable_annual_loss": {
        "prompt": "What one-year fall could you live through without changing your plan?",
        "options": {"0": 0, "5": 20, "10": 40, "20": 65, "30": 85, "40+": 100},
    },
    "experience": {
        "prompt": "How long have you invested in market-linked products?",
        "options": {"NONE": 15, "UNDER_2Y": 35, "2_5Y": 55, "5_10Y": 75, "OVER_10Y": 90},
    },
    "volatility_preference": {
        "prompt": "Which would you rather own for ten years?",
        "options": {"STEADY_6PCT": 15, "MIXED_9PCT": 50, "VOLATILE_12PCT": 85},
    },
}

# What someone did last time beats what they say they will do next time.
BEHAVIOUR_ADJUSTMENT = {
    "SOLD_IN_PANIC": -25,
    "STOPPED_SIP": -15,
    "HELD": +5,
    "BOUGHT_MORE": +12,
    "NEVER_EXPERIENCED": -5,
}


def assess_tolerance(profile):
    answers = profile.risk_tolerance_answers or {}
    if not answers:
        return {
            "dimension": "TOLERANCE",
            "score": None,
            "band": None,
            "factors": [],
            "basis": "Not yet assessed.",
            "unknown": True,
        }
    scores, factors = [], []
    for key, answer in answers.items():
        spec = TOLERANCE_QUESTIONS.get(key)
        if not spec or answer not in spec["options"]:
            continue
        value = spec["options"][answer]
        scores.append(value)
        factors.append({"factor": key, "value": answer, "effect": value, "why": spec["prompt"]})
    if not scores:
        return {"dimension": "TOLERANCE", "score": None, "band": None, "factors": [],
                "basis": "Answers not recognised.", "unknown": True}

    score = sum(scores) / len(scores)
    stated = score

    if profile.past_market_behaviour:
        adj = BEHAVIOUR_ADJUSTMENT.get(profile.past_market_behaviour, 0)
        score += adj
        factors.append({
            "factor": "What you did last time", "value": profile.past_market_behaviour,
            "effect": adj,
            "why": "Observed behaviour in a real fall is a better predictor than a stated preference.",
        })

    score = max(0.0, min(100.0, score))
    return {
        "dimension": "TOLERANCE",
        "score": round(score, 1),
        "stated_score": round(stated, 1),
        "band": _band_from_score(score),
        "factors": factors,
        "basis": "Stated preferences, adjusted for observed behaviour.",
    }


# ── requirement ──────────────────────────────────────────────────────────────

def assess_requirement(required_return_pct, registry, horizon_years):
    """How much risk the plan needs — read off the required return.

    Mapped against the published capital market assumptions so the answer is
    'this return implies roughly this much equity', not a vibe.
    """
    if required_return_pct is None:
        return {"dimension": "REQUIREMENT", "score": None, "band": None,
                "basis": "Goal not yet defined.", "unknown": True}

    cma = registry.allocation["capital_market_assumptions"]["classes"]
    debt = float(cma["DEBT_MEDIUM"]["base"])
    equity = float(cma["EQUITY_FLEXI"]["base"])

    # "Unattainable" is judged against the best-returning asset class in the
    # published assumptions, not against a diversified portfolio — otherwise a
    # goal gets branded impossible while a real allocation could still reach it.
    best_available = max(float(spec["base"]) for name, spec in cma.items()
                         if name.startswith("EQUITY"))

    if required_return_pct <= debt:
        implied_equity = 0.0
    elif required_return_pct >= equity:
        implied_equity = 100.0
    else:
        implied_equity = (required_return_pct - debt) / (equity - debt) * 100.0

    band = "CONSERVATIVE"
    for b in BANDS:
        if implied_equity <= BAND_MAX_EQUITY_PCT[b]:
            band = b
            break
    else:
        band = "AGGRESSIVE"

    unattainable = required_return_pct > best_available
    return {
        "dimension": "REQUIREMENT",
        "score": round(min(100.0, implied_equity), 1),
        "band": band,
        "required_return_pct": round(required_return_pct, 2),
        "implied_equity_pct": round(implied_equity, 1),
        "unattainable": unattainable,
        "basis": (
            f"Reaching this goal on the current contributions needs about "
            f"{required_return_pct:.1f}% a year, which implies roughly "
            f"{implied_equity:.0f}% equity under our published assumptions."
        ),
        "horizon_years": horizon_years,
    }


# ── reconciliation ───────────────────────────────────────────────────────────

@dataclass
class RiskAssessment:
    capacity: dict
    tolerance: dict
    requirement: dict
    binding_band: str
    binding_reason: str
    conflicts: list = field(default_factory=list)
    max_equity_pct: int = 0
    max_riskometer: str = "MODERATE"

    @property
    def conflict(self):
        return bool(self.conflicts)

    @property
    def conflict_summary(self):
        return " ".join(c["explanation"] for c in self.conflicts) or "No conflicts."

    def as_dict(self):
        return {
            "capacity": self.capacity,
            "tolerance": self.tolerance,
            "requirement": self.requirement,
            "binding_band": self.binding_band,
            "binding_reason": self.binding_reason,
            "max_equity_pct": self.max_equity_pct,
            "max_riskometer": self.max_riskometer,
            "conflicts": self.conflicts,
            "single_label_refused": (
                "This platform does not reduce you to one risk label, because the "
                "three measures below genuinely differ and the difference is the "
                "useful part."
            ),
        }


def reconcile(capacity, tolerance, requirement):
    """Produce the binding band and name every conflict explicitly."""
    conflicts = []
    cap_band = capacity.get("band")
    tol_band = tolerance.get("band")
    req_band = requirement.get("band")

    candidates = [b for b in (cap_band, tol_band) if b]
    if not candidates:
        binding = "CONSERVATIVE"
        reason = "Neither capacity nor tolerance is assessed yet, so the most cautious band applies."
    else:
        binding = min(candidates, key=lambda b: BAND_INDEX[b])
        reason = (
            "Capacity is the binding constraint." if binding == cap_band and cap_band != tol_band
            else "Tolerance is the binding constraint." if binding == tol_band and cap_band != tol_band
            else "Capacity and tolerance agree."
        )

    if cap_band and tol_band and BAND_INDEX[tol_band] > BAND_INDEX[cap_band] + 1:
        conflicts.append({
            "type": "TOLERANCE_EXCEEDS_CAPACITY",
            "severity": "HIGH",
            "explanation": (
                f"You are comfortable with {tol_band.lower()} levels of volatility, but your "
                f"financial position currently supports {cap_band.lower()}. Comfort does not pay "
                "an EMI during a drawdown, so capacity binds."
            ),
            "resolution": "Build the buffer and reduce fixed obligations; capacity will rise and the ceiling with it.",
        })

    if cap_band and tol_band and BAND_INDEX[cap_band] > BAND_INDEX[tol_band] + 1:
        conflicts.append({
            "type": "CAPACITY_EXCEEDS_TOLERANCE",
            "severity": "MEDIUM",
            "explanation": (
                f"You could financially afford {cap_band.lower()} risk, but you would not be "
                f"comfortable holding it. A portfolio you abandon in a fall is worse than a "
                "smaller one you keep."
            ),
            "resolution": "Start at the tolerance band and revisit after you have lived through a real drawdown.",
        })

    if req_band and BAND_INDEX[req_band] > BAND_INDEX[binding]:
        conflicts.append({
            "type": "REQUIREMENT_EXCEEDS_CAPACITY",
            "severity": "HIGH",
            "explanation": (
                f"This goal needs roughly {requirement.get('required_return_pct')}% a year, which "
                f"implies {req_band.lower()} risk — more than the {binding.lower()} band you can "
                "currently carry. Taking the extra risk anyway does not make the goal more likely; "
                "it makes the outcome wider in both directions."
            ),
            "resolution": (
                "Change the plan, not the risk: raise the contribution, extend the date, "
                "or reduce the target. The engine will not lift the equity ceiling to close a gap."
            ),
        })

    if requirement.get("unattainable"):
        conflicts.append({
            "type": "GOAL_UNATTAINABLE_AT_ANY_RISK",
            "severity": "CRITICAL",
            "explanation": (
                "The return this goal needs is above what any allocation in our assumptions "
                "supports. No fund choice fixes this."
            ),
            "resolution": "The contribution, the target or the date has to move.",
        })

    if req_band and BAND_INDEX[req_band] < BAND_INDEX[binding] - 1:
        conflicts.append({
            "type": "RISK_NOT_REQUIRED",
            "severity": "LOW",
            "explanation": (
                "Your goal does not need the level of risk you can carry. Taking risk you don't "
                "need is not free — it widens the range of outcomes on a goal you were going to reach."
            ),
            "resolution": "Consider the lower band for this goal, or bring a further goal forward.",
        })

    return RiskAssessment(
        capacity=capacity,
        tolerance=tolerance,
        requirement=requirement,
        binding_band=binding,
        binding_reason=reason,
        conflicts=conflicts,
        max_equity_pct=BAND_MAX_EQUITY_PCT[binding],
        max_riskometer=BAND_MAX_RISKOMETER[binding],
    )


def assess(profile, registry, required_return_pct=None, horizon_years=None):
    horizon = horizon_years if horizon_years is not None else (profile.primary_horizon_years or 0)
    return reconcile(
        assess_capacity(profile, horizon),
        assess_tolerance(profile),
        assess_requirement(required_return_pct, registry, horizon),
    )


def riskometer_permitted(investor_band, scheme_riskometer):
    """Suitability gate used by selection and by the signal engine."""
    if scheme_riskometer not in RISKOMETER_ORDER:
        return False
    ceiling = BAND_MAX_RISKOMETER[investor_band]
    return RISKOMETER_ORDER.index(scheme_riskometer) <= RISKOMETER_ORDER.index(ceiling)
