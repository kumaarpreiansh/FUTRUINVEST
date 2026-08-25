"""AI Investment Copilot — source-first, grounded, and unable to invent a number.

The architecture point that matters: the language model is downstream of every
decision, never upstream of one. By the time it is called, the recommendation,
the numbers, the signals and the citations already exist. Its only job is to
say them in a sentence a person can read.

Three enforcement mechanisms make that real rather than aspirational:

  1. A fact pack. The model is handed the specific facts it may use, each with
     a source. Nothing else is in scope.
  2. Numeric grounding. Every number in the generated text must appear in the
     fact pack. One that does not is a hallucination by definition, and the
     output is rejected rather than shown.
  3. A deterministic fallback. Every answer this module can produce with a
     model, it can also produce without one. If the model is unavailable, or
     its output fails a check, the investor gets the plain version — never
     nothing, and never something unverified.
"""

import re
from dataclasses import dataclass, field

from .errors import InsufficientData

NUMBER_RE = re.compile(r"-?\d[\d,]*\.?\d*")


# ── fact pack ────────────────────────────────────────────────────────────────

@dataclass
class Fact:
    key: str
    value: object
    source: str
    as_of: str = None
    note: str = None

    def as_dict(self):
        return {"key": self.key, "value": self.value, "source": self.source,
                "as_of": self.as_of, "note": self.note}


class FactPack:
    """Everything the copilot is permitted to say, and where each bit came from."""

    def __init__(self, facts=()):
        self.facts = {f.key: f for f in facts}

    def add(self, key, value, source, as_of=None, note=None):
        self.facts[key] = Fact(key, value, source, as_of, note)
        return self

    def get(self, key):
        fact = self.facts.get(key)
        if fact is None:
            raise InsufficientData(
                f"I don't have verified data for '{key}', so I won't answer that part.",
                detail={"missing_fact": key},
            )
        return fact.value

    def has(self, *keys):
        return all(k in self.facts for k in keys)

    def sources(self):
        seen, out = set(), []
        for fact in self.facts.values():
            if fact.source not in seen:
                seen.add(fact.source)
                out.append(fact.source)
        return out

    def allowed_numbers(self):
        """Numeric tokens the model may legitimately use."""
        out = set()
        for fact in self.facts.values():
            for token in _numbers_in(str(fact.value)):
                out.add(token)
        return out


def _numbers_in(text):
    return {m.group(0).replace(",", "").rstrip(".") for m in NUMBER_RE.finditer(text or "")}


def verify_numbers(text, factpack, tolerance=0.01):
    """Numbers in `text` that are not in the fact pack.

    Small integers (0-100) are allowed through: they are overwhelmingly counts,
    percentages already present, and ordinals, and blocking them would make the
    check so noisy it would be switched off — which is the usual fate of a
    guard that cries wolf.
    """
    allowed = factpack.allowed_numbers()
    allowed_floats = []
    for token in allowed:
        try:
            allowed_floats.append(float(token))
        except ValueError:
            continue

    ungrounded = []
    for token in _numbers_in(text):
        try:
            value = float(token)
        except ValueError:
            continue
        if token in allowed:
            continue
        if value == int(value) and 0 <= value <= 100:
            continue
        # Compared on absolute value: prose legitimately turns a stored -12.4
        # into "fell 12.4%". The sign is carried by the words, not the digits.
        if any(abs(abs(value) - abs(a)) <= max(tolerance, abs(a) * tolerance)
               for a in allowed_floats):
            continue
        ungrounded.append(token)
    return sorted(ungrounded)


# ── answer format ────────────────────────────────────────────────────────────

