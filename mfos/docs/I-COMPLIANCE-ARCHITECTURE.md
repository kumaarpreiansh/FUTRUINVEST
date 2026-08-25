# I. Compliance Architecture

Implementation: `compliance.py`. Rules: `config/compliance/regulatory_rules.v1.yaml`.

> **This document is not legal advice.** Every rule below carries a
> verification status, and several are marked UNVERIFIED. A qualified Indian
> securities lawyer must review the [Legal Review Register](LEGAL-REVIEW-REGISTER.md)
> before any regulated behaviour ships.

## The first question: what is this business?

Before a line of product code matters, one thing has to be settled:

**Is this an Investment Adviser, an execution-only distributor, or something
else?**

The brief calls for personalised recommendations — switch this fund, reduce that
exposure, this allocation for your goal. In India that is investment advice, and
providing it for consideration generally requires registration as an Investment
Adviser under the SEBI (Investment Advisers) Regulations, 2013.

`config/platform.yaml` records the answer as `UNDETERMINED` and gates the
product accordingly:

```yaml
regulatory:
  sebi_ia_registration_number: null
  sebi_ia_registration_verified: false
  operating_model: "UNDETERMINED"
gates:
  personalised_advice_enabled: false
```

The pipeline halts at stage 9 on every run, with a message that draws the
distinction the whole architecture rests on:

> The analysis above is complete and available as information. What is blocked
> is presenting it as personalised advice.

**The platform must not describe itself as SEBI-registered until a registration
number exists and is recorded.** Enforced by
`test_platform_does_not_claim_registration`.

## Three enforcement postures

| Posture | Meaning | Example |
|---|---|---|
| **BLOCK** | The behaviour cannot occur | Advice without registration |
| **REQUIRE_DISCLOSURE** | It can occur with a mandated disclosure attached | Any return figure |
| **WARN / PLAN** | Logged for the compliance register and roadmap | AI governance obligations |

**Fail-closed.** A rule that is missing, expired or unverified blocks the
behaviour it governs. An unverified rule can never be used to justify *allowing*
something — only to block it.

## The rule repository

Every rule carries: `rule_id`, `title`, `domain`, `source`, `source_url`,
`effective_from`, `effective_to`, `applicable_product`, `applicable_investor_type`,
`system_impact`, `enforcement`, `control_id`, `verification_status`,
`legal_review_required`, `compliance_status`.

`test_every_regulatory_rule_has_provenance` asserts the core fields exist on
every rule. A regulatory assertion without a source cannot enter the system.

### Rules currently in the repository

| Rule ID | Domain | Enforcement | Status |
|---|---|---|---|
| SEBI-IA-REG-001 | Advisory registration | BLOCK | **NOT_MET** |
| SEBI-IA-CONSENT-001 | Per-transaction consent | BLOCK | MET_BY_DESIGN |
| SEBI-IA-SUITABILITY-001 | Risk profiling and suitability | BLOCK | MET_BY_DESIGN |
| SEBI-IA-CONFLICT-001 | Conflict segregation and disclosure | BLOCK | MET_BY_DESIGN |
| SEBI-IA-FEE-001 | Fee caps and modes | BLOCK | **NOT_MET / UNVERIFIED** |
| SEBI-MF-CATEGORY-001 | Scheme categorisation | WARN | MET_BY_DESIGN |
| AMFI-PLAN-DIRECT-REGULAR-001 | Plan disclosure | REQUIRE_DISCLOSURE | MET_BY_DESIGN |
| SEBI-MF-RISKOMETER-001 | Risk-o-meter disclosure | REQUIRE_DISCLOSURE | MET_BY_DESIGN |
| COMM-NO-GUARANTEE-001 | No guaranteed-return claims | BLOCK | MET_BY_DESIGN |
| COMM-PAST-PERF-001 | Past-performance caveat | REQUIRE_DISCLOSURE | MET_BY_DESIGN |
| KYC-KRA-001 | KYC before transacting | BLOCK | MET_BY_DESIGN |
| AML-PMLA-001 | AML/CFT obligations | BLOCK | **NOT_MET** |
| DPDP-RULES-2025-001 | Data protection, staged | PLAN | IN_PROGRESS |
| SEBI-AI-GOVERNANCE-001 | Responsible AI/ML use | PLAN | IN_PROGRESS |

`ComplianceEngine.readiness()` returns:

> Not launchable as an advisory service. 3 blocking rule(s) unmet, 1 rule(s)
> unverified. The engines run; the regulated behaviours are gated off.

## The four gates

### Gate 1 — Advice permitted

Runs first in the pipeline. Requires both a verified registration number and
the `personalised_advice_enabled` flag. Attaches `DISC-NOT-REGISTERED-001` when
it blocks.

### Gate 2 — Suitability

Requires a complete Tier-1 profile and a risk assessment on file. Warns when
the profile is older than 180 days on a material recommendation. Blocks any
scheme above the investor's band that is not part of their plan.

