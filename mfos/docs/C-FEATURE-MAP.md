# C. Feature Map

Every feature against the ten-point template: user problem, user experience,
business logic, AI logic, data required, regulatory requirement, risk,
safeguard, audit requirement, success metric.

Implementation column points at the module that actually does it.

---

## F1 — Progressive investor profiling
*Implementation: `profile.py`*

| | |
|---|---|
| **User problem** | Fifty questions before any value is delivered. Most people abandon at question twelve. |
| **User experience** | Three questions produce a first output. Ten produce a plan. Everything else is asked later, always with a stated reason. |
| **Business logic** | Three tiers. Tier 1 gates recommendations. Missing fields ranked by how much each changes the answer. Any material edit bumps the profile version. |
| **AI logic** | Rules-based gap detector (`profile_gap_detector` v0.9.0). It selects which question to ask next; it never infers a missing value and proceeds. |
| **Data required** | Investor-supplied. No inference from third-party data without consent. |
| **Regulatory** | SEBI-IA-SUITABILITY-001 — advice requires risk profiling and suitability. |
| **Risk** | An investor guesses at a number to get past a screen, and the plan is built on fiction. |
| **Safeguard** | Every number is shown back in context ("₹37,000 monthly surplus — does that look right?"). Blocking gaps raise `InsufficientData` with the questions attached, rather than defaulting. |
| **Audit** | Profile version + SHA-256 fingerprint pinned into every recommendation. |
| **Metric** | Tier-1 completion rate; Tier-2 completion within 30 days; profile refresh rate at 12 months. |

---

## F2 — Financial Health Score
*Implementation: `health.py`*

| | |
|---|---|
| **User problem** | "Am I doing okay?" has no answer anywhere in the market that is not a product pitch. |
| **User experience** | 0–100, ten dimensions, one action. Caps shown with their reason. |
| **Business logic** | Weighted mean over available dimensions, renormalised for gaps. Foundation caps: emergency fund below three months caps at 70; thin insurance caps at 75. |
| **AI logic** | None. Deterministic banding throughout. |
| **Data required** | Tier 1 + Tier 2 profile; portfolio stats where available. |
| **Regulatory** | None directly. Insurance guidance must stay generic — recommending a specific policy is a different licence. |
| **Risk** | A score that flatters the investor sells better and helps less. |
| **Safeguard** | Caps are non-negotiable and stated. Unknown dimensions reported as unknown, reducing coverage rather than scoring zero. |
| **Audit** | Score, breakdown, caps and coverage stored with each computation. |
| **Metric** | Score improvement over 12 months, especially emergency fund and savings rate. |

---

## F3 — Goal Engine
*Implementation: `goals.py`*

| | |
|---|---|
| **User problem** | People save towards vague intentions and discover the shortfall too late to fix it. |
| **User experience** | Unlimited goals; each shows inflation-adjusted target, probability, required return, required contribution, shortfall. |
| **Business logic** | Closed-form moment-matched lognormal terminal wealth. No simulation — the number must be reproducible for audit years later. |
| **AI logic** | `goal_probability` v1.0.0, DETERMINISTIC. Published method and published limitations. |
| **Data required** | Goal parameters, contribution schedule, capital market assumptions. |
| **Regulatory** | COMM-NO-GUARANTEE-001; DISC-PROJECTION-001 attached to every projection. |
| **Risk** | A probability reads as a promise. |
| **Safeguard** | Bands in words alongside the number; three scenarios always; the disclosure is part of the payload, not the page furniture. |
| **Audit** | Assumption set, ruleset version and all inputs stored per projection. |
| **Metric** | Median goal probability across the book; share of goals moving from AT_RISK to ON_TRACK. |

---

## F4 — Scenario and stress engine
*Implementation: `scenarios.py`*

