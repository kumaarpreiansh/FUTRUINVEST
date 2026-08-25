# N. UX / UI Screen Map

## Copy rules, applied everywhere

1. **No number without a comparison.** "Your fund returned 12%" is uninformative.
   "12% against 11% for its benchmark, with lower volatility" is information.
2. **No projection without a range.** Three scenarios or nothing.
3. **No recommendation without "what could make this wrong".**
4. **No action prompt without its tax and cost.**
5. **Rupees, not just percentages.** "0.9% higher expense ratio" is abstract;
   "₹9,485 a year on this holding" is not.
6. **Every alert answers "what if I do nothing?"**
7. **Never manufacture urgency.** No countdowns, no "act now", no red badges on
   anything that is not genuinely material.

## Screen map

```
ONBOARDING
  O1  Three questions              age, income, expenses → surplus, instantly
  O2  Seven more                   savings, investments, loans, buffer,
                                   experience, goals, horizon
  O3  Financial health             score, ten dimensions, ONE action
  O4  First goal                   target, date, current corpus
  O5  Risk                         four questions + "what did you do last time?"
  O6  Your plan                    allocation, projection, probability

HOME
  H1  Dashboard                    wealth, invested, gain, goal progress,
                                   portfolio health, risk band,
                                   today's one alert, one AI recommendation

GOALS
  G1  Goal list                    probability band per goal, ordered by need
  G2  Goal detail                  three scenarios, p10–p90, required return,
                                   required contribution, shortfall
  G3  Stress tests                 seven scenarios in probability terms
  G4  Digital twin                 what-if sliders: SIP, date, lump sum, allocation
  G5  Step-up ladder               0/5/10/15% priced in goal probability

PORTFOLIO
  P1  Holdings                     with goal tags, not just a flat list
  P2  Allocation                   current vs target, drift, sleeve view
  P3  Overlap                      pairwise matrix + verdict per pair
  P4  Look-through                 sector, market cap, AMC, top securities
  P5  Performance                  always vs benchmark and category, never alone
  P6  Fund detail                  score, five pillars, metrics, provenance

DECISIONS
  D1  Signals                      per holding, with what fired
  D2  Recommendation               the full explainable format
  D3  Decision simulator           before/after: allocation, tax, goal probability
  D4  Switch comparison            side by side + net benefit + breakeven
  D5  Exit review                  which of six reasons, counter-arguments
  D6  Consent                      the twelve-item transaction checklist

MONEY
  M1  SIP list                     with pause presented as a normal option
  M2  SIP detail                   history, step-up, impact
  M3  Tax centre                   realised, unrealised, exemption used, headroom
  M4  Transactions                 with the recommendation that led to each

TRUST
  T1  Trust centre                 the "why should I believe this" page
  T2  Methodology                  scoring, allocation, probability — the actual maths
  T3  Data sources                 hierarchy, freshness, quality per scheme
  T4  Conflicts                    every commercial relationship
  T5  AI disclosure                models, versions, what they may and may not do
  T6  Decision history             every recommendation, accepted and declined
  T7  Regulatory status            plainly stated, including what we are not

LEARN
  L1  Contextual explainer         30 seconds, from wherever the question arose
  L2  Depth                        beginner / intermediate / advanced
```

## H1 — Dashboard

The most-visited screen, and the easiest to get wrong. What it shows:

```
Your wealth                              ₹29.88 L
Invested ₹22.40 L        Gain ₹7.48 L  (+33.4%)

Goal progress            ████████░░  84% on track
Portfolio health         87/100
Risk                     Moderate (capacity-bound)

⚠ One thing to look at
   Your equity allocation is 6 points above target.

💡 What we suggest
   Use new SIP contributions to close it gradually rather than
   selling. Selling would cost about ₹18,400 in tax to achieve
   the same allocation.
```

**Exactly one alert and one recommendation.** More than one of each and the
investor triages instead of acting, which is the same as showing none.

**What it does not show:** today's movement, a market index, "top performing
funds", or anything that changes colour intraday.

## D2 — Recommendation

The screen the whole architecture exists to produce:

```
SWITCH REVIEW                                     Confidence 67%

Meridian Flexi Cap Regular → Meridian Flexi Cap Direct

Why
  • Identical portfolio, 1.10 percentage points cheaper each year
  • That is about ₹8,536 a year on this holding — the only part of
    this case that is certain
  • Quality score 79.3 vs 61.4, entirely explained by cost

What could make this wrong
  • Switching realises about ₹49,530 in tax this year that you
    would not otherwise pay
  • On these assumptions it takes 4.0 years just to recover its
    own costs — if you might need this money sooner, do not switch
  • A fund manager change or a regime reversal would undermine it

Tax impact                                        ₹49,530  ⓘ lower bound
Exit load                                              ₹0
Friction                                           ₹1,164
Net benefit over 5 years                          ₹12,822

Portfolio impact
  Equity   72% → 72%      (no change — same portfolio)
  Cost     1.78% → 0.68%
  Goal probability   84% → 86%

[ See the full comparison ]   [ Not now ]   [ Review and consent ]
```

