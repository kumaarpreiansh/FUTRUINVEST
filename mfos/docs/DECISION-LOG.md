# Decision Log

Design decisions where the obvious choice was wrong, or where two defensible
options existed and one was picked. Recorded so the reasoning survives the
people who made it.

---

## D1 — The risk-o-meter is not the primary risk control

**Decision:** Membership of the goal's target allocation is the suitability gate.
The risk-o-meter binds only for schemes asked about outside the plan.

**Why:** The first implementation used the risk-o-meter as a hard per-scheme
gate — a MODERATE investor could not hold a scheme rated above HIGH. Running it
produced an absurd result: *every* equity scheme in the universe was rejected,
including large-cap funds, because in the Indian market nearly every equity
scheme is labelled "Very High". Under that design, equity was uninvestable for
anyone below AGGRESSIVE.

The risk-o-meter is a product-labelling disclosure, not a discriminator within
equity. The control that actually binds is the equity ceiling per risk band,
applied at portfolio level in `target_allocation()`, plus the minimum-horizon
rules per asset class.

**Consequence:** The target allocation is itself built under the investor's
equity ceiling, so an asset class appearing in it has already been judged
appropriate. The risk-o-meter gate then applies exactly where it is useful — an
investor searching for a small-cap fund that has no place in their plan.

**Open:** L13 in the [Legal Review Register](LEGAL-REVIEW-REGISTER.md). Counsel
must confirm this is defensible as a suitability control.

---

## D2 — Closed-form goal probability, not Monte Carlo

**Decision:** Moment-matched lognormal, computed in closed form.

**Why:** An investor is told "84%". Eight years later, in a dispute, that figure
must be reproducible exactly. Monte Carlo with a stored seed is reproducible only
while the RNG implementation is stable across language and library versions —
which is not a property anyone should bet a regulatory defence on.

**Cost:** The final moment-matching step is an approximation, and path-dependent
features (a glide path, a mid-horizon contribution change) are handled by
time-weighted assumptions rather than simulated paths.

**Accepted** because the approximation is documented, bounded, and published
with the projection, while the reproducibility is absolute.

---

## D3 — Trigger predicates in code, thresholds in config

**Decision:** The YAML holds weights, thresholds and parameters. The predicate
that evaluates them is reviewed Python, keyed by trigger ID.

**Rejected:** Evaluating condition strings from config (`"rolling_3y_rate < 0.40"`).
More configurable, and considerably less safe: a config change could then alter
logic without code review, and a typo would fail at runtime rather than at build.

**Consequence:** Adding a trigger requires both a YAML entry and an
implementation. `test_signal_triggers_all_have_implementations` fails the build
if either is missing, so the two cannot drift apart.

---

## D4 — Performance alone never reaches EXIT_REVIEW

**Decision:** The EXIT_REVIEW band requires a material event or a trigger that
explicitly emits it. Pure performance deterioration is capped at SWITCH_REVIEW,
regardless of score.

**Why:** The first implementation let five performance triggers reach score 80
and therefore EXIT_REVIEW. Reading the output made the error obvious: the engine
was recommending *leaving an asset class* because a *fund* was weak. Those are
different decisions. A weak fund is a reason to change the fund.

**Implementation:** `requires_material_event_or_emitter: true` on the
EXIT_REVIEW band, with the suppression reason shown to the investor:

> Every trigger was about how the scheme has performed — nothing changed about
> the scheme itself or about your goal. A weak fund is a reason to change the
> fund, not to leave the asset class.

---

## D5 — Queued alerts count against the budget

**Decision:** Any alert that will reach the investor — immediately or in a
digest — is recorded and counts toward de-duplication and the budget. Only
suppressed alerts leave no trace.

**Why:** The first implementation only recorded alerts sent immediately. Since
most alerts are queued to a digest, de-duplication never fired: the same alert
could be queued repeatedly and would appear multiple times in one digest.

**Principle:** the budget protects the investor's attention, and a digest
consumes attention.

---

## D6 — An empty portfolio returns BUILD, not REBALANCE

**Decision:** `rebalance_plan()` short-circuits on an empty portfolio.

**Why:** Arithmetically, a portfolio holding nothing is 100% away from every
target weight, so the drift check fired and produced a rebalance recommendation
for someone with nothing to rebalance. The answer is to build it.