| | |
|---|---|
| **User problem** | "What if the market crashes?" is the question behind most panic selling, and it is usually unanswered until it happens. |
| **User experience** | Three scenarios plus seven stresses, each reported as a change in goal probability. |
| **Business logic** | Portfolio volatility computed through a published correlation matrix, not summed. Stresses modelled as events on top of base assumptions. |
| **AI logic** | None. |
| **Data required** | Allocation, correlations, capital market assumptions. |
| **Regulatory** | DISC-PROJECTION-001. |
| **Risk** | A stress test that looks frightening in rupees causes the behaviour it was meant to prevent. |
| **Safeguard** | Results expressed in probability, not in rupees lost, with a recovery note on each. |
| **Audit** | Scenario set and version stored with each run. |
| **Metric** | Share of investors who have viewed a stress result before their first real drawdown. |

---

## F5 — Three-dimensional risk engine
*Implementation: `risk.py`*

| | |
|---|---|
| **User problem** | One risk label collapses three different questions and loses the only interesting information — where they disagree. |
| **User experience** | Three bands side by side, the binding one named, and every conflict explained with a resolution. |
| **Business logic** | Capacity derived from facts; tolerance asked then adjusted for observed behaviour; requirement from goal arithmetic. Binding = min(capacity, tolerance). Requirement never raises it. |
| **AI logic** | None. Deterministic scoring. |
| **Data required** | Buffer, EMIs, dependants, income stability, savings rate, horizon; tolerance answers; past behaviour; required return. |
| **Regulatory** | SEBI-IA-SUITABILITY-001. |
| **Risk** | An investor argues their way into a higher band. |
| **Safeguard** | Capacity is derived, not asked, so it cannot be talked up. The equity ceiling is a hard constraint in `target_allocation`. |
| **Audit** | All three assessments, factors, effects and conflicts stored. |
| **Metric** | Share of portfolios inside their band; drawdown-period SIP continuation by band. |

---

## F6 — Mutual fund data layer
*Implementation: `funds.py`*

| | |
|---|---|
| **User problem** | Invisible: bad data produces confident, wrong advice. |
| **User experience** | Mostly invisible, except when it says "I don't have sufficient verified data on this scheme". |
| **Business logic** | Multi-source observations per field; published hierarchy resolves; disagreements recorded. Quality scored on completeness, freshness, source rank, cross-source agreement, history and internal consistency. |
| **AI logic** | `anomaly_detector` v0.1.0 — registered, NOT VALIDATED, and blocked by the gateway until it is. |
| **Data required** | AMFI, RTA, AMC documents, exchange feeds, validated vendors. |
| **Regulatory** | SEBI-MF-CATEGORY-001 for peer grouping; SEBI-MF-RISKOMETER-001 for disclosure. |
| **Risk** | Single-vendor dependence with no cross-check. |
| **Safeguard** | Below the quality floor a scheme is not scored, not recommended and not displayed as known. |
| **Audit** | Full provenance per field: value, source, timestamp, URL, independent source count, disagreements. |
| **Metric** | Median data quality; single-source field count; disagreement rate by vendor. |

---

## F7 — Fund Intelligence (0–100 score)
*Implementation: `scoring.py`* — methodology in [F](F-FUND-SCORING-METHODOLOGY.md)

| | |
|---|---|
| **User problem** | Trailing-return rankings select for whatever style was in favour last year. |
| **User experience** | One score, five pillars, each openable. Bands in words. Provisional label under three years of history. |
| **Business logic** | Weighted pillars within SEBI category peer groups; separate weight map for passive; disqualifiers before scoring. |
| **AI logic** | None. Deterministic. |
| **Data required** | NAV and benchmark history, expense, AUM, holdings, manager tenure, turnover, market-cap split. |
| **Regulatory** | SEBI-IA-CONFLICT-001; COMM-PAST-PERF-001. |
| **Risk** | Commercial terms leaking into ranking. |
| **Safeguard** | Prohibited inputs named in the rule-set by their real field names, and verified by a test that re-scores the universe with commissions perturbed and asserts nothing moves. |
| **Audit** | Score, pillars, components, coverage, peer group, metrics and ruleset version stored. |
| **Metric** | Score stability over time; correlation between score and subsequent risk-adjusted outcome. |

