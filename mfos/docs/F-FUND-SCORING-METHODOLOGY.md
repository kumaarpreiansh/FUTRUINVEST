# F. Fund Scoring Methodology

Implementation: `scoring.py`. Weights: `config/scoring/fund_scoring.v1.yaml`.

## What the score is, and is not

**It is:** a measure of how well a scheme does the job its own SEBI category
describes, judged against its own benchmark and its own category peers.

**It is not:** a recommendation. A scheme can score 92 and be entirely wrong for
a given investor. Suitability is computed separately in `selection.py` and can
veto any score. This separation is the point — collapsing quality and fit into
one number is how "top rated fund" becomes "fund you should buy", which is the
error the whole product exists to avoid.

## Why not rank by returns

Trailing-return ranking is the industry default and close to useless. It selects
for whichever factor was in favour over the ranking window. A three-year table
in a mid-cap bull run is a list of the funds that held the most mid-caps, which
tells you about the market, not the manager.

So **raw trailing return carries no direct weight**. What carries weight is
*consistency*: the share of rolling windows in which the fund beat its own
benchmark. One good year proves nothing. Beating your benchmark in 70% of all
three-year windows over eight years is evidence of something.

## The five pillars

| Pillar | Weight | Question |
|---|---|---|
| Consistency | 30 | Does it beat its own benchmark repeatedly, not once? |
| Risk | 25 | What was endured to get the return? |
| Quality | 20 | Is the process and portfolio sound, independent of outcome? |
| Cost | 15 | What is certain in advance? |
| Tracking | 10 | How closely does it do what it says? |

Weights sum to 100, validated at registry load.

### Consistency (30)

| Component | Weight | Detail |
|---|---|---|
| Rolling 3y outperformance rate | 40 | Share of 3-year windows beating the benchmark, scaled 0.2 → 0.8 |
| Rolling 5y outperformance rate | 30 | Same over 5-year windows; needs eight years of history |
| Return rank stability | 20 | Dispersion of rolling returns — a fund that alternates top and bottom decile is not consistent |
| Benchmark excess CAGR | 10 | The only place raw excess return appears, at the lowest weight in the heaviest pillar |

### Risk (25)

Percentile-ranked against category peers: Sharpe (25), Sortino (25), maximum
drawdown (25, shallower better), downside capture (15, lower better), volatility
(10, lower better).

Downside capture is weighted deliberately: a fund that falls less than its
benchmark is the fund an investor is most likely to still be holding at the
bottom, and staying invested dominates every selection decision.

### Quality (20)

| Component | Weight | Detail |
|---|---|---|
| Manager tenure | 25 | Scaled 0.5 → 7 years |
| Manager stability | 15 | Changes in the last five years |
| Portfolio concentration | 20 | Top-10 weight percentile against category |
| AUM adequacy | 20 | Non-monotonic: under ₹100 cr is fragile, ₹500–25,000 cr is comfortable, beyond that capacity drag |
| Turnover reasonableness | 10 | Very high turnover is cost without evidence of benefit |
| Mandate drift | 10 | Actual market-cap exposure against what the category promises |

Mandate drift is a *cause-level* signal, not a symptom. A large-cap fund quietly
holding 30% small caps is taking risk the investor never agreed to, and it will
look excellent right up until it does not.

### Cost (15)

Expense ratio percentile against category (70), exit load burden (15), plan-type
penalty (15 — Direct scores 100, Regular scores 35).

Cost is the only input known with certainty in advance, which is why the
Direct/Regular gap moves the score materially. In the reference universe two
schemes hold identical portfolios; the Direct plan scores 79.3 and the Regular
plan 61.4. The gap is entirely cost, and the product says so.

### Tracking (10)

For passive schemes, tracking error percentile against peers (60) and tracking
difference (40).

For active schemes, tracking error is scaled *inversely* — very low tracking
error in an actively managed, actively priced fund is closet indexing, and the
investor is paying active fees for index exposure.

