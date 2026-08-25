# G. Portfolio Construction Methodology

Implementation: `portfolio.py`. Policies: `config/allocation/allocation_policy.v1.yaml`.

## Sleeves, not a single blend

Every rupee has a job, and the investor can be told what it is:

| Sleeve | Job |
|---|---|
| **Core** | The engine. Broad, boring, long-held. Flexi-cap and large-cap. |
| **Growth** | A measured tilt to higher-return, higher-volatility segments. Mid and small cap. |
| **Stability** | Ballast. Its job is to be there when equity is not. Debt. |
| **Diversifier** | Something that does not move with the rest. Gold. |

**Not every goal gets every sleeve.** A three-year goal has no growth sleeve at
all, and an emergency fund has only stability. Empty sleeves are shown as
deliberately empty, because "we considered it and it does not belong here" is
information.

## Construction sequence

```
1. Select published policy      by goal type × horizon × binding risk band
2. Cap at the equity ceiling    hard constraint from the risk band
3. Apply the small-cap limit    max 25% of the equity sleeve
4. Apply the glide path         if the goal is within 5 years
5. Normalise to 100
```

### Step 1 — Policy selection

Seven published policies. `POL-EMERGENCY` matches on goal type and short-circuits
everything else — an emergency fund's requirements are liquidity and capital
stability, and return is irrelevant.

`POL-VERY-SHORT` (under three years) holds no equity at all, with the reason
stated in the policy itself: *"Equity is not an appropriate vehicle for money
needed inside three years."*

### Step 2 — Equity ceiling

| Band | Max equity |
|---|---|
| CONSERVATIVE | 20% |
| CAUTIOUS | 40% |
| MODERATE | 60% |
| GROWTH | 80% |
| AGGRESSIVE | 90% |

A hard constraint. Excess equity is moved to the stability sleeve, and the
investor is told:

> The published policy for this goal would hold more equity, but your binding
> risk band (MODERATE) caps it at 60%. The plan adjusts; the ceiling does not.

This is where "requirement never raises the ceiling" becomes concrete. A goal
needing 12% a year does not get more equity because it needs it. It gets the
truth: the contribution, the target or the date has to move.

### Step 3 — Small-cap limit

Small caps are capped at 25% of the equity sleeve. Excess moves to flexi-cap.
Small-cap drawdowns are the most common cause of an investor abandoning a plan,
and a portfolio that gets abandoned returns nothing regardless of its expected
return.

### Step 4 — Glide path

Starting five years before the goal, equity is reduced linearly toward 10% at
the goal date, twice a year. Everything taken out of equity goes to the
shortest-duration stability sleeve available.

The glide path is **published from day one**. An investor who can see it in
year one does not experience year-nine de-risking as the platform panicking —
which matters, because de-risking usually happens during exactly the market
conditions that make it look like a reaction.

## Capital market assumptions

Published in the ruleset, marked `COMMITTEE_ASSUMPTION`, deliberately
conservative:

| Class | Conservative | Base | Optimistic | Volatility |
|---|---|---|---|---|
| Equity large | 7.0 | 10.5 | 13.5 | 16.0 |
| Equity flexi | 7.0 | 11.0 | 14.5 | 17.0 |
| Equity mid | 6.0 | 11.5 | 16.0 | 21.0 |
| Equity small | 4.0 | 12.0 | 18.0 | 26.0 |
| Debt medium | 5.5 | 7.2 | 8.8 | 4.5 |
| Gold | 3.0 | 7.5 | 12.0 | 15.0 |
| Inflation | 7.0 | 6.0 | 4.5 | — |

These are judgement, not fact, and the product says so. They are versioned, so a
projection made under v1.0.0 stays replayable when v1.1.0 changes them.

## Correlations matter

Portfolio volatility is computed **through the correlation matrix**, not by
summing asset volatilities. For a typical Indian balanced portfolio, summing
overstates risk by roughly a third — which would understate every goal
probability on the platform and push every investor toward more equity than they
need.

The matrix is published in the same file. Gold's small negative correlation with
Indian equity is why it earns a diversifier sleeve at all; it is not there
because gold is expected to return well.

## Hygiene limits

| Limit | Value | Why |
|---|---|---|
| Max funds per goal | 5 | Beyond this, monitoring degrades |
| Max funds total | 12 | Past ~8 you own the market at an active fee |
| Min allocation per fund | 5% | Below this a holding adds admin, not diversification |
| Max single fund | 40% | Single-manager risk |
| Max single AMC | 35% | Operational and process risk |
| Max sector (look-through) | 30% | Accidental sector bets |
| Max pairwise overlap | 40% | Duplication paid for twice |
| Max small cap share of equity | 25% | Drawdown tolerance |

## Overlap analysis

Overlap = sum of the minimum weight of each shared security. Two funds each
holding a stock at 5% overlap by 5 points on that stock, not 10.

The verdict matters more than the number:

- **Same category, above 40%** → PROBLEMATIC. *"You are paying two expense
  ratios for one portfolio, and getting one portfolio's diversification."*
- **Different categories** → EXPECTED. A large-cap and a flexi-cap sharing a
  core is normal and not worth acting on.

Alarming investors about harmless overlap would generate churn, which is the
outcome the product is built to avoid. So the engine says which is which.

Look-through concentration is computed across AMC, sector, market cap and
individual security — the last of these regularly surprising, since a portfolio
of five diversified funds can carry 8% in one bank without any single fund
looking concentrated.

## Rebalancing

**Tolerance bands, not the calendar.** Trigger requires *both* an absolute drift
above 5 percentage points *and* a relative drift above 25% of the target weight —
the tighter test wins, which prevents a 2% gold sleeve triggering on a 1-point move.

**Preference order:**

1. **Do nothing** — inside the band. *"Rebalancing now would cost money and
   change nothing."*
2. **Cashflow rebalance** — redirect contributions. No tax, no exit load. Chosen
   when twelve months of inflow covers 70% of the gap.
3. **Gradual** — redirect now, review in six months.
4. **Sell** — requires drift beyond 1.5× the band, a positive net-of-tax
   benefit, *and* the goal inside its glide path.

Plus a floor of six months between rebalances.

The usual answer is (2), and it is worth stating why: selling to rebalance costs
tax and exit load to achieve something new contributions achieve for free. The
only reason to prefer selling is proximity to the goal, when the risk of leaving
the drift exceeds the cost of fixing it.

An empty portfolio returns BUILD, not REBALANCE. Arithmetically it is 100% off
target; sensibly, the answer is to build it.

## What this methodology does not do

- **No tactical allocation.** No view is taken on whether equity is cheap. The
  allocation moves for the investor's circumstances, not for the market's.
- **No factor tilts.** Value, momentum and quality tilts are omitted — not
  because they lack evidence, but because they require a conviction horizon
  most retail investors will not hold through.
- **No international sleeve by default.** `EQUITY_INTL` has assumptions but no
  policy uses it. Regulatory limits on overseas investment by Indian mutual
  funds have been variable, so it is a LEGAL REVIEW item, not a default.
- **No direct equity, ever.** Different product, different licence, different
  risk.
