# P. Test Strategy

176 tests over the engines. `cd mfos/tests && python3 -m unittest discover -s .`

## The organising idea

In a product that gives financial advice, the tests that matter most are not the
ones that prove a feature works. They are the ones that prove a **guarantee
cannot be broken by a future change**.

A conflict-of-interest policy in a document is a promise. A test that re-ranks
the entire fund universe with commissions reversed and fails the build if
anything moves is a control. This suite is built around the second kind.

## The invariants

Each of these is a claim the product makes to investors. Each has a test that
fails the build if it stops being true.

| Invariant | Test |
|---|---|
| Commercial terms never affect ranking | `test_no_commission_influence_on_ranking` |
| A falling market never produces a signal | `test_a_falling_market_is_not_a_trigger` |
| A price fall is never an exit condition | `test_price_fall_is_not_an_exit_condition` |
| One trigger never produces an action | `test_one_trigger_never_reaches_an_action_state` |
| Performance alone never reaches EXIT_REVIEW | `test_performance_alone_never_reaches_exit_review` |
| Execution is always disabled | `test_execution_stage_is_always_disabled` |
| Auto-execution and withdrawal gates stay closed | `test_locked_safety_gates_are_off` |
| The sandbox cannot open execution gates | `test_sandbox_does_not_open_execution_gates` |
| The platform never claims registration it lacks | `test_platform_does_not_claim_registration` |
| An unverified rule cannot produce a figure | `test_unverified_class_is_refused_not_guessed` |
| Guaranteed-return language is blocked | `test_prohibited_language_is_blocked` |
| The LLM cannot introduce a number | `test_llm_output_with_invented_numbers_is_rejected` |
| Consent cannot be reused or assumed | seven tests in `TestConsent` |
| Suitability removes rather than down-ranks | `test_suitability_removes_rather_than_downranks` |
| Confidence never reaches certainty | `test_confidence_never_reaches_certainty` |
| Audit rows supersede rather than edit | `test_response_supersedes_rather_than_edits` |
| Tax rates live in config, not code | `test_no_module_hardcodes_a_tax_rate` |
| Synthetic data stays labelled | `test_synthetic_data_is_labelled` |

## Layers

### 1. Configuration validity — `test_ruleset_and_config.py`

Runs before anything else, because every engine reads config. Asserts weights
sum to 100, allocation policies sum to 100, every regulatory rule has
provenance, every tax rule declares a verification status, every signal trigger
has an implemented predicate, every active LLM declares prohibited uses, and the
locked safety gates are off.

The trigger-implementation test is quietly important: it makes it impossible to
add a trigger to the YAML without implementing it, which would otherwise fail
silently at runtime.

### 2. Financial mathematics — `test_financial_core.py`

Known-answer tests where an answer is known: a doubling over ten years is
7.177% CAGR; ₹1,00,000 to ₹1,61,051 over five years is exactly 10% XIRR.

Property tests where it is not:
- more contribution raises probability
- more volatility at the same return does not raise probability, and widens
  p10–p90 in both directions
- probability stays within [0, 1] across the whole input range
- diversification produces portfolio volatility below the weighted sum of asset
  volatilities
- the glide path reduces equity and still sums to 100
- every stress test reduces or holds probability, never raises it

And refusal tests: CAGR under a year returns `None`, not an annualised guess;
XIRR without both signs returns `None`; short series return `None` rather than
zero.

### 3. Tax and risk — `test_tax_and_risk.py`

Tax: FIFO ordering, per-lot holding periods, the annual exemption applied once
and net of what is already used, exit load only on lots inside the load period,
net proceeds reconciling exactly, and every estimate citing its rule IDs and
ruleset version.

The refusal tests matter more than the arithmetic:
- an unverified rule class raises rather than estimating
- a slab-taxed scheme without the marginal rate asks a question
- the surcharge gap is surfaced as a lower-bound label, not hidden