---

## F8 — Selection engine
*Implementation: `selection.py`*

| | |
|---|---|
| **User problem** | "Best fund" is the wrong question; it has no answer without an investor attached. |
| **User experience** | Two to three candidates per sleeve with quality and fit shown separately. |
| **Business logic** | Eligibility gates (asset class in plan, data quality, horizon, history, risk-o-meter for out-of-plan) run first and remove. Ranking is 45% quality, 55% fit. |
| **AI logic** | None. |
| **Data required** | Scheme records, scores, investor profile, risk assessment, existing portfolio. |
| **Regulatory** | SEBI-IA-SUITABILITY-001; SEBI-IA-CONFLICT-001; AMFI-PLAN-DIRECT-REGULAR-001. |
| **Risk** | Suitability implemented as a weight, which a high enough score can outvote. |
| **Safeguard** | Suitability is a filter, not a weight. Rejections are recorded with the gate that fired. |
| **Audit** | Shortlist, rejections with gates, and conflict disclosures stored. |
| **Metric** | Recommendation acceptance; 12-month reversal rate. |

---

## F9 — Portfolio construction
*Implementation: `portfolio.py`* — methodology in [G](G-PORTFOLIO-CONSTRUCTION.md)

| | |
|---|---|
| **User problem** | Portfolios accumulate by purchase rather than by design. |
| **User experience** | Four sleeves, each with a stated job. Empty sleeves are shown as deliberately empty. |
| **Business logic** | Policy by goal type × horizon × band, capped at the equity ceiling, small-cap limited, glide-path adjusted. |
| **AI logic** | None. |
| **Data required** | Goal, risk assessment, allocation policy. |
| **Regulatory** | SEBI-IA-SUITABILITY-001. |
| **Risk** | Forcing every asset class into every portfolio. |
| **Safeguard** | Policies with empty sleeves exist and are used (a three-year goal has no growth sleeve). |
| **Audit** | Policy ID, weights, cap applied, ruleset version. |
| **Metric** | Share of portfolios inside every hygiene limit. |

---

## F10 — Overlap and concentration
*Implementation: `portfolio.py`*

| | |
|---|---|
| **User problem** | Five funds, one portfolio, two expense ratios. |
| **User experience** | "You own 5 schemes and the two most similar overlap 62%" — with a verdict on whether it matters. |
| **Business logic** | Sum of minimum weights of shared securities. Same-category overlap is PROBLEMATIC; cross-category is EXPECTED. Look-through concentration by AMC, sector, market cap and security. |
| **AI logic** | None. |
| **Data required** | Scheme holdings, sector and market-cap splits. |
| **Regulatory** | None directly. |
| **Risk** | Alarming investors about harmless overlap and triggering churn. |
| **Safeguard** | Every finding carries a verdict and an explanation of whether it is actually a problem. |
| **Audit** | Matrix, concentration and findings stored. |
| **Metric** | Share of portfolios above the overlap limit, and its trend. |

---

## F11 — SIP engine
*Implementation: `sip.py`*

| | |
|---|---|
| **User problem** | People optimise fund choice, which barely matters, and ignore contribution level, which decides everything. |
| **User experience** | Step-up ladder priced in goal probability. Pause presented as strictly better than stopping. |
| **Business logic** | Full lifecycle with history. Income-linked sizing. Inverse solve for required contribution at a target probability. |
| **AI logic** | None. |
| **Data required** | Goal, allocation, assumptions, income. |
| **Regulatory** | DISC-PROJECTION-001. |
| **Risk** | Committing more than the surplus supports, leading to a stop later. |
| **Safeguard** | Required contribution compared against actual surplus; unreachable targets stated as such. |
| **Metric** | Step-up adoption; SIP continuation through drawdowns. |