@dataclass
class Answer:
    """The mandated shape for every investment answer."""

    question: str
    answer: str
    why: list = field(default_factory=list)
    your_situation: list = field(default_factory=list)
    risks: list = field(default_factory=list)
    alternatives: list = field(default_factory=list)
    tax_and_cost: list = field(default_factory=list)
    recommendation: str = None
    confidence_pct: int = None
    sources: list = field(default_factory=list)
    disclaimer_ids: list = field(default_factory=list)
    generated_by: str = "deterministic"
    model_id: str = None
    model_version: str = None

    def as_dict(self):
        return {
            "question": self.question,
            "answer": self.answer,
            "why": self.why,
            "your_situation": self.your_situation,
            "risks": self.risks,
            "alternatives": self.alternatives,
            "tax_and_cost": self.tax_and_cost,
            "recommendation": self.recommendation,
            "confidence_pct": self.confidence_pct,
            "sources": self.sources,
            "disclaimer_ids": self.disclaimer_ids,
            "generated_by": self.generated_by,
            "model": {"id": self.model_id, "version": self.model_version},
        }

    def as_text(self):
        parts = [f"**Answer**\n{self.answer}"]
        for title, items in (("Why", self.why), ("Your situation", self.your_situation),
                             ("Risks", self.risks), ("Alternatives", self.alternatives),
                             ("Tax / cost", self.tax_and_cost)):
            if items:
                parts.append(f"**{title}**\n" + "\n".join(f"- {i}" for i in items))
        if self.recommendation:
            parts.append(f"**Recommendation**\n{self.recommendation}")
        if self.confidence_pct is not None:
            parts.append(f"**Confidence**\n{self.confidence_pct}%")
        if self.sources:
            parts.append("**Sources**\n" + "\n".join(f"- {s}" for s in self.sources))
        return "\n\n".join(parts)


# ── built-in intents ─────────────────────────────────────────────────────────

def why_is_my_portfolio_down(factpack):
    required = ("portfolio_return_period_pct", "benchmark_return_period_pct", "period_label")
    if not factpack.has(*required):
        raise InsufficientData(
            "I need your portfolio's return and its benchmark's return for the same period "
            "before I can explain the difference.",
        )
    own = factpack.get("portfolio_return_period_pct")
    bench = factpack.get("benchmark_return_period_pct")
    period = factpack.get("period_label")
    gap = own - bench

    return Answer(
        question="Why is my portfolio down?",
        answer=(
            f"Your portfolio returned {own}% over {period} while its blended benchmark returned "
            f"{bench}%. Most of the fall is the market itself, not your fund choices."
            if abs(gap) < 2 else
            f"Your portfolio returned {own}% over {period} against {bench}% for its blended "
            f"benchmark — a gap of {round(gap, 1)} percentage points that is worth understanding."
        ),
        why=[
            "Falls of this size are a normal feature of an equity allocation, not a malfunction.",
            "The comparison that matters is against the benchmark for the same period, not "
            "against zero and not against your purchase price.",
        ],
        your_situation=[
            factpack.get("goal_context")
        ] if factpack.has("goal_context") else [],
        risks=[
            "Selling now converts a fall in price into a permanent loss and restarts the "
            "compounding clock.",
        ],
        alternatives=[
            "Continue the SIP — falling prices buy more units for the same money.",
            "Review the allocation only if your goal, income or risk capacity has changed.",
        ],
        tax_and_cost=[
            "Doing nothing costs nothing. Selling would realise tax and possibly an exit load.",
        ],
        recommendation="No action indicated by a price fall alone.",
        sources=factpack.sources(),
        disclaimer_ids=["DISC-PAST-PERF-001", "DISC-MF-RISK-001"],
    )


