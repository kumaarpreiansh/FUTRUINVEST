# Q. AI Governance Framework

Registry: `config/ai/model_registry.yaml`. Governing rule: SEBI-AI-GOVERNANCE-001.

> SEBI's June 2025 consultation paper on responsible AI/ML use in Indian
> securities markets is a **consultation paper, not a final circular**. The rule
> records that, carries a watch item, and the framework below is built to the
> proposed obligations because they are sound practice regardless of the final
> text.

## The principle

**The regulated entity is responsible for what its models do.** Not the vendor,
not the model provider, not "the AI". That responsibility is only meaningful if
someone can answer, for any past recommendation: which model, which version,
what did it see, what did it produce, and what checked it.

That is what the registry and the audit trail exist to answer.

## Registered models

| Model | Version | Kind | Status |
|---|---|---|---|
| `explainer` | 1.0.0 | LLM | ACTIVE |
| `profile_gap_detector` | 0.9.0 | RULES | ACTIVE |
| `goal_probability` | 1.0.0 | DETERMINISTIC | ACTIVE |
| `anomaly_detector` | 0.1.0 | ML | **NOT_VALIDATED — blocked** |

`anomaly_detector` is in the registry precisely because it is not ready. Its
intended use ("data quality gating") and prohibited use ("investment
recommendations of any kind") are on the record before anyone is tempted to
enable it, and the gateway refuses to invoke it.

## Global constraints

```yaml
llm_may_produce_numbers: false
llm_may_name_a_scheme_not_in_shortlist: false
llm_may_assert_a_regulatory_fact: false
llm_output_passes_language_scan: true
every_output_records_model_id_and_version: true
human_override_always_available: true
```

`test_llm_may_not_produce_numbers` asserts the first one stays false.

## Per-model record

Every model carries: ID, version, kind, purpose, intended use, **prohibited
use**, inputs, grounding mode, validation date and owner, test suite references,
confidence threshold, rollback target, monitoring signals, status.

The prohibited-use list is the unusual field and the important one. For
`explainer`:

- producing any number, rate, NAV, return, tax figure or probability
- deciding a recommendation, score or signal
- stating a regulatory or tax requirement from its own knowledge
- naming a scheme that the deterministic shortlist did not produce

`test_every_ai_model_declares_prohibited_use` asserts every active LLM has one.

## Lifecycle

```
Proposal → Design review → Build → Validation → Bias testing
    → Hallucination testing → Compliance review → Registry entry
    → Shadow → Limited release → Full release → Monitoring
    → Periodic revalidation → Retirement
```

Nothing skips validation. A model without a validation date has status
`NOT_VALIDATED` and the gateway blocks it — which is enforcement, not policy.

## Validation gates

| Gate | Requirement | Blocks |
|---|---|---|
| Grounding | Zero ungrounded numbers on the adversarial suite | Release |
| Language | Zero prohibited phrases pass the scan | Release |
| Refusal | Unknown intents and missing facts refuse | Release |
| Fallback | Outage and rejection still produce an answer | Release |
| Bias | Equivalent profiles get equivalent outputs | **Release — currently PENDING** |
| Calibration | Confidence bands match outcome distributions | Full release |

The bias suite is `PENDING` in the registry. It blocks production use of the
explainer model and appears in every governance review until it exists.

## The model gateway

Every invocation:

1. Look up `model_id`. Unknown → refuse.
2. Check status. Not ACTIVE → refuse.
3. Check the call against `prohibited_use`.
4. Build the prompt with the fact pack and the constraint block.
5. Invoke.
6. Verify numbers against the fact pack.
7. Run the compliance language scan.
8. On failure, fall back deterministically and log.
9. Record model ID, version and every check into the audit row.

Steps 6 and 7 are what make step 5 acceptable.

## Prompts are versioned code

Prompts live alongside code, not in a database where they can be edited without
review. A prompt change is a model version change and triggers revalidation —
the prompt is as much a determinant of behaviour as the weights.

## Monitoring

| Signal | Threshold | Action |
|---|---|---|
| Ungrounded-number rate | > 0 | Page AI governance; consider disabling |
| Language block rate | > 1% | Review prompt; investigate |
| Fallback rate | > 5% | Investigate model or provider |
| Refusal rate | > 20% | Fact packs may be too thin |
| Latency p95 | > 3s | Degrade to deterministic |

Investor-visible degradation is silent — they get the deterministic answer.
Internally it is loud.

## Rollback

Pin the previous version in the registry. Effective on the next request. No
deployment, because the situation in which you need to roll back a model is
rarely the situation in which you want to run a deployment.

Every model declares its `rollback_to` target. A model with no safe predecessor
declares `null` and can only be disabled, not rolled back — which is stated
rather than discovered.

## Human oversight

| Trigger | Route |
|---|---|
| Confidence below 65 | Qualified adviser before presentation |
| Amount ≥ ₹10 lakh | Adviser review |
| EXIT_REVIEW or REDUCE on retirement or child education | Adviser review |
| Three or more conflicting signals | Adviser review |
| Any model output rejected by grounding | AI governance review |
| Unusual transaction pattern | Compliance review |

`needs_human_review()` computes this and the pipeline records the outcome. Where
review is required, nothing is presented for consent until a person has looked.

## Audit

Every AI-influenced output records model ID and version, the fact pack, the
checks run and their results, whether fallback was used and why, and the
generating path (`llm` / `deterministic` / `deterministic_fallback`).

The requirement this satisfies: **AI must never silently change an investment
recommendation without an auditable record.** Since the LLM cannot change a
recommendation at all — only its prose — the exposure is bounded by
architecture, and the audit trail proves it in any individual case.

## Accountability

Each model has a named accountable individual, recorded in the registry, with
technical understanding of what it does. The registry's `accountable_owner`
field holds a role today and must hold a named person before production.

Quarterly governance review covers: model performance against monitoring
thresholds, validation currency, incidents and rollbacks, the pending bias
suite, and any change in the regulatory position — including whether the SEBI
consultation has become a circular.

## What we chose not to do

| Option | Why not |
|---|---|
| An agentic LLM that calls tools and decides | The decision sequence is the regulated artefact; it must be fixed and replayable |
| Fine-tuning on investor data | Investor financial data is never used to train a third-party model |
| An LLM that ranks or scores funds | Ranking must be reproducible component by component |
| RAG over regulations for compliance answers | Retrieval over legal text produces plausible paraphrases of law |
| Model-generated numbers with a validation pass | Validating a number you should not have generated is the wrong control; do not generate it |