---

## F12 — Signal engine
*Implementation: `signals.py`* — rules in `config/signals/signal_rules.v1.yaml`

| | |
|---|---|
| **User problem** | Either no monitoring, or monitoring that fires on every market move. |
| **User experience** | Seven states. Most evaluations are HOLD and silent. |
| **Business logic** | Weighted triggers, persistence confirmation, dampeners, minimum-trigger rule, and EXIT_REVIEW gated behind a material event or an emitting trigger. |
| **AI logic** | None. Predicates in reviewed code; thresholds in versioned config. |
| **Data required** | Rolling metrics, peer statistics, scheme events, portfolio state, goal state, profile changes. |
| **Regulatory** | None directly; feeds recommendations that are gated. |
| **Risk** | A signal engine that produces action becomes a churn engine. |
| **Safeguard** | No single trigger reaches an action state. No trigger fires on market decline — the omission is listed in the rule-set under `non_triggers` so it is reviewable. |
| **Audit** | Triggers, evidence, dampeners, suppression reason and ruleset version stored. |
| **Metric** | Signals per holding per year; share of action signals accepted; churn rate. |

---

## F13 — Switch intelligence
*Implementation: `switching.py`* — methodology in [H](H-SWITCHING-EXIT-METHODOLOGY.md)

| | |
|---|---|
| **User problem** | Switching feels free and is not. |
| **User experience** | Side-by-side comparison, net benefit, breakeven in years, and what would make it wrong. |
| **Business logic** | Expected improvement minus tax, exit load and friction. Cost advantage at full value; quality advantage discounted hard. |
| **AI logic** | None. |
| **Data required** | Both scheme records, both scores, holding lots, tax rules, goal horizon. |
| **Regulatory** | TAX-SWITCH-IS-REDEMPTION-001; SEBI-IA-CONSENT-001 before any execution. |
| **Risk** | The engine becomes a churn machine. |
| **Safeguard** | Minimum score gap of 8 points; net benefit must exceed 1% of the holding; counter-arguments mandatory. |
| **Audit** | Full analysis with tax detail stored. |
| **Metric** | Switch recommendations per investor per year (target: low); realised benefit against predicted. |

---

## F14 — Exit intelligence
*Implementation: `exits.py`*

| | |
|---|---|
| **User problem** | Investors exit on price and stay on hope, which is exactly backwards. |
| **User experience** | Six named exit types. A price fall returns "no exit condition". |
| **Business logic** | Evaluated in order of decisiveness: goal, allocation, risk, thesis, quality, tax-aware. |
| **AI logic** | None. |
| **Data required** | Goal state, liquidity declarations, profile changes, scheme events, signal state, tax estimate. |
| **Regulatory** | SEBI-IA-CONSENT-001. |
| **Risk** | An exit review reads as an instruction to sell. |
| **Safeguard** | Counter-arguments mandatory on every triggered exit; explicit statement that a market fall is not an exit condition. |
| **Audit** | Exit type, rationale, counter-arguments, route and confidence stored. |
| **Metric** | Share of exits that were goal-driven rather than market-driven. |

---

## F15 — Rebalancing
*Implementation: `portfolio.py`*

| | |
|---|---|
| **User problem** | Calendar rebalancing generates tax for no gain. |
| **User experience** | Usually "redirect your contributions", rarely "sell". |
| **Business logic** | Tolerance bands (absolute and relative, tighter wins), minimum six months between rebalances, cashflow preferred, taxable sale requires drift beyond 1.5× the band plus a positive net-of-tax benefit plus goal proximity. |
| **AI logic** | None. |
| **Data required** | Current and target weights, inflow, last rebalance date, goal horizon, tax estimate. |
| **Regulatory** | Tax rules for any taxable leg. |
| **Risk** | Over-rebalancing. |
| **Safeguard** | Empty portfolios return BUILD, not REBALANCE. Recent rebalances DEFER. |
| **Metric** | Taxable rebalances per portfolio per year (target: near zero). |