Small, but characteristic: the arithmetic was right and the answer was wrong.

---

## D7 — "Unattainable" is judged against the best asset class

**Decision:** A goal is flagged `GOAL_UNATTAINABLE_AT_ANY_RISK` only when its
required return exceeds the highest base return among all equity classes.

**Why:** The first version compared against flexi-cap (11.0%), which flagged a
goal needing 11.0% as impossible even though small-cap assumptions run to 12.0%.
Branding a goal impossible is a serious statement and should only be made when no
real allocation could reach it.

**Note:** a 100%-single-asset-class portfolio is not an allocation anyone would
be given. The test is deliberately generous, because the consequence of a false
"impossible" is worse than the consequence of a false "difficult" — the latter
still produces the REQUIREMENT_EXCEEDS_CAPACITY conflict and the same advice.

---

## D8 — Numeric grounding compares absolute values

**Decision:** `verify_numbers()` matches on absolute value, and lets small
integers 0–100 through.

**Why:** Prose legitimately turns a stored `-12.4` into "fell 12.4%" — the sign
is carried by the words. Without the absolute comparison, correct model output
was rejected and the fallback fired on every well-formed answer.

Small integers pass because they are overwhelmingly counts, ordinals and
percentages already present. A check that fires constantly on correct output
gets switched off, and a guard that has been switched off protects nothing.

**Not weakened:** any number outside the fact pack that is not a small integer
is still rejected, and the model still cannot introduce a return, a NAV or a tax
figure.

---

## D9 — Prohibited scoring inputs are named by their real field names

**Decision:** `prohibited_inputs` lists `distributor_commission_pct` and
`amc_relationship` — the actual attributes on the scheme record.

**Why:** The first version listed conceptual names (`distributor_commission`).
Conceptual names read well and cannot be checked. Real field names let the test
assert the exclusion mechanically, which turns a policy into a control.

---

## D10 — The compliance halt is a 200, not an error

**Decision:** A pipeline halt returns a successful response with `status:
HALTED` and a reason.

**Why:** A halt is a legitimate outcome, not a failure. "I cannot advise you
because we are not registered, but here is the analysis" is a *useful* answer,
and treating it as an error would push clients toward retry logic and error
pages rather than toward rendering the explanation.

---

## D11 — The execution endpoint exists and is permanently blocked

**Decision:** `POST /v1/orders` and the `transaction_order` table both exist, and
both are gated closed.

**Why:** Having the boundary present and refusing is better than having it
absent. The contract is reviewable, the controls are testable against it, and
enabling execution later becomes a configuration change against controls that
already exist rather than a project that builds them under time pressure.

`test_execution_stage_is_always_disabled` asserts it stays closed.

---

## D12 — Sandbox mode is in-memory and refuses in production

**Decision:** `enable_sandbox_advisory()` fakes registration in memory only,
writes nothing to disk, refuses when `build_stage == "production"`, and does not
touch the execution, auto-execution, withdrawal or commission gates.

**Why:** The full sixteen-stage pipeline had to be demonstrable and testable, but
the mechanism that makes that possible must not be the mechanism that fakes
registration in production. Two tests hold the line:
`test_sandbox_refused_on_production_build` and
`test_sandbox_does_not_open_execution_gates`.

---

## D13 — Foundation caps on the financial health score

**Decision:** No emergency fund caps the score at 70; thin insurance caps at 75 —
regardless of portfolio performance.

**Why:** An investor with an excellent portfolio and no cash buffer is one
medical event away from liquidating that portfolio at the worst possible moment.
A score that reads "82 — doing well" is actively misleading in that situation.

**Consequence:** the product tells some would-be investors to stop investing more
and build a buffer first, which sells nothing and is the correct advice.

---

## D14 — Money is Decimal, statistics are float

**Decision:** `Decimal` quantised to paise for every rupee amount; `float` for
volatility, correlation and percentile.

**Why:** Float arithmetic on money accumulates error that shows up in
reconciliation and in tax computation. Decimal arithmetic on statistics is slow
and buys nothing, since a volatility estimate is uncertain in its second digit
anyway.

**Boundary:** the conversion happens at the edge of `metrics.py`, and every
figure that reaches an investor or a tax computation is `Decimal`.