**Design note on the risk-o-meter.** Nearly every Indian equity scheme, large-cap
funds included, is labelled "Very High". Using it as a hard per-scheme gate would
make equity uninvestable for every band below Aggressive — an absurd outcome. So
membership of the goal's target allocation *is* the risk gate (that allocation
was itself built under the investor's equity ceiling), and the risk-o-meter gate
applies only to schemes asked about outside the plan, which is the case where it
is genuinely useful. This is recorded in the [Decision Log](DECISION-LOG.md).

### Gate 3 — Conflicts

Blocks outright if `commission_influences_ranking` is ever true. Attaches a
disclosure to every scheme with a commercial relationship.

The substantive control is not this gate but the scoring test described in
[F](F-FUND-SCORING-METHODOLOGY.md): the universe is re-scored with commissions
perturbed and the build fails if any score moves.

### Gate 4 — Transaction

Four independent checks, all of which must pass:

1. KYC status is VALIDATED
2. AML controls are implemented (currently NOT_MET → fail-closed)
3. Valid per-transaction consent
4. `execution_enabled` is true (currently false)

On the shipped build all four fail, and each says why.

## Consent

Consent is **specific to one transaction**, not a blanket authorisation:

```python
Consent(investor_id, scheme_code, direction, amount,
        granted_at, expires_at, method,
        disclosures_shown, tax_estimate_shown, conflicts_shown)
```

`verify_consent()` rejects: missing consent, a mismatch on scheme, direction or
amount, an expired token, consent captured without the tax estimate shown, and
consent captured without conflict disclosures shown. Seven tests cover these.

The last two are the interesting ones: consent given without seeing the tax
consequence is not informed consent, so the system treats it as no consent at
all.

## Language control

Every outbound string — LLM output, alert, notification, email, marketing copy —
passes `check_language()`.

A violation **blocks delivery**. It is never silently rewritten, because a silent
rewrite hides that a model produced prohibited language, and that fact is exactly
what AI governance needs to see.

Disclosures attach automatically: return figures pull `DISC-PAST-PERF-001`,
projections pull `DISC-PROJECTION-001`, scheme mentions pull `DISC-MF-RISK-001`,
and while unregistered, scheme mentions also pull `DISC-NOT-REGISTERED-001`.

## Data protection

The DPDP Rules 2025 commence in stages, so the rule carries a **compliance
calendar** rather than a single date:

| Milestone | Date | Status |
|---|---|---|
| Foundational provisions, Data Protection Board | 2025-11-14 | IN_FORCE |
| Consent manager provisions | 2026-11-14 | PENDING |
| Substantive data fiduciary obligations | 2027-05-14 | PENDING |

The application must read this calendar rather than hard-code a date. Consent
artefacts, purpose limitation, retention schedules and erasure workflows must be
live *before* the substantive date, not on it.

## AI governance

SEBI issued a consultation paper on responsible AI/ML use in Indian securities
markets in June 2025. It is a consultation paper, not a final circular, and the
rule records that distinction with a watch item:

```yaml
watch:
  reason: "This was a consultation paper. Final obligations may differ.
           RegWatch must track the final circular and re-open this rule."
  next_check: "2026-09-25"
```

The system is built to the proposed framework — model registry, documented
intended and prohibited use, validation, audit trail, named accountable owner,
rollback — because those are sound practice regardless of what the final
circular says. Detail in [Q](Q-AI-GOVERNANCE.md).

## Audit

Every recommendation stores the regulatory rules evaluated, the tax rules used,
the disclosures shown, the conflicts disclosed, and the version of every
rule-set in force. `regulatory_rules_used` on a completed run reads:

```
SEBI-IA-CONFLICT-001, SEBI-IA-REG-001, SEBI-IA-SUITABILITY-001
```

Which means: three years from now, someone can establish exactly which rules
were applied to a given decision, in the version that was live at the time.

## What a lawyer must settle before launch

Summarised here, itemised in the [Legal Review Register](LEGAL-REVIEW-REGISTER.md):

1. **The operating model.** IA, distributor, or a segregated structure — this
   decides which features may ship at all.
2. **Fee caps and permitted modes** (SEBI-IA-FEE-001, UNVERIFIED). Billing is
   disabled until verified.
3. **Every tax rule.** All are VERIFIED_SECONDARY at best; two are UNVERIFIED.
4. **Surcharge bands.** Unpopulated, so every tax figure is a lower bound.
5. **Specified mutual fund classification.** Which schemes fall under s.50AA is
   a legal question, not an inference from a scheme name.
6. **Hybrid scheme tax treatment.** Currently UNVERIFIED and refused outright.
7. **AML/CFT programme.** Not built. Fail-closed until it is.
8. **Advertising review** of every investor-facing string.
9. **The DPDP compliance calendar** and the consent architecture.
10. **Grievance redressal and the SCORES/ODR obligations** for the chosen model.