Note what is prominent: the counter-arguments and the breakeven, not the
benefit. And that "Not now" is a first-class button, the same weight as
proceeding.

## D6 — Consent

The twelve-item checklist, rendered as a screen rather than buried in terms:

```
Before you confirm

  1  Transaction        Switch
  2  Amount             ₹8,62,229.60 (1,800 units)
  3  From               Meridian Flexi Cap Regular
     To                 Meridian Flexi Cap Direct
  4  Reason             Lower expense ratio, identical portfolio
  5  Estimated tax      ₹49,530   (lower bound — surcharge not modelled)
  6  Exit load          ₹0
  7  Portfolio impact   Cost 1.78% → 0.68%; goal probability 84% → 86%
  8  Conflicts          We have no commercial relationship with this AMC

  ☐  I have read the tax impact and understand it is an estimate
  ☐  I understand a switch is a redemption and starts a new holding period
  ☐  I am giving consent for this specific transaction only

  [ Confirm ]     This consent expires in 15 minutes
```

Consent captured without items 5 and 8 shown is rejected by
`verify_consent()`. The screen and the code agree, which is the only way a
disclosure requirement survives a redesign.

## Behavioural interstitial

Between a drawdown-driven sell intent and the transaction:

```
Before you sell

Your portfolio is down 24%. Before this goes through: a fall in
prices is not, by itself, new information about your plan.

  Has your goal changed?                                    ☐ Yes  ☐ No
  Has your income or job situation changed?                 ☐ Yes  ☐ No
  Has your capacity to take risk changed?                   ☐ Yes  ☐ No
  Has anything changed about the scheme itself?             ☐ Yes  ☐ No
  Has your target allocation moved outside its band?        ☐ Yes  ☐ No

If you sell now
  Goal probability      84% → 41%
  Tax crystallised      ₹1,24,000
  You would need        ₹18,400/month more to recover the gap

[ I still want to sell ]              [ Keep my plan ]
```

**"I still want to sell" always works.** After a 24-hour cooling-off on a deep
drawdown, but it works. Blocking someone from their own money is both wrong and
probably unlawful.

If all five answers are "No", the screen says so plainly: nothing has changed
except the price.

## T1 — Trust centre

```
Why should you believe any of this?

Regulatory status
  We are NOT registered with SEBI as an Investment Adviser.
  Nothing here is personalised investment advice.        [what this means]

How we make money
  [ the actual model, in one paragraph ]

Do commissions affect what we show you?
  No — and here is how that is enforced rather than promised:
  every ranking is computed with commercial fields excluded, and an
  automated test re-ranks the entire universe with commissions
  reversed on every build. If any ranking moved, the build fails.

Where our data comes from                                 [source hierarchy]
How we score funds                                        [the actual maths]
How we compute goal probability                           [the actual maths]
What our AI can and cannot do                             [model registry]
What we don't know                                        [limitations]
Your decision history                                     [every recommendation]
Complaints                                                [grievance process]
```

The limitations page is not a disclaimer. It lists, in plain language: that
scoring rests on history which does not predict, that capital market assumptions
are committee judgement, that tax figures are lower bounds because surcharge is
not modelled, and that several tax rules await legal verification.

## Empty, partial and refusing states

Most products design the happy path and let the rest fall out. Here the refusing
states are the product's character:

| State | Copy |
|---|---|
| Profile incomplete | "I need three more things before I can answer that — here's why each one matters." |
| Fund data poor | "I don't have sufficient verified data on this scheme to make a recommendation about it." |
| Tax rule unverified | "I can't compute the tax on this accurately yet, so I'm not going to show you a recommendation that depends on it." |
| Confidence too low | "I don't have a strong enough basis to recommend anything here." |
| Goal unreachable | "No contribution within reach gets this to 75%. The amount, the date or the target has to move — adding risk is not a substitute." |
| Nothing to say | "Nothing material has changed. Your plan is working." |

That last one is the most important screen in the product, and the hardest to
ship, because it looks like the app is doing nothing. It is doing the main
thing.

## Accessibility and reach

WCAG 2.2 AA. Colour never the sole carrier of meaning — every severity has an
icon and a word. Full screen-reader support on tables and charts, each with a
text summary. Dynamic type to 200%. Offline read of the last synced state.

English plus the major Indian languages, with **numbers formatted in the Indian
system throughout** (₹1,23,45,678, and lakh/crore phrasing where natural).
Financial vocabulary is translated by a person, not machine-translated, because
"risk capacity" and "risk tolerance" collapse into the same word in several
languages and the distinction is the whole point.
