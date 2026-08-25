"""AI Confidence Engine.

Every recommendation carries a confidence figure, and that figure is computed
from things that can be checked — not asserted by a model that has no way to
know how sure it should be.

Six inputs, each of which can only reduce confidence from a cautious base:

  data quality        how good is the underlying scheme data
  data freshness      how old is it
  source independence how many independent sources agree
  evidence balance    supporting signals against conflicting ones
  model limitation    known weaknesses of the method used
  history depth       how much observed history the judgement rests on

Below the platform's minimum, the honest output is not a low-confidence
recommendation. It is no recommendation, and a question.
"""

from dataclasses import dataclass, field

BASE = 78.0          # a well-evidenced, well-sourced recommendation starts here


@dataclass
class Confidence:
    score: float
    band: str
    inputs: list = field(default_factory=list)
    supporting_signals: int = 0
    conflicting_signals: int = 0
    data_freshness_hours: float = None
    independent_sources: int = 0
    limitations: list = field(default_factory=list)
    sufficient: bool = True

    def as_dict(self):
        return {
            "confidence_pct": round(self.score),
            "band": self.band,
            "how_it_was_computed": self.inputs,
            "supporting_signals": self.supporting_signals,
            "conflicting_signals": self.conflicting_signals,
            "data_freshness_hours": (round(self.data_freshness_hours, 1)
                                     if self.data_freshness_hours is not None else None),
            "independent_sources": self.independent_sources,
            "model_limitations": self.limitations,
            "sufficient_to_recommend": self.sufficient,
        }


def _band(score):
    if score >= 80:
        return "HIGH"
    if score >= 65:
        return "MODERATE"
    if score >= 50:
        return "LOW"
    return "INSUFFICIENT"


def assess(registry, *, data_quality=None, freshness_hours=None, independent_sources=1,
           supporting=0, conflicting=0, history_months=None, limitations=None,
           method_limitations=None):
    """Compute confidence. Only ever subtracts from the base."""
    score = BASE
    inputs = []

    if data_quality is not None:
        delta = (data_quality - 85) * 0.35
        delta = min(6.0, delta)
        score += delta
        inputs.append({"factor": "Data quality", "value": data_quality, "effect": round(delta, 1)})

    if freshness_hours is not None:
        limit = registry.threshold("max_data_staleness_hours", "nav")
        if freshness_hours > limit:
            delta = -min(20.0, (freshness_hours - limit) / limit * 15.0)
        else:
            delta = 2.0
        score += delta
        inputs.append({"factor": "Data freshness", "value": f"{freshness_hours:.0f}h",
                       "effect": round(delta, 1)})

    delta = {0: -20.0, 1: -6.0, 2: 2.0}.get(independent_sources, 5.0)
    score += delta
    inputs.append({"factor": "Independent sources", "value": independent_sources,
                   "effect": round(delta, 1)})

    if supporting or conflicting:
        net = supporting - conflicting * 1.5
        delta = max(-22.0, min(10.0, net * 2.5))
        score += delta
        inputs.append({
            "factor": "Evidence balance",
            "value": f"{supporting} supporting, {conflicting} conflicting",
            "effect": round(delta, 1),
        })

    if history_months is not None:
        floor = registry.threshold("min_history_months_for_scoring")
        if history_months < floor:
            delta = -min(18.0, (floor - history_months) * 0.6)
        else:
            delta = min(5.0, (history_months - floor) / 24.0)
        score += delta
        inputs.append({"factor": "History depth", "value": f"{history_months} months",
                       "effect": round(delta, 1)})

    limits = list(limitations or [])
    limits.extend(method_limitations or [])
    if limits:
        delta = -min(12.0, len(limits) * 3.0)
        score += delta
        inputs.append({"factor": "Known method limitations", "value": len(limits),
                       "effect": round(delta, 1)})

    score = max(0.0, min(96.0, score))     # never 100. Nothing here is certain.
    minimum = registry.threshold("min_recommendation_confidence")

    return Confidence(
        score=score,
        band=_band(score),
        inputs=inputs,
        supporting_signals=supporting,
        conflicting_signals=conflicting,
        data_freshness_hours=freshness_hours,
        independent_sources=independent_sources,
        limitations=limits,
        sufficient=score >= minimum,
    )


def needs_human_review(registry, confidence, *, amount=None, goal_type=None, action=None):
    """When a person must look before the investor is asked to decide."""
    reasons = []
    if confidence.score < registry.threshold("human_review_confidence_below"):
        reasons.append(f"Confidence {confidence.score:.0f}% is below the review threshold.")
    if amount is not None and float(amount) >= registry.threshold("human_review_amount_inr"):
        reasons.append("The amount is large enough that a person should check it.")
    if goal_type in ("RETIREMENT", "CHILD_EDUCATION"):
        if action in ("EXIT_REVIEW", "REDUCE", "SWITCH_REVIEW"):
            phrase = action.replace("_", " ").lower()
            article = "An" if phrase[0] in "aeiou" else "A"
            reasons.append(f"{article} {phrase} on a {goal_type.lower().replace('_', ' ')} goal "
                           "is not recoverable if it is wrong.")
    if confidence.conflicting_signals >= 3:
        reasons.append("The evidence is genuinely mixed.")
    return {
        "required": bool(reasons),
        "reasons": reasons,
        "route": ("Escalate to a qualified adviser before presenting this to the investor."
                  if reasons else None),
    }


# Known limitations of each method, attached automatically so they cannot be
# forgotten. Being explicit about what a model cannot do is part of the output.
METHOD_LIMITATIONS = {
    "goal_probability": [
        "Assumes returns are lognormal and independent across years; real markets have fat "
        "tails and momentum.",
        "Uses long-run average assumptions, which no individual decade has ever matched.",
        "Assumes contributions are actually made as scheduled.",
    ],
    "fund_score": [
        "Rests on observed history, which is not evidence about the future.",
        "Percentile components are unreliable in categories with few peers.",
        "Cannot see a manager's process, only its results.",
    ],
    "switch_analysis": [
        "Converts a score difference into an expected return advantage using a fixed, "
        "conservative factor that may be wrong in either direction.",
        "Assumes the current quality difference persists over the benefit horizon.",
    ],
    "tax_estimate": [
        "Surcharge is not modelled, so figures are lower bounds.",
        "Assumes the investor's other capital gains for the year are as recorded.",
    ],
}