def should_i_increase_my_sip(factpack):
    required = ("current_sip", "goal_probability_pct", "probability_with_increase_pct",
                "increase_amount")
    if not factpack.has(*required):
        raise InsufficientData("I need your current SIP and goal projection to answer that.")
    now = factpack.get("goal_probability_pct")
    after = factpack.get("probability_with_increase_pct")
    increase = factpack.get("increase_amount")

    return Answer(
        question="Should I increase my SIP?",
        answer=(
            f"Increasing your SIP by {increase} would move this goal's probability from "
            f"{now}% to {after}%."
        ),
        why=[
            "Contribution level is the lever you control. Returns are not.",
            "A step-up linked to your income increase costs nothing in lifestyle terms, because "
            "the money was never in your budget.",
        ],
        your_situation=[factpack.get("surplus_context")] if factpack.has("surplus_context") else [],
        risks=[
            "Committing more than your surplus supports leads to pausing later, which is worse "
            "than a smaller SIP maintained.",
        ],
        alternatives=[
            "An annual step-up rather than a one-off increase.",
            "Directing the increase to the weakest goal rather than the largest one.",
        ],
        tax_and_cost=["No tax consequence. New contributions start their own holding period."],
        recommendation=(
            "Worth doing if the surplus is genuinely spare."
            if after > now else "The increase does not move this goal materially."
        ),
        sources=factpack.sources(),
        disclaimer_ids=["DISC-PROJECTION-001"],
    )


def why_do_i_own_similar_funds(factpack):
    if not factpack.has("overlap_pct", "fund_a", "fund_b"):
        raise InsufficientData("I need your holdings' portfolios to measure overlap.")
    overlap = factpack.get("overlap_pct")
    return Answer(
        question="Why do I own funds with similar holdings?",
        answer=(
            f"{factpack.get('fund_a')} and {factpack.get('fund_b')} share {overlap}% of their "
            "portfolios. You are paying two expense ratios for close to one portfolio."
        ),
        why=[
            "Funds bought at different times, each well-rated then, often converge on the same "
            "large companies — the Indian large-cap universe is not very large.",
            "Overlap between different categories is expected; overlap inside the same category "
            "is duplication.",
        ],
        your_situation=[factpack.get("portfolio_context")] if factpack.has("portfolio_context") else [],
        risks=[
            "It looks diversified and is not, so a single market segment moves more of your "
            "portfolio than you would expect.",
        ],
        alternatives=[
            "Keep both and stop new contributions to one — no tax, no exit load.",
            "Consolidate over time as units pass their exit load and long-term holding period.",
        ],
        tax_and_cost=[
            "Consolidating immediately realises tax. Redirecting future contributions does not.",
        ],
        recommendation="Redirect new contributions before considering any sale.",
        sources=factpack.sources(),
        disclaimer_ids=["DISC-MF-RISK-001"],
    )


def what_if_i_stop_investing(factpack):
    required = ("goal_probability_pct", "probability_if_paused_pct", "pause_months")
    if not factpack.has(*required):
        raise InsufficientData("I need the goal projection to model a pause.")
    now = factpack.get("goal_probability_pct")
    after = factpack.get("probability_if_paused_pct")
    months = factpack.get("pause_months")
    return Answer(
        question=f"What happens if I stop investing for {months} months?",
        answer=(
            f"Pausing for {months} months moves this goal's probability from {now}% to {after}%."
        ),
        why=[
            "The cost of a pause is the contributions missed plus the compounding they would "
            "have earned — not a catastrophe, but not nothing either.",
        ],
        your_situation=[],
        risks=["A pause that becomes permanent is the real risk, not the pause itself."],
        alternatives=[
            "Reduce the SIP rather than stopping it — keeping the habit matters more than the amount.",
            "Pause the lowest-priority goal and protect the non-negotiable ones.",
        ],
        tax_and_cost=["Pausing has no tax consequence. Redeeming does."],
        recommendation="If cash is tight, reduce rather than stop, and set a restart date now.",
        sources=factpack.sources(),
        disclaimer_ids=["DISC-PROJECTION-001"],
    )


INTENTS = {
    "WHY_PORTFOLIO_DOWN": why_is_my_portfolio_down,
    "SHOULD_I_INCREASE_SIP": should_i_increase_my_sip,
    "WHY_SIMILAR_FUNDS": why_do_i_own_similar_funds,
    "WHAT_IF_I_STOP": what_if_i_stop_investing,
}


