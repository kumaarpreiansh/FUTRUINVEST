# L. API Architecture

## Services

| Service | Owns | Notes |
|---|---|---|
| identity | Auth, KYC, consent artefacts | The only service that touches raw PAN |
| profile | Investor profile, versioning | Emits `profile.updated` with the new version |
| goal | Goals, projections | Calls the deterministic engine; never caches a projection without its assumptions |
| risk | Three-dimensional assessment | Pure function of profile + goal |
| fund-data | Scheme master, NAV, observations, quality | The only writer of scheme data |
| market-data | Benchmarks, indices, macro | Read-only downstream |
| scoring | Fund scores | Recomputed on NAV and event, cached by (scheme, ruleset version) |
| portfolio | Holdings, lots, allocation, overlap | Reconciles against RTA daily |
| recommendation | The sixteen-stage pipeline | Orchestrator; calls everything, decides nothing itself |
| signal | Monitoring and signal evaluation | Scheduled, not request-driven |
| tax | Tax estimates | Refuses on unverified rules |
| compliance | Rule repository, gates, language scan | Can only say no |
| copilot | Conversational answers | Calls the model gateway; never the engines directly |
| alert | Dispatch, budget, digests | Owns the send log |
| execution | Order placement | **Disabled.** Exists to be gated. |
| audit | Append-only record | Write-once; no update endpoint exists |
| analytics | Aggregates, cohorts | Reads replicas only |

## Contract conventions

- REST over HTTPS, JSON, versioned by path (`/v1/…`).
- Money as a **decimal string** (`"125000.00"`), never a JSON number —
  JSON numbers are IEEE-754 doubles and lose paise.
- Every timestamp RFC 3339 UTC.
- Every response carrying a computed figure includes `ruleset_versions` and
  `disclaimer_ids`.
- Every mutating request requires an `Idempotency-Key`.
- Errors use a typed code from `errors.py`, never a bare 500.

### Error shape

```json
{
  "code": "INSUFFICIENT_DATA",
  "message": "This scheme is taxed at your slab rate. I need your applicable marginal rate before I can estimate the tax.",
  "detail": { "missing": "investor_marginal_rate_pct" },
  "rule_ids": ["TAX-SPECMF-SLAB-001"]
}
```

| Code | HTTP | Meaning |
|---|---|---|
| `INSUFFICIENT_DATA` | 422 | Ask the investor; do not retry |
| `DATA_QUALITY_FAILURE` | 422 | Scheme data below the floor |
| `COMPLIANCE_BLOCK` | 403 | A rule blocks this; `rule_ids` says which |
| `SUITABILITY_BLOCK` | 403 | Not suitable for this investor |
| `CONSENT_REQUIRED` | 428 | Specific consent needed |
| `RULE_NOT_VERIFIED` | 503 | The rule exists but is not verified; refusing to compute |
| `CONFIG_ERROR` | 500 | Configuration invalid; page someone |

`RULE_NOT_VERIFIED` returns 503 rather than 4xx deliberately: it is a
platform-side gap, not a client error, and it will resolve when a reviewer
verifies the rule.

## Selected endpoints

### Profile

```http
POST /v1/investors/{id}/profile
{ "monthly_income": "220000.00", "dependants": 2 }

201 { "version": 4, "fingerprint": "a1b2c3d4e5f6a7b8",
      "completeness": {"tier1": 100.0, "tier2": 70.0, "tier3": 22.2},
      "next_questions": [
        {"field": "liquidity_needs_next_12m",
         "why_asking": "Money needed within a year should not be in a market-linked scheme."}
      ]}
```

`next_questions` is part of the response, not a separate call — progressive
profiling only works if the next question travels with the last answer.

### Goal projection

```http
GET /v1/goals/{id}/projection

200 {
  "scenarios": {
    "base": {
      "expected_return_pct": 9.55, "volatility_pct": 12.71, "inflation_pct": 6.0,
      "inflation_adjusted_target": "102046000",
      "median_corpus": "83812000",
      "range_p10_p90": ["50213000", "141902000"],
      "probability_pct": 24.1,
      "required_return_pct": 11.0,
      "required_monthly_contribution": "62480",
      "assumptions": { "...": "the full CMA set" },
      "disclaimer_ids": ["DISC-PROJECTION-001", "DISC-PAST-PERF-001"]
    },
    "conservative": {}, "optimistic": {}
  },
  "probability_band": "UNLIKELY"
}
```

No endpoint returns a single scenario. The three come together or not at all —
a client cannot accidentally render one number as the answer.

### Recommendation

