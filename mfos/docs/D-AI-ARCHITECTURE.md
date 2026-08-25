# D. AI Architecture

## The organising rule

**The language model sits downstream of every decision, never upstream of one.**

By the time an LLM is called, the recommendation, the numbers, the signals and
the citations already exist. Its only job is to say them in a sentence a person
can read. It cannot change what is said, only how.

This is not a stylistic preference. A model that can produce a number can
produce a wrong number confidently, and in a financial product a confident
wrong number is the failure mode that matters.

## Four layers, four jobs

```
┌─────────────────────────────────────────────────────────────────┐
│  RETRIEVAL           versioned rule repositories, scheme docs    │
│                      → returns rules WITH their provenance       │
├─────────────────────────────────────────────────────────────────┤
│  RULES ENGINE        compliance, suitability, signals, limits    │
│                      → returns BLOCK / ALLOW / REQUIRE, with     │
│                        the rule ID that decided                  │
├─────────────────────────────────────────────────────────────────┤
│  DETERMINISTIC       XIRR, CAGR, rolling returns, Sharpe, tax,   │
│  FINANCIAL ENGINE    allocation, goal probability, scoring       │
│                      → the ONLY producer of numbers              │
├─────────────────────────────────────────────────────────────────┤
│  ML MODELS           anomaly detection, data quality, behaviour  │
│                      → flags for humans and gates, never advice  │
├─────────────────────────────────────────────────────────────────┤
│  LLM                 explanation, conversation, education        │
│                      → words only, over a fixed fact pack        │
└─────────────────────────────────────────────────────────────────┘
```

Information flows up. Nothing flows down.

## Layer 1 — Retrieval

Serves the versioned rule repositories (`config/tax/`, `config/compliance/`)
plus scheme documents and official disclosures.

Every retrieval returns the rule **with** its `rule_id`, `source`,
`effective_from`, `verification_status`. A rule without provenance is not
returned; there is no path by which an unattributed regulatory statement enters
the system.

`RuleSet.require_verified()` is the choke point. An `UNVERIFIED` rule raises
`RuleNotVerified` rather than returning a value. This is why the tax engine
refuses hybrid schemes outright instead of estimating them.

## Layer 2 — Rules engine

Compliance, suitability, signal thresholds and portfolio limits.

Trigger *predicates* live in reviewed Python; their *thresholds* live in
versioned YAML. Neither can change without appearing in a diff. The alternative —
evaluating condition strings from config — would have been more configurable and
considerably less safe: a config change could then alter logic without code
review.

The rules engine can only ever return BLOCK, ALLOW or REQUIRE_DISCLOSURE, always
with the rule ID that decided. `ComplianceResult.raise_if_blocked()` is the only
way past it.

## Layer 3 — Deterministic financial engine

Every number an investor sees originates here: `metrics.py`, `goals.py`,
`scenarios.py`, `sip.py`, `tax.py`, `scoring.py`, `portfolio.py`.

Properties enforced throughout:

- **Reproducible.** Same inputs, same outputs, forever. Goal probability is a
  closed-form moment-matched lognormal rather than a simulation precisely so a
  figure quoted in 2026 can be reproduced exactly in 2034.
- **Refusing.** Every function returns `None` rather than a fabricated value
  when input is insufficient. `M.cagr()` on eleven months returns `None`, not an
  annualised guess.
- **Exact where it matters.** Money is `Decimal` quantised to paise. Floats are
  used only for statistics, never for a figure in a tax computation.

## Layer 4 — ML models

Currently one, and it is switched off.