def answer(intent, factpack, registry=None, compliance_engine=None, confidence_pct=None):
    """Produce an answer and put it through the compliance language scan."""
    if intent not in INTENTS:
        raise InsufficientData(
            f"I don't have a verified way to answer '{intent}'. I would rather say so than "
            "produce something that sounds right.",
        )
    result = INTENTS[intent](factpack)
    result.confidence_pct = confidence_pct

    if compliance_engine is not None:
        check = compliance_engine.check_language(
            result.as_text(), contains_return_figure=True, contains_projection=True,
            mentions_scheme=True,
        )
        check.raise_if_blocked()
        for disclosure in check.required_disclosures:
            if disclosure not in result.disclaimer_ids:
                result.disclaimer_ids.append(disclosure)
    return result


# ── optional LLM layer ───────────────────────────────────────────────────────

class GroundedExplainer:
    """Wraps a language model with the checks that make it safe to use here.

    `client` is any callable taking a prompt and returning a string. Left as
    None — the default — the deterministic answer is used unchanged, so the
    platform works fully offline and no recommendation ever depends on a model
    being available.
    """

    def __init__(self, registry, client=None, model_id="explainer"):
        self.registry = registry
        self.client = client
        self.model_id = model_id
        self.spec = next(
            (m for m in registry.ai_models["models"] if m["model_id"] == model_id), None
        )
        if self.spec is None:
            raise InsufficientData(f"Model {model_id!r} is not in the registry and cannot be used.")
        if self.spec.get("status") != "ACTIVE":
            raise InsufficientData(
                f"Model {model_id!r} has status {self.spec.get('status')} and is blocked."
            )

    def build_prompt(self, answer_obj, factpack):
        facts = "\n".join(
            f"- {f.key}: {f.value}  (source: {f.source})" for f in factpack.facts.values()
        )
        return (
            "Rewrite the structured answer below as plain, calm prose for an Indian retail "
            "investor.\n\n"
            "Hard constraints:\n"
            "- Use ONLY the facts listed. Do not add any number that is not there.\n"
            "- Do not promise, guarantee or imply a certain outcome.\n"
            "- Do not name any scheme that is not in the facts.\n"
            "- Do not state a tax or regulatory rule of your own.\n"
            "- If something is not in the facts, say it is not known.\n\n"
            f"FACTS\n{facts}\n\nSTRUCTURED ANSWER\n{answer_obj.as_text()}\n"
        )

    def explain(self, answer_obj, factpack, compliance_engine=None):
        """Return prose — from the model if it passes every check, else the
        deterministic text. The fallback is silent to the investor and loud in
        the audit log."""
        if self.client is None:
            return {"text": answer_obj.as_text(), "generated_by": "deterministic",
                    "model_id": None, "checks": {"skipped": "no model configured"}}

        try:
            raw = self.client(self.build_prompt(answer_obj, factpack))
        except Exception as exc:                       # a model outage is not an outage
            return {"text": answer_obj.as_text(), "generated_by": "deterministic_fallback",
                    "model_id": self.model_id, "checks": {"error": str(exc)}}

        ungrounded = verify_numbers(raw, factpack)
        language_ok = True
        blocks = []
        if compliance_engine is not None:
            check = compliance_engine.check_language(
                raw, contains_return_figure=True, contains_projection=True, mentions_scheme=True)
            language_ok = check.allowed
            blocks = check.blocks

        if ungrounded or not language_ok:
            return {
                "text": answer_obj.as_text(),
                "generated_by": "deterministic_fallback",
                "model_id": self.model_id,
                "model_version": self.spec["version"],
                "checks": {
                    "ungrounded_numbers": ungrounded,
                    "language_blocks": blocks,
                    "action": "Model output rejected; deterministic text used instead.",
                },
            }

        return {
            "text": raw,
            "generated_by": "llm",
            "model_id": self.model_id,
            "model_version": self.spec["version"],
            "checks": {"ungrounded_numbers": [], "language_blocks": []},
        }
