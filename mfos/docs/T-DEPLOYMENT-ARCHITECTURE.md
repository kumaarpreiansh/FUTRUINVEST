# T. Production Deployment Architecture

## Topology

```
                         ┌──────────────┐
                         │   WAF / CDN  │
                         └──────┬───────┘
                                │
                         ┌──────▼───────┐
                         │  API Gateway │  auth, rate limit, idempotency
                         └──────┬───────┘
                                │
        ┌───────────────────────┼───────────────────────┐
        │                       │                       │
  ┌─────▼─────┐         ┌───────▼───────┐       ┌───────▼───────┐
  │  Edge     │         │  Core         │       │  Governed     │
  │  services │         │  services     │       │  services     │
  ├───────────┤         ├───────────────┤       ├───────────────┤
  │ profile   │         │ recommendation│       │ compliance    │
  │ goal      │         │ scoring       │       │ tax           │
  │ portfolio │         │ signal        │       │ execution ⛔  │
  │ copilot   │         │ risk          │       │ audit         │
  │ alert     │         │ fund-data     │       │ identity      │
  └─────┬─────┘         └───────┬───────┘       └───────┬───────┘
        │                       │                       │
        └───────────────────────┼───────────────────────┘
                                │
        ┌───────────────────────┼───────────────────────┐
   ┌────▼─────┐          ┌──────▼──────┐         ┌──────▼──────┐
   │ Postgres │          │    Redis    │         │  WORM store │
   │ primary  │          │   cache     │         │  audit      │
   │ + replicas│         │  + queues   │         │  replica    │
   └──────────┘          └─────────────┘         └─────────────┘
```

Three tiers, chosen by blast radius rather than by traffic:

- **Edge** — investor-facing, horizontally scaled, stateless.
- **Core** — the engines. Compute-heavy, cacheable, no direct investor traffic.
- **Governed** — compliance, tax, execution, audit, identity. Restricted
  deployment, two-person change approval, separate audit trail, tighter access.

The separation is what allows a compliance rule change to follow a different,
slower path than a UI change without slowing the UI down.

## Environments

| Env | Data | Purpose |
|---|---|---|
| `local` | Synthetic only | Development. `demo.py` runs here. |
| `ci` | Synthetic only | 176 tests plus lint, SAST, dependency scan |
| `staging` | Masked production shape | Integration, load, UAT |
| `sandbox` | Synthetic, real rules | Ruleset changes rehearsed before production |
| `production` | Live | |

`enable_sandbox_advisory()` — which lets the full sixteen-stage pipeline run for
demonstration — **refuses outright when `build_stage` is `production`**. Asserted
by `test_sandbox_refused_on_production_build`. The mechanism that makes the demo
possible cannot be the mechanism that fakes registration in production.

## Deployment pipeline

```
commit → lint → 176 tests → SAST → dependency scan → SBOM
       → build → sign → staging → smoke → canary 5% → 25% → 100%
```

**Ruleset changes take a different path:**

```
ruleset diff → full suite against BOTH versions → impact analysis
             → two-person approval → sandbox → production
```

The impact analysis answers: how many live recommendations change state under
the new rules? A threshold change that flips two hundred holdings from HOLD to
SWITCH_REVIEW is a product decision requiring sign-off, not a config push.

## Configuration

Rule-sets are **signed, content-hashed artefacts**, versioned in git,
deployed as immutable objects. The registry verifies the signature at load and
refuses to start on a mismatch.

`Registry._validate()` runs at start-up and refuses to boot when:
- fund scoring pillar weights do not sum to 100
- any allocation policy does not sum to 100
- any locked safety gate is true

A misconfigured build does not start. Failing to start is a much better outcome
than running with a broken control.

## Scheduled work