`anomaly_detector` v0.1.0 is registered with status `NOT_VALIDATED`. The gateway
refuses to invoke it. It is in the registry so that its existence, its intended
use ("data quality gating") and its prohibited use ("investment recommendations
of any kind") are on the record before anyone is tempted to enable it.

ML is permitted for anomaly detection, data quality, and behavioural
classification. It is prohibited from producing recommendations, scores, or any
number shown to an investor.

## Layer 5 — LLM

One active model: `explainer` v1.0.0.

**Permitted:** explaining a recommendation that has already been computed;
answering "why" questions about the investor's own portfolio; education.

**Prohibited, in the registry, enforced in code:**
- producing any number, rate, NAV, return, tax figure or probability
- deciding a recommendation, score or signal
- stating a regulatory or tax requirement from its own knowledge
- naming a scheme the deterministic shortlist did not produce

### The three enforcement mechanisms

**1. Fact pack.** The model receives a `FactPack` — an explicit set of facts,
each with a source. The prompt states that these are the only facts in scope.
Anything not in the pack does not exist for that call.

**2. Numeric grounding.** `verify_numbers()` extracts every numeric token from
the generated text and checks it against the pack. Two deliberate allowances:

- **Absolute-value comparison.** Prose legitimately turns a stored `-12.4` into
  "fell 12.4%". The sign is carried by the words, not the digits.
- **Small integers 0–100 pass.** They are overwhelmingly counts, ordinals and
  percentages already present. Blocking them would make the check so noisy it
  would be switched off — which is the usual fate of a guard that cries wolf.

Anything else absent from the pack is a hallucination by definition, and the
output is rejected.

**3. Deterministic fallback.** Every answer the copilot can produce with a model,
it can produce without one. On any failure — model outage, ungrounded number,
prohibited language — the investor gets the deterministic text. The fallback is
silent to the investor and loud in the audit log, which records the model ID, the
checks that failed and what was rejected.

The consequence worth stating plainly: **no recommendation on this platform
depends on a language model being available or correct.** Turn the LLM off
entirely and the product still works; the prose is just flatter.

## Model gateway

Every invocation passes through it:

1. Look up `model_id` in `config/ai/model_registry.yaml`. Unknown → refuse.
2. Check status. Not `ACTIVE` → refuse.
3. Check the call against the model's `prohibited_use`.
4. Build the prompt with the fact pack and the hard constraints.
5. Invoke.
6. Verify numbers against the pack.
7. Run the compliance language scan.
8. On any failure, fall back and log.
9. Record `model_id`, `model_version` and every check into the audit record.

## What we are not doing, and why

| Tempting | Why not |
|---|---|
| Fine-tune an LLM on fund data so it can answer directly | It would produce numbers. There is no acceptable rate of hallucinated NAVs. |
| Let the model rank funds | Ranking must be reproducible and explainable component by component. |
| Use an LLM agent that calls tools and decides | The decision sequence is the regulated artefact. It must be fixed, reviewed and replayable. |
| RAG over regulations to answer compliance questions | Retrieval over legal text produces plausible paraphrases of law. Rules are curated, versioned and human-verified instead. |
| Predictive models for fund returns | We do not believe they work, and if they did, presenting them to retail investors would be the wrong thing to do with them. |

## Prompt discipline

Prompts are versioned alongside code, not stored in a database where they can be
edited without review. A prompt change is a model version change and triggers
re-validation, because the prompt is as much part of the model's behaviour as its
weights.

Every prompt carries the same block:

```
- Use ONLY the facts listed. Do not add any number that is not there.
- Do not promise, guarantee or imply a certain outcome.
- Do not name any scheme that is not in the facts.
- Do not state a tax or regulatory rule of your own.
- If something is not in the facts, say it is not known.
```

## Evaluation

| Suite | Asserts | Frequency |
|---|---|---|
| Grounding | Ungrounded-number rate is zero on adversarial fact packs | Every build |
| Language | Prohibited phrasing is blocked, not rewritten | Every build |
| Refusal | Unknown intents and missing facts refuse rather than improvise | Every build |
| Fallback | Model outage and rejected output still yield an answer | Every build |
| Bias | Equivalent profiles differing only by name, gender or location receive equivalent recommendations | **PENDING — blocks LLM in production** |
| Calibration | Outcome distribution by confidence band | Quarterly |

The bias suite is marked PENDING in the registry rather than quietly skipped.
It blocks production use of the explainer model.

## Human oversight

Every model has a named accountable owner. Escalation to a qualified human
adviser is triggered by: confidence below 65, amounts at or above ₹10 lakh,
exit or reduce actions on retirement and child-education goals, three or more
conflicting signals, and any low-confidence recommendation on a non-negotiable
goal.

Rollback is a config change — pin the previous `model_id`/`version` in the
registry — and takes effect on the next request. No deployment required, because
the situation in which you need to roll back a model is rarely the situation in
which you want to run a deployment.
