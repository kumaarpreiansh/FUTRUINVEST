# Legal Review Register

**Every open legal question, by rule ID.** Nothing in this register may be
resolved by an engineer, a product manager or a model. Each item needs a
qualified Indian securities or tax adviser.

`ComplianceEngine.readiness()` and `Registry.open_legal_items()` report the
machine-checkable subset. Twenty rules currently carry
`legal_review_required: true`.

---

## Blocking — the product cannot launch until these are settled

### L1 — Operating model
**Rules:** SEBI-IA-REG-001
**Status:** `operating_model: UNDETERMINED`

Is this an Investment Adviser, an execution-only distributor, or a segregated
structure? Everything downstream depends on the answer: which features may
ship, how revenue may be earned, what disclosures are required, and what the
grievance process must be.

Until resolved, `personalised_advice_enabled` is false and the pipeline halts at
stage 9 on every run.

**Question for counsel:** Given the product described in [A](A-PRD.md) — goal-based
personalised recommendations on specific schemes, for a fee — what registration
is required, and can advisory and any future execution activity coexist in one
entity or must they be segregated?

---

### L2 — SEBI IA registration
**Rules:** SEBI-IA-REG-001 · **Status:** NOT_MET

The platform must not describe itself as SEBI-registered until a number exists
and is recorded in `platform.yaml`. Asserted by
`test_platform_does_not_claim_registration`.

---

### L3 — Fee caps and permitted modes
**Rules:** SEBI-IA-FEE-001 · **Status:** UNVERIFIED, NOT_MET

Fee caps and permitted fee modes for Investment Advisers are prescribed and have
been amended. The rule is a placeholder with no values, and billing is disabled
until it is populated from primary sources.

**Question:** What are the operative fee caps and permitted modes for the chosen
model, and how must they be disclosed and consented to?

---

### L4 — AML/CFT programme
**Rules:** AML-PMLA-001 · **Status:** NOT_MET

Not built. `check_transaction()` fails closed on it, so no transaction can be
placed regardless of every other gate.

**Question:** What is the minimum viable AML/CFT programme — risk
categorisation, screening, monitoring rules, STR process, record-keeping — for
this entity type?

---

## Tax — every rule needs primary verification

All tax rules are `VERIFIED_SECONDARY` at best. They were corroborated from
reputable secondary commentary on 2026-08-25 and are usable only for figures
explicitly labelled as estimates.

### L5 — Verify every tax rule against primary sources
**Rules:** TAX-EQ-LTCG-001, TAX-EQ-STCG-001, TAX-SPECMF-SLAB-001,
TAX-DEBT-OLD-LTCG-001, TAX-SWITCH-IS-REDEMPTION-001, TAX-CESS-001, TAX-FIFO-001

Each must be checked against the Income-tax Act and the operative Finance Act,
and its status raised to `VERIFIED_PRIMARY`.

### L6 — Surcharge bands
**Rules:** TAX-SURCHARGE-001 · **Status:** UNVERIFIED, bands empty

Because surcharge is not modelled, **every tax figure the platform produces is a
lower bound** and is labelled as such. This is honest but not good enough for
production.

**Question:** What are the operative surcharge bands on capital gains, and how do
they interact with the investor's total income?

### L7 — Specified mutual fund classification
**Rules:** TAX-SPECMF-SLAB-001

Which schemes fall within the statutory definition under s.50AA is a legal
question, not an inference from a scheme name. The definition has itself been
amended.

**Question:** What is the operative definition, and what data must we hold per
scheme to classify it correctly?

### L8 — Hybrid scheme tax treatment
**Rules:** TAX-HYBRID-CLASSIFY-001 · **Status:** UNVERIFIED

The engine **refuses outright** on hybrid schemes rather than estimating —
`RuleNotVerified` is raised and the recommendation is blocked. Asserted by
`test_unverified_class_is_refused_not_guessed`.

**Question:** How is a hybrid scheme's tax treatment determined, and what
thresholds apply?

### L9 — STT on redemption
**Rules:** TAX-STT-001 · **Status:** UNVERIFIED, rate empty

Not currently included in cost estimates, which slightly understates the cost of
redeeming.

### L10 — Grandfathering
**Rules:** TAX-EQ-LTCG-001 caveat

