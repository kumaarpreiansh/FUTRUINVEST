# A. Product Requirements Document

## 1. The problem

An Indian investor with ₹50,000 a month to invest can name a dozen "top rated"
funds within ten minutes and still have no idea whether they will be able to
retire. The market is saturated with fund *selection* and starved of financial
*planning*. The consequences are visible in the data every platform holds and
few publish:

- portfolios of eight to fifteen schemes that hold substantially the same
  thirty companies
- Regular plans held for a decade because nobody explained the expense
  difference in rupees
- SIPs stopped in the third month of every drawdown and restarted after the
  recovery
- money needed for a house deposit in eighteen months sitting in a small-cap
  fund
- switches made on one-year league tables, each one crystallising tax

None of these is a fund-selection failure. Every one is a planning, behaviour
or disclosure failure, and none is solved by a better ranking.

## 2. What we are building

A goal-first investment operating system that behaves like a disciplined
adviser: it asks what the money is for before it says what to buy, it monitors
continuously without reacting constantly, it explains every conclusion, and it
refuses to act on evidence it does not have.

It borrows the *machinery* of an algorithmic trading platform — continuous
monitoring, rules engines, signals, risk controls, scenario modelling, event
detection — and points it at the opposite objective. A trading platform
optimises for extracting return from price movement. This optimises for the
probability that a named human reaches a named goal, at acceptable risk, after
tax, cost and their own worst instincts.

## 3. What we are explicitly not building

| Not this | Because |
|---|---|
| A fund screener with filters | Choice is not advice. A twenty-fund result list is an abdication. |
| A returns leaderboard | Last year's ranking is the weakest available predictor of next year's. |
| A trading app for funds | Mutual funds are not positions. There is no stop-loss here, deliberately. |
| A robo-advisor that auto-rebalances | Per-transaction consent is required, and automated execution is prohibited. |
| A distribution funnel with an advice skin | Commercial terms are structurally excluded from ranking, and the exclusion is tested. |
| An AI that answers anything | The copilot refuses questions it has no verified basis for. |

## 4. Users

**Primary — the accumulating salaried investor.** 28–45, ₹1–5 lakh monthly
household income, one to three goals, some existing funds bought unsystematically,
moderate financial literacy, high anxiety during drawdowns. Wants to know whether
they are on track and what to do next — in that order.

**Secondary — the self-directed experienced investor.** Wants the analytics:
rolling returns, overlap look-through, tax-aware sequencing, scenario modelling.
Will not tolerate being talked down to and will leave if the product is a funnel.

**Tertiary — the human adviser.** Uses the platform's analysis and audit trail
to serve clients. Every escalation path in the product terminates here.

**Explicitly not a user:** the trader. Nothing here rewards frequency, and the
product will feel obstructive to anyone looking for it to.

## 5. Product principles

1. **Goals before funds.** The first question is never "which fund?".
2. **Ranges, never point estimates.** Every projection is conservative, base and
   optimistic, plus stress. A single number is a lie about certainty.
3. **The contribution is the lever.** A 10% annual step-up moves goal probability
   further than any switch. Say so, repeatedly, even though it sells nothing.
4. **Every recommendation is explainable, cited and reversible in reasoning.**
   Including a "what could make this wrong" section, always.
5. **Inaction is a legitimate recommendation** and usually the right one.
6. **Friction where it protects, none where it does not.** Panic selling gets a
   cooling-off period. Increasing a SIP gets one tap.
7. **Say "I don't know."** Refusing is always available and never penalised.

## 6. Functional requirements

### 6.1 Onboarding and profile
- Ten Tier-1 questions produce a usable plan; Tiers 2 and 3 are asked later,
  each with a stated reason for the asking.
- Profile is versioned; every recommendation pins a version and a fingerprint.
- A profile stale beyond 180 days blocks material recommendations until confirmed.

### 6.2 Financial health
- 0–100 across ten weighted dimensions, capped when a foundation is missing:
  no emergency fund caps the score at 70, thin insurance at 75, regardless of
  portfolio performance.
- Every dimension pairs a finding with one concrete action.
- Unknown dimensions are reported as unknown and reduce coverage, never scored zero.

### 6.3 Goals
- Unlimited goals, each with type, inflation-adjusted target, horizon, priority,
  and flags for whether amount and date are negotiable.