---

## F16 — Behavioural guardrails
*Implementation: `behaviour.py`*

| | |
|---|---|
| **User problem** | The largest destroyer of retail returns is the investor's own reflex. |
| **User experience** | During a drawdown-driven sell: five questions, goal probability before and after, the tax that would be crystallised, and on a deep fall a 24-hour cooling-off. |
| **Business logic** | Six guardrails: panic, FOMO, overtrading, recency, loss aversion, performance chasing. |
| **AI logic** | None in the gate. The copilot may explain an intervention, grounded. |
| **Data required** | Drawdown, intended action, action history, holding return, stated reason. |
| **Regulatory** | Investor-protection principle; COMM-NO-GUARANTEE-001 on all copy. |
| **Risk** | Paternalism. Blocking someone from their own money is both wrong and probably unlawful. |
| **Safeguard** | `proceed` is always `True`. Friction, information and a question — never refusal. |
| **Audit** | Interventions shown, acknowledgements, cooling-off applied, final action. |
| **Metric** | Panic sales avoided; SIP continuation through drawdowns. |

---

## F17 — Alert engine
*Implementation: `alerts.py`*

| | |
|---|---|
| **User problem** | Notification fatigue means the one alert that mattered is ignored. |
| **User experience** | At most three a week. Every alert says what happens if you do nothing. |
| **Business logic** | Materiality, de-duplication, per-severity and global budgets, quiet hours, digest cadences. RED exempt from budget and quiet hours. |
| **AI logic** | Copy may be phrased by the explainer model, grounded and language-scanned. |
| **Data required** | Signals, portfolio state, goal state, send history. |
| **Regulatory** | COMM-NO-GUARANTEE-001; COMM-PAST-PERF-001. |
| **Risk** | Alerts become a re-engagement mechanic. |
| **Safeguard** | Eight mandatory fields or it is not sent. Prohibited patterns listed in policy. No alert on market movement. |
| **Audit** | Every dispatch decision and its reason recorded. |
| **Metric** | Alerts per investor per month; open rate; action rate on RED. |

---

## F18 — AI Copilot
*Implementation: `copilot.py`* — architecture in [D](D-AI-ARCHITECTURE.md)

| | |
|---|---|
| **User problem** | Investors have questions at 11pm and no one to ask. |
| **User experience** | Nine-section answers in a fixed shape, with sources. |
| **Business logic** | Intent → deterministic answer builder → fact pack → optional LLM prose → numeric verification → language scan. |
| **AI logic** | `explainer` v1.0.0, STRICT_CONTEXT_ONLY. Prohibited from producing numbers, deciding recommendations, asserting regulatory facts, or naming schemes outside the shortlist. |
| **Data required** | Fact pack assembled from deterministic engines only. |
| **Regulatory** | SEBI-AI-GOVERNANCE-001; COMM-NO-GUARANTEE-001; SEBI-IA-REG-001 gates personalised answers. |
| **Risk** | Hallucinated figures presented with an authoritative tone. |
| **Safeguard** | Numeric grounding check rejects any number absent from the fact pack; language scan blocks prohibited phrasing; deterministic fallback on any failure. |
| **Audit** | Model ID, version, checks run, whether fallback was used, and why. |
| **Metric** | Ungrounded-number rate (must be zero); fallback rate; answer usefulness rating. |

---

## F19 — Tax engine
*Implementation: `tax.py`* — rules in `config/tax/`

