# H. Switching and Exit Methodology

Implementation: `switching.py`, `exits.py`, `signals.py`.

## The premise

A switch is not a free move between two rows in a table. It is a redemption
(taxable, possibly load-bearing), a gap out of the market, and a reset of the
holding period — all paid today, in exchange for an improvement that is
expected, uncertain, and spread over years.

An exit is not a stop-loss. Mutual funds are not positions. A price fall is the
cost of the returns being sought, not a signal to leave.

Both engines are built to say **no** more often than yes, because the base rate
of beneficial switching in Indian retail portfolios is low and the base rate of
harmful switching is high.

## Part 1 — Switch Intelligence

### The equation

```
switching benefit = expected improvement
                  − tax cost
                  − exit load
                  − transaction friction
                  − behavioural and strategic risk
```

### Expected improvement, split by certainty

**Cost advantage** counts at full value. It is the only part of the case that
does not depend on a forecast: a 1.10 percentage point expense difference on a
₹8.6 lakh holding is ₹9,485 a year, every year, whatever the market does.

**Quality advantage** is discounted hard. Each point of combined-score
difference is assumed worth **3 basis points a year** — deliberately small,
because a 10-point score gap does not reliably deliver 10 points of return, and
pretending otherwise is how platforms end up churning portfolios.

The improvement is credited over **five years** at most, and over the goal
horizon if that is shorter. Beyond five years, the assumption that today's
quality difference persists is not defensible.

**Friction** of 15 basis points is charged against every switch, for the market
gap between redemption and reinvestment plus the reset of the holding period.

### The decision rule

| Condition | Result |
|---|---|
| Net benefit > 1% of holding **and** score gap ≥ 8 | SWITCH_REVIEW |
| Net benefit > 0 **and** cost advantage > 0.4pp | SWITCH_REVIEW, on cost alone |
| Otherwise | HOLD |

The 8-point minimum score gap exists because a smaller gap is inside the noise of
any scoring method. A 4-point difference is not evidence that the alternative is
better; it is evidence that the two are similar.

### Worked: Regular to Direct

```
Meridian Flexi Cap Regular → Meridian Flexi Cap Direct
  Expected improvement over 5y      ₹63,516
    of which cost advantage/yr       ₹8,536   (certain)
    of which quality advantage/yr    ₹4,167   (forecast, discounted)
  Tax                               ₹49,530
  Exit load                              ₹0
  Friction                           ₹1,164
  ────────────────────────────────────────
  Net benefit                       ₹12,822
  Breakeven                         4.0 years
  → SWITCH_REVIEW at 67% confidence
```

Recommended — but with the four-year breakeven stated prominently, because an
investor who might redeem within four years should not make this switch. Same
underlying portfolio, and it still takes four years for the cost saving to repay
the tax.

### Worked: a merely-better fund

```
Meridian Flexi Cap Direct → Halcyon Flexi Cap Direct
  Score gap                        4 points
  Net benefit                     −₹56,314
  → HOLD
  "The expected improvement does not cover the cost of making the change.
   Staying put is the better decision on these numbers."
```

Halcyon genuinely scores higher. The switch is still wrong, and the engine says
so — which is the behaviour that distinguishes an advice engine from a
distribution engine.

### Mandatory counter-arguments

Every switch analysis returns `what_could_make_this_wrong`, always including:

> A fund manager change, a reversal in the market regime, or the candidate's
> recent record being luck rather than skill would each undermine this case.

Plus, where applicable, the tax crystallised, the exit load, and the breakeven
if it exceeds three years.

### Side-by-side comparison

Eleven rows: overall score, consistency, risk, cost, expense ratio, max
drawdown, Sharpe, downside capture, rolling 3-year win rate, plan, AMC — each
marked with which direction is better. Enough for the investor to disagree with
the engine, which is the test of whether an explanation is real.

## Part 2 — Exit Intelligence

### Six reasons, none of them price

| Type | Condition | Route |
|---|---|---|
| **GOAL_EXIT** | Money needed within 6 months, or goal funded and within 3 years | Move to liquid in stages, oldest units first |
| **ALLOCATION_EXIT** | Horizon now below the asset class minimum | Shift to stability over 2–3 months |
| **RISK_EXIT** | Risk capacity band dropped | Reduce to the new ceiling — not a full exit |
| **THESIS_EXIT** | Material scheme change or mandate drift > 0.35 | Review a replacement before redeeming |
| **QUALITY_EXIT** | Signal ≥ SWITCH_REVIEW with 3+ triggers | Treat as a switch, not a redemption |
| **TAX_AWARE_EXIT** | Planned withdrawal due | Tranche across financial years |

Evaluated in that order — decisiveness first. A goal that has arrived outranks a
scheme that has deteriorated, because the money is needed either way.

### What is deliberately absent

There is no price-based exit. A 40% drawdown returns:

> None of the six exit conditions applies. Nothing about your goal, your
> circumstances or this scheme has changed enough to justify selling.

This is the single most important behaviour in the product, and it is asserted
by `test_price_fall_is_not_an_exit_condition`.

### Counter-arguments are mandatory

Every triggered exit carries them, always including:

> If the underlying reason is a fall in markets rather than a change in your
> circumstances or in the scheme, this is not an exit condition.

Plus the tax and exit load that would be crystallised, and a churn warning if
the holding was acted on within the last year.

### Tax-aware sequencing

`sequencing_plan()` orders lots oldest-first — more likely to qualify for
long-term treatment — and splits redemptions into tranches sized to sit within
one year's annual exemption where the timeline allows.

With its own caveat attached:

> Splitting across financial years only helps if the money is not needed sooner.
> Never delay a redemption you actually need in order to save tax.

Tax optimisation that compromises the goal is not optimisation.

## Part 3 — Why the signal engine feeds both

The signal engine decides *whether to look*. These engines decide *what to do*.

Three structural rules connect them:

**1. No single trigger produces an action.** Action requires three independent
triggers or one material event. This stops one bad quarter becoming a switch
recommendation.

**2. EXIT_REVIEW requires a material event or an emitting trigger.** Pure
performance deterioration, however many triggers fire, is capped at
SWITCH_REVIEW:

> Every trigger was about how the scheme has performed — nothing changed about
> the scheme itself or about your goal. A weak fund is a reason to change the
> fund, not to leave the asset class.

**3. Dampeners subtract.** Exit load (−15), tax cost above 3% of value (−20),
action on this holding within 12 months (−30), history under 36 months (−25),
alternative less than 8 points better (−25). An action has to be worth its own
cost before it is proposed.

## The failure mode this is all guarding against

A platform with a switch engine and a revenue model tied to transactions will
find reasons to switch. The guards are structural rather than cultural:

- Commercial fields are excluded from scoring, and the exclusion is tested.
- The score-to-return conversion factor is conservative and published.
- Tax and load are subtracted before, not after, the recommendation.
- Counter-arguments are a required field, so they cannot be dropped in a
  redesign.
- Recent action on a holding is a heavy dampener.
- Switch recommendations per investor per year is a monitored metric, and the
  target is low.

None of these depends on anyone remembering to be careful.