| Job | Cadence | Notes |
|---|---|---|
| NAV ingestion | Daily, post-cutoff | Multi-source, reconciled |
| Fund score recomputation | Daily after NAV | Cached by (scheme, ruleset version) |
| Signal evaluation | Monthly per holding, staggered | Never all at once — a synchronised evaluation produces a synchronised alert storm |
| Portfolio reconciliation vs RTA | Daily | Mismatch freezes the account |
| Goal reprojection | Weekly, and on any change | |
| Alert digests | Daily and weekly | Per investor local time |
| Data quality sweep | Daily | Feeds the quality gate |
| Regulatory scan | Daily | Human review always |
| Backup verification | Daily | Restore test quarterly |

The staggering of signal evaluation is not an optimisation. Evaluating every
holding on the first of the month would produce a monthly wave of alerts, which
is exactly the notification pattern the alert policy exists to prevent.

## Caching and invalidation

| Cached | TTL | Invalidated by |
|---|---|---|
| Fund scores | 24h | `nav.published`, `scheme.event`, `ruleset.published` |
| Goal projections | 7d | `profile.updated`, contribution change, `ruleset.published` |
| Peer statistics | 24h | `nav.published`, category reclassification |
| Rule-sets | Process lifetime | Deployment only |

`ruleset.published` invalidates every cached score and projection globally. A
cached figure computed under a superseded rule-set is a wrong figure, and the
invalidation cost is trivial against the alternative.

## Observability

**Metrics** — the ones that matter are product invariants, not infrastructure:

| Metric | Alert |
|---|---|
| Recommendations halted, by stage | Sudden shift in the distribution |
| Ungrounded-number rate | **> 0, always** |
| Language block rate | > 1% |
| Model fallback rate | > 5% |
| Data quality, median | < 80 |
| Schemes below the quality floor | Sudden increase |
| Alerts per investor per month | > 8 |
| Signal state distribution | Sudden shift toward action states |
| Audit chain integrity | Any break — Sev-1 |

A sudden shift toward action signals is the early warning for a churn-generating
config error, and it is monitored for that reason.

**Tracing** — every recommendation carries a trace ID spanning all sixteen
stages, so a slow or halted recommendation can be read stage by stage.

**Logging** — structured, no PII, opaque investor IDs, scrubbed at the pipeline.
Governed services log to a separate, longer-retained stream.

## Resilience

| Dependency | Failure mode | Degradation |
|---|---|---|
| NAV feed | Stale data | Serve cached with a visible staleness warning; block new recommendations if beyond the limit |
| Model gateway | Outage | Deterministic text, silently to the investor and loudly internally |
| RTA | Outage | Read-only portfolio; no reconciliation-dependent actions |
| Compliance service | Outage | **Fail closed.** No recommendations. |
| Tax service | Outage | No recommendation with a tax component |
| Audit service | Outage | **Halt recommendation generation.** No unaudited advice. |

The last two are the load-bearing ones. It is better to produce no
recommendation than one that cannot be explained or replayed.

## Capacity and SLOs

| Endpoint | p95 | p99 |
|---|---|---|
| Goal projection | 300ms | 800ms |
| Fund score (cached) | 50ms | 150ms |
| Full recommendation | 3s | 8s |
| Copilot | 4s | 10s |

Availability: 99.9% for planning and analytics; 99.95% for the execution path
once it is enabled.

Signal evaluation is batch and off the interactive path entirely.

## Disaster recovery

RPO 15 minutes. RTO 4 hours platform-wide, 1 hour for execution once enabled.

Cross-region replication with a quarterly **restore test against a real
restore** — not a backup-completed metric. The audit WORM replica has its own
retention policy and its own credentials, so an attacker with full production
database access still cannot rewrite history unnoticed.

Documented degraded mode: planning, analytics and education continue when
fund-data or the model gateway is unavailable.

## Data residency

Investor data stays in India. Backups stay in India. The model gateway must use
an India-region endpoint or an on-premises deployment; if neither is available
for a given provider, the LLM layer is disabled and the platform runs
deterministically — which it is designed to do without loss of correctness.

## Cost shape

Data licensing dominates and is largely fixed. Compute is dominated by daily
score recomputation across the universe and by copilot inference, which is why
the copilot is rate-limited per investor. Storage grows with the audit trail,
which is retained indefinitely and is cheap relative to its value.
