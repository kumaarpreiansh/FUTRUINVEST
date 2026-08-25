# R. Regulatory Change Management

## The problem this solves

A SEBI circular is published on a Tuesday. Somewhere in a fintech, someone reads
it, mentions it in a standup, and a developer changes a constant. Six months
later nobody can say which version of which rule applied to a recommendation
made in March, or who approved the change.

That is the failure mode. The answer is that **a regulatory change is a
versioned configuration change with an approver, and every recommendation
records the versions it was computed under.**

## The pipeline

```
DETECT → ASSESS → CLASSIFY → DRAFT → REVIEW → APPROVE
   → DEPLOY → VERIFY → COMMUNICATE → ARCHIVE
```

### Detect

Monitored sources, with owners:

| Source | Cadence | Owner |
|---|---|---|
| SEBI circulars and master circulars | Daily | RegWatch |
| SEBI consultation papers | Weekly | RegWatch |
| AMFI circulars and best practice guidelines | Weekly | RegWatch |
| Finance Act, CBDT notifications | On publication | Tax counsel |
| MeitY / DPDP notifications | Weekly | Privacy |
| RBI, where payments are involved | Weekly | Compliance |
| AMC addenda for held schemes | Daily | Fund data |

Automated feeds where they exist, human review always. A regulatory change
detected by a scraper and not read by a person is not detected.

Rules can also carry their own watch item:

```yaml
watch:
  reason: "This was a consultation paper. Final obligations may differ.
           RegWatch must track the final circular and re-open this rule."
  next_check: "2026-09-25"
```

### Assess

For every change, six questions:

1. Does it apply to our operating model?
2. Which rule IDs does it touch, or does it need a new one?
3. What is the effective date, and is there a transition period?
4. Which system components does it affect?
5. Does it change what we may say, what we may recommend, or what we may do?
6. Does it invalidate anything already recommended?

Question 6 is the one most often skipped and the most expensive to skip. A rule
change that makes a live recommendation wrong requires proactive investor
communication, not a quiet config update.

### Classify

| Class | Meaning | Path |
|---|---|---|
| **P0 — Blocking** | A permitted behaviour becomes prohibited | Emergency: disable the feature, then fix |
| **P1 — Material** | Changes recommendation logic or figures | Standard change with full regression |
| **P2 — Disclosure** | New or changed disclosure required | Standard change |
| **P3 — Informational** | No system impact | Register only |

A P0 disables first and fixes second. The gates in `platform.yaml` exist partly
for this: switching a feature off is a config change, not a deployment.

### Draft

The change is a diff against a YAML rule-set. Every field is mandatory:

```yaml
- rule_id: "SEBI-XXX-001"
  title: "..."
  domain: "..."
  source: "SEBI circular SEBI/HO/.../2026/XX dated ..."
  source_url: "https://www.sebi.gov.in/..."
  effective_from: "2026-10-01"
  effective_to: null
  applicable_product: ["MUTUAL_FUND"]
  applicable_investor_type: ["ALL"]
  system_impact:
    - "Named component and named behaviour"
  enforcement: "BLOCK"
  control_id: "CTRL-..."
  verification_status: "VERIFIED_PRIMARY"
  legal_review_required: false
  compliance_status: "MET_BY_DESIGN"
```

A rule cannot enter the repository without a source and a verification status.
`test_every_regulatory_rule_has_provenance` enforces this at build time.

### Review and approve

| Change | Reviewers |
|---|---|
| Tax rule | Tax counsel + engineering |
| Compliance rule | Legal + compliance officer + engineering |
| Scoring weights | Investment committee + engineering |
| Allocation policy | Investment committee |
| Signal thresholds | Investment committee + engineering |
| Alert policy | Product + compliance |

**Two-person approval on tax and compliance rules.** Recorded in
`ruleset_version.approved_by`. Deployment refuses a tax or compliance ruleset
without it.

### Deploy

1. Bump the ruleset version (semantic: major for behaviour, minor for additions,
   patch for corrections).
2. Run the full suite against **both** old and new versions.
3. Diff the impact: how many live recommendations change state under the new
   rules?
4. If the diff is material, that is a product decision requiring sign-off, not a
   config push.
5. Deploy with the ruleset marked `active_from`; the old version stays queryable
   forever.
6. Emit `ruleset.published`, which invalidates every cached score and projection.

Step 3 is the one that distinguishes this from a config change. A threshold
adjustment that flips two hundred holdings from HOLD to SWITCH_REVIEW is not a
tweak.

### Verify

- The rule appears in `ComplianceEngine.register()` with the right status
- `readiness()` reflects the change
- Recommendations under the new version cite the new rule IDs
- A replay of a sampled historical recommendation still produces its original
  result under its original version

That last check is the guarantee that matters: **new rules do not rewrite old
decisions.** They apply forward.

### Communicate

| Audience | When | How |
|---|---|---|
| Investors with affected recommendations | Before the effective date | Direct, explaining what changed and what it means for them |
| All investors | If disclosures change | In-app on next session |
| Advisers | On approval | Briefing note |
| Support | On approval | FAQ update |

### Archive

The circular, the assessment, the diff, the approvals, the impact analysis and
the deployment record are retained indefinitely and linked to the ruleset
version. That package is what a regulator would ask for.

## Versioning

```
tax.IN            2026-27.1
compliance.IN     1.0.0
scoring.fund      1.0.0
allocation.policy 1.0.0
signals           1.0.0
alerts            1.0.0
ai.registry       1.0.0
```

Every recommendation stores all seven. `Registry.version_stamp()` produces the
stamp; `audit.new_record()` pins it.

## Verification status, and why the ladder exists

| Status | Meaning | May be used to compute? |
|---|---|---|
| `VERIFIED_PRIMARY` | Checked against the bare Act, circular or official text by a qualified reviewer | Yes |
| `VERIFIED_SECONDARY` | Corroborated from reputable commentary only | Yes, for figures labelled estimates |
| `UNVERIFIED` | Placeholder | **No** — the engine refuses |
| `COMMITTEE_ASSUMPTION` | Our judgement, published as such | Yes, for projections labelled as such |

Every tax rule in the repository is currently `VERIFIED_SECONDARY` at best, and
two are `UNVERIFIED`. The ladder is what lets the product ship the engines
honestly while the verification work proceeds: nothing unverified reaches an
investor as a number.

## Standing calendar

| Review | Cadence |
|---|---|
| Regulatory scan | Daily |
| Compliance register review | Monthly |
| Tax rule review | Post-Budget, and half-yearly |
| Scoring methodology | Half-yearly |
| Allocation policy and CMAs | Half-yearly |
| AI governance review | Quarterly |
| Full ruleset audit | Annual |
| DPDP compliance calendar check | Quarterly |

The DPDP check is calendar-driven because its obligations commence in stages
through May 2027, and the system reads that calendar from config rather than
assuming a single date.

## Open watch items

| Item | Why | Next check |
|---|---|---|
| SEBI AI/ML consultation → final circular | Obligations may differ from the proposals we built to | 2026-09-25 |
| Union Budget | Capital gains treatment has changed twice recently | Post-Budget |
| DPDP staged commencement | Consent manager provisions, then substantive obligations | Quarterly |
| SEBI IA framework amendments | The framework has been amended repeatedly | Monthly |
| AMFI half-yearly market-cap list | Changes peer groups and category floors | Half-yearly |