- Probability of success from a closed-form moment-matched lognormal — exactly
  reproducible years later for audit.
- Required return, required contribution, shortfall and surplus for every goal.

### 6.4 Risk
- Three separately computed dimensions: capacity (derived from facts), tolerance
  (asked, then adjusted for observed behaviour), requirement (arithmetic from the
  goal).
- Binding band is `min(capacity, tolerance)`. Requirement never raises it.
- Conflicts are named and explained. The product never emits a single risk label.

### 6.5 Fund data and intelligence
- Multi-source ingestion with a published hierarchy; disagreements recorded, not
  silently resolved.
- Data quality scored 0–100 per scheme; below 70 a scheme is not scored, not
  recommended, not shown as known.
- Fund score 0–100 over five pillars, within SEBI category peer groups only,
  with a separate weight map for passive schemes.

### 6.6 Selection and construction
- Eligibility gates run before ranking and remove rather than down-weight.
- Portfolios built in sleeves: core, growth, stability, diversifier. Not every
  goal gets every sleeve.
- Hard ceilings: equity by risk band, small-cap share of equity, single fund,
  single AMC, sector, and total fund count.

### 6.7 Monitoring and signals
- Seven states: HOLD, WATCH, REVIEW, REBALANCE, SWITCH_REVIEW, REDUCE, EXIT_REVIEW.
- No single trigger produces an action state. Action requires three independent
  triggers or one material event.
- There is no trigger that fires on market or NAV decline. This is a designed
  absence, listed explicitly in the rule-set under `non_triggers`.
- Dampeners (exit load, tax cost, recent action, thin history, marginal
  alternative) subtract from the score.

### 6.8 Switching and exits
- Switch benefit is net of tax, exit load and friction, with a breakeven in years.
- Score differences convert to expected return at a deliberately conservative
  factor; cost differences count at full value because they are certain.
- Six exit types, none of them price-based. Every triggered exit carries
  counter-arguments.

### 6.9 Tax
- Versioned rules with effective dates, sources and verification status.
- FIFO lot accounting; annual exemption tracked at investor level.
- Unverified rule → refusal, not an estimate. Missing marginal rate → a question.
- Surcharge unmodelled, so every figure is labelled a lower bound.

### 6.10 Behaviour, alerts and copilot
- Six guardrails; friction and questions, never refusal to act.
- Alert budget: three a week, eight a month, RED exempt. Eight mandatory fields
  or the alert is not sent.
- Copilot answers in the mandated nine-section format, grounded in a fact pack,
  with numeric verification on any model-generated prose.

## 7. Non-functional requirements

| Area | Requirement |
|---|---|
| Correctness | Money in `Decimal`, quantised to paise. Floats only for statistics. |
| Reproducibility | Same inputs, same outputs, forever. No randomness in any investor-facing figure. |
| Auditability | Every recommendation replayable from its pinned versions and snapshots. |
| Availability | Planning and analytics degrade gracefully; a model outage never blocks an answer. |
| Latency | Recommendation under 3s p95; goal projection under 300ms. |
| Privacy | DPDP-aligned consent, purpose limitation, retention and erasure. |
| Security | See M. Financial data is treated as the most sensitive class. |

## 8. Out of scope for v1

Direct equity, PMS, AIF, insurance sales, credit products, international
brokerage, intraday anything, and any feature whose success metric is
transaction count.

## 9. Success

Measured in outcomes and behaviour, never in activity. See
[Business Model](S-BUSINESS-MODEL.md) §5 for the full metric tree; the four that
matter most:

1. Median goal success probability across the active book, and its trend.
2. SIP continuation rate through a drawdown of 15% or worse.
3. Unnecessary switches avoided (signals raised where the net-of-cost analysis
   said hold).
4. Recommendation reversal rate — how often we tell someone the opposite of what
   we told them within twelve months.

## 10. The honest constraints

- **We are not registered.** Until SEBI IA registration exists, personalised
  advice cannot ship. The engines run; the advice gate is closed.
- **Several tax rules are unverified.** They are marked, and the engine refuses
  to compute with them rather than estimating.
- **Surcharge is not modelled.** Every tax figure is a lower bound and says so.
- **Capital market assumptions are committee judgement**, not fact, and are
  published as such.
- **The fund score rests on history**, which is not evidence about the future.
  The product says this on the screen, not only in a footnote.