```http
POST /v1/investors/{id}/recommendations
{ "goal_id": "...", "context": {"trigger": "QUARTERLY_REVIEW"} }

200 { "status": "COMPLETE", "recommendation": {...}, "confidence": {...},
      "explanation": "...", "human_review": {...}, "audit_id": "REC-..." }

200 { "status": "HALTED",
      "halted_at": {"stage": 9, "name": "apply_compliance", "status": "BLOCKED",
                    "message": "This platform is not registered as an Investment Adviser...",
                    "output": {"next_step": "Complete SEBI IA registration..."}},
      "audit_id": "REC-..." }
```

A halt is **200, not an error**. It is a legitimate outcome with a reason, and
the client renders it as an explanation rather than a failure.

### Tax estimate

```http
POST /v1/tax/estimate
{ "holding_id": "...", "units": "1800", "action": "SWITCH" }

200 { "investment_value": "862229.60", "cost": "270000.00", "gain": "592229.60",
      "short_term_gain": "0.00", "long_term_gain": "592229.60",
      "exemption_applied": "125000.00", "estimated_tax": "60724.00",
      "estimated_tax_is_lower_bound": true,
      "surcharge": "UNKNOWN", "exit_load": "0.00", "net_proceeds": "801505.60",
      "rule_ids": ["TAX-EQ-LTCG-001", "TAX-CESS-001", "TAX-SWITCH-IS-REDEMPTION-001"],
      "ruleset_version": "2026-27.1",
      "caveats": ["Surcharge is not modelled...", "A switch is treated as a redemption..."] }
```

`estimated_tax_is_lower_bound` and `caveats` are required fields. A client that
renders the figure without them is failing the contract, and the design review
checklist says so.

### Consent and execution

```http
POST /v1/consents
{ "recommendation_id": "...", "scheme_code": "FLX01", "direction": "REDEEM",
  "amount": "862229.60", "tax_estimate_id": "...", "method": "IN_APP_2FA" }

201 { "consent_id": "...", "expires_at": "2026-08-25T12:15:00Z" }
```

The consent service rejects creation unless the tax estimate and conflict
disclosures were shown, and records evidence of both.

```http
POST /v1/orders
Idempotency-Key: 6f1c...

403 { "code": "COMPLIANCE_BLOCK",
      "message": "Execution is disabled on this build. Recommendations are produced; nothing is ever placed.",
      "rule_ids": ["PLATFORM-GATE", "SEBI-IA-CONSENT-001", "KYC-KRA-001", "AML-PMLA-001"] }
```

The endpoint exists and is permanently blocked. Having it present and refusing
is better than having it absent: the contract is reviewable, and the gate is
demonstrably in the path.

### Copilot

```http
POST /v1/copilot/ask
{ "question": "Why is my portfolio down?", "goal_id": "..." }

200 { "answer": "...", "why": [...], "your_situation": [...], "risks": [...],
      "alternatives": [...], "tax_and_cost": [...], "recommendation": "...",
      "confidence_pct": 82, "sources": [...],
      "disclaimer_ids": ["DISC-PAST-PERF-001", "DISC-MF-RISK-001"],
      "generated_by": "llm",
      "model": {"id": "explainer", "version": "1.0.0"} }
```

`generated_by` is `llm`, `deterministic`, or `deterministic_fallback`, and it is
in the response so the client — and later, an auditor — can see when the model
was bypassed.

## Cross-cutting

**Idempotency.** Every mutating endpoint requires an `Idempotency-Key`, stored
with the response for 24 hours. A duplicate returns the original response rather
than acting twice. Non-negotiable on anything that could place an order.

**Rate limits.** Per investor and per IP. The copilot is limited more tightly
because it is the expensive path.

**Circuit breakers.** Every downstream dependency has one. Fund-data failure
degrades to cached scores with a staleness warning; model-gateway failure
degrades to deterministic text. Neither takes the platform down.

**Read-your-writes.** Profile updates are strongly consistent. Everything else
tolerates eventual consistency, except anything on the consent-to-execution
path, which is strongly consistent end to end.

**No PII in URLs, logs or error messages.** Investor IDs are opaque UUIDs.
`errors.py` payloads are structured precisely so nobody has to interpolate an
investor's details into a message string.

## Events

| Event | Emitted by | Consumed by |
|---|---|---|
| `profile.updated` | profile | risk, recommendation, signal |
| `nav.published` | fund-data | scoring, portfolio, signal |
| `scheme.event` | fund-data | signal, alert |
| `signal.raised` | signal | alert, recommendation |
| `recommendation.created` | recommendation | audit, alert |
| `consent.granted` | identity | execution, audit |
| `ruleset.published` | compliance | everything |

`ruleset.published` invalidates every cached score and projection. A cached
figure computed under a superseded rule-set is a wrong figure, and the
invalidation is cheaper than the alternative.
