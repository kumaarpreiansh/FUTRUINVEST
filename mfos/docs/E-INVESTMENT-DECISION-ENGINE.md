# E. Investment Decision Engine

The sixteen-stage pipeline. Implementation: `pipeline.py`.

Every stage can stop the run, and stopping is a legitimate outcome that gets
written to the audit log like any other.

## The stages

| # | Stage | Halts when |
|---|---|---|
| 1 | Understand investor | Tier-1 profile incomplete → returns the questions |
| 2 | Understand goal | No goal — nothing to be suitable for |
| 3 | Determine risk | A CRITICAL conflict (goal unreachable at any risk) |
| 4 | Required allocation | No policy covers this goal/horizon/band |
| 5 | Analyse portfolio | — |
| 6 | Identify gaps | — |
| 7 | Analyse eligible funds | No scheme has data good enough to score |
| 8 | Apply suitability | Nothing is both suitable and adequate |
| 9 | Apply compliance | Any blocking rule unmet |
| 10 | Apply tax | Governing rule unverified, or marginal rate missing |
| 11 | Expected outcomes | — |
| 12 | Generate recommendation | Confidence below the platform minimum |
| 13 | Explain | Explanation fails the language scan |
| 14 | Obtain consent | — (consent is requested, never assumed) |
| 15 | Execute | Always. Three gates, all closed. |
| 16 | Monitor | — |

## Stage detail

### 1. Understand investor

Requires all ten Tier-1 fields. On a gap the pipeline halts and returns the
missing fields ranked by impact, each with the reason it is being asked. It does
not default, infer, or proceed with a placeholder.

### 2. Understand goal

A recommendation without a goal is a product pitch. There is no code path that
produces one.

### 3. Determine risk

Computes capacity, tolerance and requirement. The binding band is
`min(capacity, tolerance)`; requirement never raises it.

Halts on `GOAL_UNATTAINABLE_AT_ANY_RISK` — where the required return exceeds the
best-returning asset class in the published assumptions. Continuing would mean
recommending an allocation that cannot reach the goal, which is worse than
saying so.

The unattainability test compares against the best available asset class rather
than a diversified portfolio, so a goal is only branded impossible when no real
allocation could reach it.

### 4. Required allocation

Policy selection → equity ceiling cap → small-cap limit → glide path. Detailed
in [G](G-PORTFOLIO-CONSTRUCTION.md).

### 5. Analyse current portfolio

Value, asset-class weights, pairwise overlap, look-through concentration by AMC,
sector, market cap and individual security.

### 6. Identify gaps

Under-allocated asset classes (more than 5 points below target) plus a rebalance
plan. An empty portfolio returns BUILD, not REBALANCE — arithmetically it is
100% off target, but the answer is to build it.

### 7. Analyse eligible funds

Scores the universe within SEBI category peer groups. Schemes below the data
quality floor return `NOT_SCORED` and are excluded. If nothing is scoreable,
the pipeline halts rather than lowering the floor.

### 8. Apply suitability

Shortlists per under-allocated asset class. Gates remove; they do not
down-weight. If nothing survives, the pipeline halts — "there is nothing here
that is both suitable for you and good enough" is a real answer.

### 9. Apply compliance

Three checks: advice permitted, suitability satisfied, conflicts handled. Any
block halts the run.

On the shipped build this is where every run stops, because
`sebi_ia_registration_verified` is false. The halt message says so, and adds:

> The analysis above is complete and available as information. What is blocked
> is presenting it as personalised advice.

That distinction is the whole regulatory design in one sentence.

### 10. Apply tax

Only when something would be sold. `RuleNotVerified` or `InsufficientData`
halts the run:

> I will not show you a recommendation whose tax consequence I cannot compute.

### 11. Expected outcomes

Three scenarios, seven stress tests, probability band. Ranges always.

### 12. Generate recommendation

`_decide()` maps stage outputs to one state, in a deliberately conservative
order:

1. An action signal (EXIT_REVIEW / REDUCE / SWITCH_REVIEW) wins.
2. Else a REBALANCE signal.
3. Else ADD, if there are gaps or nothing is held.
4. Else the signal's REVIEW or WATCH.
5. Else HOLD.

Confidence is then computed. Below the platform minimum the pipeline halts:

> I don't have a strong enough basis to recommend anything here.
> Improve the inputs rather than lowering the bar.

### 13. Explain

Assembled from evidence, not generated: recommendation, why, your situation
(including risk conflicts), candidates with their notes, what could make this
wrong, and tax and cost. Then run through the compliance language scan. A
failed scan halts rather than silently rewriting — a silent rewrite would hide
that prohibited language was produced, which is exactly what AI governance needs
to see.

### 14. Obtain consent

Consent is **specific to one transaction**: bound to scheme, direction and
amount, captured only after the tax estimate and conflict disclosures are shown,
and expiring. `verify_consent()` rejects a mismatch on any field, an expired
token, or consent captured without those disclosures.

Where human review is required, nothing is presented for consent until a person
has reviewed it.

### 15. Execute

Always `DISABLED`:

> Execution is switched off on this build, automated execution is permanently
> prohibited, and no consent exists. Three independent gates, all closed.

Asserted by `test_execution_stage_is_always_disabled`.

### 16. Monitor

Schedules the next review and states what would change the answer — the closing
of the loop and the start of the next one.

## The three override rules, as code

| Rule | Where |
|---|---|
| COMPLIANCE OVERRIDES AI | Stage 9; `ComplianceResult.raise_if_blocked()` |
| DATA QUALITY OVERRIDES AI | Stage 7; `funds.require_usable()` |
| SUITABILITY OVERRIDES PERFORMANCE | Stage 8; `selection.eligibility()` filters, never weights |

## Goal probability: the method, stated so it can be argued with

Terminal wealth `W = Σ cᵢ·exp(Xᵢ)`, where `Xᵢ ~ N(m·tᵢ, s²·tᵢ)` and
`Cov(Xᵢ, Xⱼ) = s²·min(tᵢ, tⱼ)` — contributions share the market path from the
later contribution date onwards.

Log parameters from simple-return moments:
`s² = ln(1 + σ²/(1+μ)²)`, `m = ln(1+μ) − s²/2`.

Both moments of `W` are computed exactly; `W` is then matched to a lognormal and
`P(W ≥ target)` read off it. The approximation is confined to the final matching
step.

**Known limitations, published with every projection:**
- Returns are assumed lognormal and independent across years. Real markets have
  fat tails and momentum.
- Long-run average assumptions are used, and no individual decade has matched
  them.
- Contributions are assumed to be made as scheduled.
- The glide path is approximated by time-weighted assumptions rather than by
  path-dependent simulation.

**Why closed-form rather than Monte Carlo:** an investor is told "84%". Eight
years later, in a dispute, that figure must be reproducible exactly. Monte Carlo
with a stored seed is reproducible only while the RNG implementation is stable
across language versions. Closed-form is reproducible by arithmetic.

## Signal scoring, worked

```
Underperformance in rolling 3y windows        +20
Bottom quartile 3y and 5y                     +10
Risk-adjusted deterioration                   +20
Expense increase above category median        +15
Concentration above category norm             +15
                                              ────
                                               80  → EXIT_REVIEW band
```

But every trigger is about *performance*, and none is a material event, so:

```
Held at SWITCH_REVIEW.
"Every trigger was about how the scheme has performed — nothing changed about
 the scheme itself or about your goal. A weak fund is a reason to change the
 fund, not to leave the asset class."
```

Now add costs:

```
Exit load still applicable                    −15
Tax cost 4.2% of value                        −20
Acted on this holding 7 months ago            −30
                                              ────
                                               15  → HOLD
```

The engine's answer changes from "switch" to "hold" because acting is expensive
and recent — which is the correct answer and the one a churn-driven product
would never give.
