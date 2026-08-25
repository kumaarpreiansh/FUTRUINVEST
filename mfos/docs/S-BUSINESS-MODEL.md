# S. Business Model

## The structural problem

Most Indian investment platforms earn from distribution: an AMC pays a trail
commission on assets brought in, and the platform's revenue is a function of
which schemes investors hold and how much they move.

That model is not neutral. It does not require anyone to behave badly; it just
means the platform's interests and the investor's interests point in slightly
different directions, permanently. Regular plans earn more than Direct plans.
Switching generates activity. More AUM in a partner AMC is worth more than the
same AUM elsewhere.

A product whose core claim is "our recommendations are not influenced by
commercials" cannot rest that claim on good intentions.

## The revenue options, honestly assessed

| Model | Revenue | Conflict | Viability in India |
|---|---|---|---|
| **Fee-only advisory** | Investor pays a fee | Minimal — aligned | Hard: Indian retail is unused to paying for advice |
| **Direct-plan distribution** | Nothing from AMCs on Direct plans | None | No revenue at all |
| **Regular-plan distribution** | AMC trail commission | **Severe** | Standard, and incompatible with this product |
| **Freemium** | Free planning, paid depth | Low | Plausible, needs scale |
| **B2B2C** | Employers, banks pay | Low, if the buyer's interest aligns | Plausible |
| **Adviser tooling** | SaaS to registered advisers | Low | Plausible, smaller market |

## The chosen model

**Fee-only, with a free planning tier.**

| Tier | Price | Includes |
|---|---|---|
| **Free** | ₹0 | Financial health, goals, projections, portfolio tracking, overlap analysis, education, alerts |
| **Plan** | Annual subscription | Recommendations, switch and rebalance analysis, tax engine, scenario modelling, copilot |
| **Advised** | Higher annual fee | Everything, plus access to a qualified human adviser |

Two things follow from this shape, and both are deliberate:

**The free tier is genuinely useful.** Financial health, goal probability and
overlap analysis are the features most likely to change an investor's behaviour
for the better, and they are free. If the paid tier were the only place value
existed, the free tier would be a funnel, and funnels corrupt the copy.

**Revenue does not vary with what the investor holds or how often they move.**
A subscriber who never transacts pays the same as one who rebalances quarterly.
This is the only structure in which "we recommend you do nothing" is
commercially neutral — and the product recommends doing nothing very often.

## What is refused, and why

**No trail commission from AMCs on advised assets.** The moment revenue depends
on which scheme is held, every ranking becomes contestable.

**No paid placement, ever.** Not "sponsored, clearly labelled". Not "promoted
funds". The ranking is the product.

**No revenue share on transaction volume.** It would make the switch engine a
profit centre, which is precisely the failure mode [H](H-SWITCHING-EXIT-METHODOLOGY.md)
is built to prevent.

**No selling investor data.** Not aggregated, not anonymised, not "insights".

**No lead generation to insurers or lenders.** The financial health score
identifies insurance gaps, which makes it a valuable lead source, which is
exactly why it must not be one. The score would drift toward finding gaps.

### If distribution economics are ever required

Should the business need distribution revenue to survive, the requirement is
**structural segregation**: a separate entity, separate systems, and
recommendation ranking that provably cannot see the commercial layer.

Until that structure exists and has been reviewed by counsel, the answer is no.
`config/platform.yaml` records it as a locked gate:

```yaml
commission_influences_ranking: false   # MUST stay false. Enforced by test.
```

## How the claim is enforced rather than promised

`test_no_commission_influence_on_ranking` assigns every scheme in the universe a
large, ranking-inverting commission, marks every AMC a preferred partner,
re-scores the entire universe, and asserts **not one score moves**. If a
commercial field ever reaches the scoring path, the build fails.

The Trust Centre says this to investors in those terms — not "we don't let
commissions affect rankings" but "here is the automated test that runs on every
build, and here is what happens if it fails."

## Unit economics

The honest version:

- Free-tier cost is dominated by data licensing, which is largely fixed, plus
  compute per active investor, which is small.
- Paid conversion is the whole model. A free tier that converts at 3% needs a
  large top of funnel; at 8% it works at modest scale.
- The copilot is the main variable cost per user and is rate-limited
  accordingly.
- Human advisers are the expensive tier and are priced to cover their time
  rather than as a loss leader.

The uncomfortable truth: **fee-only retail advisory is hard in India.** Indian
investors are accustomed to advice being free because it is paid for invisibly
through Regular plan expense ratios. The bet is that showing an investor
precisely what that invisible payment costs them — in rupees, over their actual
horizon — makes an explicit fee comparatively easy to accept.

That bet may be wrong. If it is, the answer is a different business, not a
different set of incentives inside this one.

## Success metrics

Not measured: transactions, AUM, trades, switches, sessions, time in app.

### Financial outcomes
- Goal achievement probability, median and distribution
- Goals completed
- Savings rate change over twelve months
- Portfolio diversification: share within all hygiene limits
- Risk alignment: share of portfolios inside their binding band

### Behaviour
- SIP consistency
- Step-up adoption
- **Panic sales avoided** — sell intents during drawdowns that did not proceed
  after the guardrail
- **Unnecessary switching avoided** — signals where the net-of-cost analysis said
  hold

### Trust
- Recommendation acceptance
- **Recommendation reversal within twelve months** — how often we tell someone
  the opposite of what we told them. Should be near zero; a rising number means
  the engines are reacting to noise.
- Explanation satisfaction
- Data accuracy: estimate versus actual on tax and returns
- Complaint rate

### Engagement, carefully
- Monthly active investors
- Goal reviews, portfolio reviews, health checks completed

**Not** session count or time in app. A product that succeeds is one an investor
checks quarterly and trusts in between. Optimising for attention would mean
building the notification behaviour [F17](C-FEATURE-MAP.md) exists to prevent.

## The commercial risk worth naming

The features that make this product good are the features that make it hard to
monetise conventionally: it discourages transacting, refuses to sell leads, and
tells investors to save more rather than to buy something.

If the business runs short of money, the pressure to relax exactly those
constraints will be strong, and it will arrive dressed as a reasonable product
decision. The gates in `platform.yaml`, the automated ranking test, and this
document exist so that relaxing them requires an explicit, visible, minuted
decision rather than a quiet drift.