Risk: capacity falls with a thinner buffer, more dependants, more debt;
tolerance is adjusted by observed behaviour with the stated score preserved
separately; the binding band is the lower of capacity and tolerance; **and
requirement never raises the ceiling** — the same profile with a 7% requirement
and a 13% requirement gets the same equity cap.

### 4. Funds, selection, portfolio — `test_funds_and_selection.py`

Data quality: missing critical fields fail the floor, stale NAV is penalised,
the source hierarchy resolves disagreements, an unusable scheme raises rather
than returning a guess.

Scoring: the commission test above; Direct beats its Regular twin on cost; skill
is detected above noise; the index fund scores well on cost and tracking; a
missing component reduces coverage rather than scoring zero.

Portfolio: the equity ceiling holds; allocations sum to 100; overlap uses
minimum weights not sums; same-category overlap is PROBLEMATIC and
cross-category is not; rebalancing prefers cashflow; an empty portfolio returns
BUILD; a recent rebalance defers.

### 5. Decisions — `test_decisions.py`

Signals: the full ladder from one trigger to a material event, dampeners
cancelling an action entirely, persistence requiring repeat observation,
allocation drift emitting REBALANCE rather than SWITCH.

Switching: a marginal improvement is rejected, Regular-to-Direct survives its
costs, tax and load are always subtracted, counter-arguments are mandatory.

Exits: all six types reachable, price fall reaching none of them, quality exit
requiring a multi-trigger signal.

Behaviour: panic adds friction but `proceed` stays `True`, the reality-check
questions are asked, calm contexts produce no friction at all.

Alerts: HOLD produces no alert, incomplete alerts are refused, duplicates are
suppressed, the budget defers, RED is exempt.

### 6. Governance and pipeline — `test_governance_and_pipeline.py`

Compliance, consent, confidence, copilot grounding, audit integrity, and the
pipeline end to end in both modes — halting at stage 9 as shipped, completing
all sixteen in sandbox with execution still disabled.

Plus cross-cutting safety: no hard-coded tax rates, disclaimers exist for every
mandatory ID, prohibited language list is populated, synthetic data is labelled.

## Testing refusal

Roughly a fifth of the suite asserts that the system **declines** to do
something. This is unusual and deliberate. In a normal product, refusing is an
error path. Here it is a feature, and an untested feature regresses.

Every refusal test checks two things: that it refuses, and that it explains why
in terms an investor could act on.

## Property-based testing

Used where the correct answer is a relationship rather than a value —
"more contribution never lowers probability" holds for every input, and asserting
it across a range catches sign errors that a single example would not.

Not used for tax, where the correct answer is exact and known cases are more
useful than generated ones.

## What is not tested here, and must be before launch

| Gap | Needed |
|---|---|
| Live data ingestion | Contract tests per feed, replay of a month of real data |
| Reconciliation | Against real RTA statements |
| Tax accuracy | Estimate vs real capital gains statements |
| Bias | Equivalent profiles differing only by name, gender, location — **PENDING, blocks LLM in production** |
| Load | Signal evaluation across a full book |
| Security | Penetration test, per [M](M-SECURITY-ARCHITECTURE.md) |
| Accessibility | WCAG 2.2 AA audit |
| Copy | Legal review of every investor-facing string |

The bias suite is marked PENDING in the model registry rather than quietly
omitted, so it appears in every governance review until it exists.

## Regression discipline

Every production defect gets a test that fails before the fix and passes after.
Every rule-set change runs the full suite against both the old and the new
version, because a threshold change that alters a hundred existing
recommendations is a product decision, not a config tweak.

## Determinism

Every investor-facing figure is deterministic — no randomness, no wall-clock
dependence in the maths. This is what makes replay-based regression testing
possible: a stored recommendation from any date can be recomputed and compared
exactly.

Synthetic data uses seeded generators, so the test universe is identical on
every machine and every run.