## Passive override

Judging an index fund on alpha consistency is a category error, so passive
schemes use a different weight map: consistency 10, risk 20, cost 30, quality
10, tracking 30. Cost and tracking dominate, because for a passive fund they are
the entire proposition.

## Peer grouping

**Within SEBI category only.** Comparing a small-cap fund to a large-cap fund
tells you which asset class had a better decade. Cross-category ranking is
prohibited in the engine and in the UI.

Below eight peers, percentile components are marked indicative and the result
carries `thin_peer_group_note`. In the reference universe the debt and gold
categories have too few peers for percentiles, so their risk pillar returns
`None` and coverage drops to 75% — reported honestly rather than filled in.

## Missing data

A missing component **reduces coverage**; it never silently scores zero. The
weighted mean renormalises over available components and reports
`coverage_pct` per pillar and overall. A fund scored on 75% coverage is
presented as such.

## Disqualifiers

| ID | Condition | Effect |
|---|---|---|
| DQ-DATA-QUALITY | quality < 70 | `NOT_SCORED` — "insufficient verified data" |
| DQ-HISTORY | history < 36 months | `PROVISIONAL` — scored, flagged, treated cautiously |

## Prohibited inputs, and how the prohibition is verified

```yaml
prohibited_inputs:
  - "distributor_commission_pct"
  - "amc_relationship"
  - "amc_revenue_share"
  - "platform_margin"
  - "campaign_flag"
  - "marketing_spend"
```

These are named by their **real field names on the scheme record**, so the
exclusion is checkable rather than aspirational.

`test_no_commission_influence_on_ranking` assigns every scheme in the universe a
large, deliberately ranking-inverting commission, marks each AMC a preferred
partner, re-scores the whole universe, and asserts that **not one score moves**.
If a commercial field ever leaks into the scoring path, that test fails the build.

This is the difference between a conflict-of-interest policy and a
conflict-of-interest control.

## Bands

| Score | Label |
|---|---|
| 85+ | Strong |
| 70–84 | Above average |
| 55–69 | Average |
| 40–54 | Below average |
| < 40 | Weak |

Bands, not false precision. The difference between 71.4 and 73.8 is noise, and
presenting it as a ranking invites decisions it cannot support.

## Worked example

`FLX03` — Kestrel Flexi Cap Direct, from the synthetic universe:

```
Overall 27.8  Weak
  consistency  0.0   negative skill; loses to its benchmark in most windows
  risk        17.9   poor Sharpe and deep drawdowns against peers
  cost        34.0   1.35% expense ratio, well above category median
  quality     65.9   manager changed twice in five years; 1.2 years tenure
  tracking    50.0
```

Against `FLX07` — Halcyon Flexi Cap Direct:

```
Overall 83.0  Above average
  consistency 73.6   beats its benchmark in most rolling windows
  risk        94.3   best risk-adjusted profile in the peer group
  cost        74.0   0.74% expense ratio
  quality     90.5   stable management, sensible concentration
  tracking    81.8
```

The 55-point gap comes from consistency and risk, not from last year's return —
which is exactly what the weighting was designed to produce.

## What the score cannot see

Published with every score, because a methodology that hides its blind spots is
marketing:

- **Process.** We observe results, not how they were produced. A disciplined
  manager having a bad three years and an undisciplined one having a good three
  years can look identical here.
- **The future.** Every input is historical. Persistence of fund performance is
  weak in the literature and we do not claim otherwise.
- **Thin peer groups.** Percentiles over six peers are noise dressed as
  precision, which is why they are flagged.
- **Regime change.** A fund optimised for the last decade's market may be
  unsuited to the next one, and nothing in this score would show it.

## Review cadence

Scores recomputed monthly on NAV updates, and immediately on any material scheme
event. The methodology itself is reviewed every 180 days by the investment
committee; any weight change is a ruleset version bump, and historical
recommendations remain replayable against the version that produced them.