Grandfathering of pre-01-Feb-2018 gains may apply to old holdings and is not
modelled. Currently surfaced as a caveat.

**Question:** How should grandfathered cost be computed, and what data do we need
from the investor or the RTA?

### L11 — Transitional treatment of pre-April-2023 debt units
**Rules:** TAX-DEBT-OLD-LTCG-001

Short-term treatment of these units is not modelled at all;
`InsufficientData` is raised.

---

## Product and disclosure

### L12 — Scheme categorisation and category floors
**Rules:** SEBI-MF-CATEGORY-001

Peer grouping and the category floors used in mandate-drift detection must be
confirmed against the operative circular and the AMFI half-yearly list.

### L13 — Risk-o-meter as a suitability control
**Rules:** SEBI-MF-RISKOMETER-001

The engine treats the risk-o-meter as a disclosure and a scoring input, and as a
hard gate only for schemes outside the investor's plan — because nearly every
Indian equity scheme is labelled "Very High" and a hard gate would make equity
uninvestable below Aggressive. See the [Decision Log](DECISION-LOG.md).

**Question:** Is this treatment defensible as a suitability control, or must the
risk-o-meter bind harder?

### L14 — Advertising and communication review
**Rules:** COMM-NO-GUARANTEE-001, COMM-PAST-PERF-001

Every investor-facing string — screens, alerts, notifications, emails, copilot
templates, marketing — needs legal review against the advertisement code.
`check_language()` catches prohibited phrases mechanically; it cannot judge
overall impression.

### L15 — Standardised performance periods
**Rules:** COMM-PAST-PERF-001

Which return periods must be shown, in what form, and with what caveat.

### L16 — Direct versus Regular presentation
**Rules:** AMFI-PLAN-DIRECT-REGULAR-001

The platform shows the long-term rupee cost difference prominently. Confirm this
presentation is compliant and, if a distribution arm ever exists, what
segregation and disclosure it requires.

---

## Data, AI and process

### L17 — DPDP compliance calendar and consent architecture
**Rules:** DPDP-RULES-2025-001 · **Status:** IN_PROGRESS

Staged commencement: foundational provisions in force from 14 Nov 2025, consent
manager provisions from 14 Nov 2026, substantive obligations from 14 May 2027.
The calendar is configuration; the system must not assume a single date.

**Questions:** Are we a Significant Data Fiduciary? What does the consent
architecture need to look like before the consent manager provisions commence?
How do retention obligations interact with securities record-keeping?

### L18 — AI/ML governance obligations
**Rules:** SEBI-AI-GOVERNANCE-001 · **Status:** IN_PROGRESS, watch item

SEBI's June 2025 paper is a **consultation paper**. The system is built to the
proposed obligations because they are sound practice, but final obligations may
differ.

**Action:** RegWatch tracks the final circular; next check 2026-09-25.

### L19 — Grievance redressal and ODR
**Rules:** not yet in the repository

What grievance process, SCORES registration and ODR participation are required
for the chosen operating model? Must be resolved before launch and added as a
rule.

### L20 — Breach notification
**Rules:** referenced in [M](M-SECURITY-ARCHITECTURE.md)

Reporting timelines and recipients under the DPDP framework and any applicable
SEBI cyber-incident reporting obligations must be confirmed and written into the
runbook.

### L21 — Record-keeping periods
**Rules:** not yet in the repository

What must be retained, for how long, in what form, and how does that interact
with DPDP erasure rights? The retention table in [K](K-DATABASE-SCHEMA.md) is
provisional pending this.

### L22 — International equity exposure
**Rules:** not yet in the repository

`EQUITY_INTL` has published assumptions but no allocation policy uses it,
because regulatory limits on overseas investment by Indian mutual funds have
been variable.

**Question:** What is the current position, and can it be relied on for a
long-horizon allocation?

---

## How this register is maintained

- Every rule with `legal_review_required: true` appears in
  `Registry.open_legal_items()`.
- Adding a rule with an unresolved question means adding it here.
- Resolving an item means raising the rule's `verification_status`, updating
  `compliance_status`, bumping the ruleset version, and recording the reviewer
  and date in `ruleset_version.approved_by`.
- An item is never closed by a code change alone.