| | |
|---|---|
| **User problem** | Tax is discovered after the transaction. |
| **User experience** | Value, cost, gain, holding period, taxable amount, estimated tax, exit load, net proceeds — before consent. |
| **Business logic** | FIFO lots, per-lot holding period, investor-level annual exemption, cess overlay, surcharge unmodelled. |
| **AI logic** | None. The LLM may never state a tax rule. |
| **Data required** | Lots, NAV, tax class, exit load bands, marginal rate, exemption already used. |
| **Regulatory** | Every rule in `tax_rules.IN.v2026-27.yaml`, each with source and verification status. |
| **Risk** | A wrong tax figure that the investor acts on. |
| **Safeguard** | Unverified rule → refusal. Missing marginal rate → a question. Surcharge unmodelled → every figure labelled a lower bound. |
| **Audit** | Rule IDs, ruleset version and all caveats stored with each estimate. |
| **Metric** | Estimate-versus-actual variance once real statements are available. |

---

## F20 — Compliance engine
*Implementation: `compliance.py`* — architecture in [I](I-COMPLIANCE-ARCHITECTURE.md)

| | |
|---|---|
| **User problem** | Invisible until it protects them. |
| **User experience** | Occasionally a withheld recommendation with a plain explanation of why. |
| **Business logic** | Reads the versioned rule repository. Fail-closed: an unknown rule blocks. Can only say no. |
| **AI logic** | None, by design. |
| **Data required** | The regulatory rule repository. |
| **Regulatory** | All of it. |
| **Risk** | Compliance as a checklist rather than a code path. |
| **Safeguard** | Sits in the pipeline at stage 9 and at the execution boundary; `raise_if_blocked()` is the only way past it. |
| **Audit** | Every rule evaluated, every block, every required disclosure. |
| **Metric** | Zero recommendations delivered while a blocking rule is unmet. |

---

## F21 — Confidence engine
*Implementation: `confidence.py`*

| | |
|---|---|
| **User problem** | Advice delivered with uniform confidence regardless of evidence. |
| **User experience** | A percentage with its computation shown, plus published method limitations. |
| **Business logic** | Six inputs, each only able to reduce from a cautious base. Capped at 96 — nothing here is certain. |
| **AI logic** | None. |
| **Data required** | Data quality, freshness, source count, signal balance, history depth, method limitations. |
| **Regulatory** | SEBI-AI-GOVERNANCE-001. |
| **Risk** | A confidence number that is itself unjustified. |
| **Safeguard** | Every input's contribution is shown. Below the minimum, no recommendation is produced at all. |
| **Audit** | Score, band, inputs and limitations stored. |
| **Metric** | Calibration: outcomes at each confidence band. |

---

## F22 — Audit trail
*Implementation: `audit.py`*

| | |
|---|---|
| **User problem** | Nobody can explain a decision made three years ago. |
| **User experience** | Full decision history, including declined recommendations. |
| **Business logic** | Append-only JSONL. Responses supersede rather than edit, linked by content hash. |
| **AI logic** | Models used are recorded, never consulted. |
| **Data required** | Everything: profile version, snapshots, rule versions, models, signals, confidence, response, action. |
| **Regulatory** | SEBI-AI-GOVERNANCE-001; general record-keeping obligations. |
| **Risk** | An audit trail that references live data rather than a snapshot, and so cannot be replayed. |
| **Safeguard** | Fund and market data are snapshotted at decision time, not referenced. Chain integrity verifiable. |
| **Audit** | This is the audit. |
| **Metric** | Replay success rate on sampled historical recommendations. |

---

## Deliberately absent features

| Not built | Why |
|---|---|
| Stop-loss on funds | Mutual funds are not positions. Price is not an exit condition. |
| Returns leaderboard | Rewards recency and drives performance chasing. |
| Social / copy investing | Someone else's portfolio is unsuitable for you by construction. |
| Automated rebalancing | Requires per-transaction consent that automation cannot give. |
| Push on market moves | The single most damaging notification a platform can send. |
| Streaks tied to transacting | Rewards frequency, which is the opposite of the objective. |
