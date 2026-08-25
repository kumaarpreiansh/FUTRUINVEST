# B. User Journey

The canonical arc:

```
Financial Health → Goals → Risk → Strategy → Fund Selection → Portfolio
   → SIP → Monitoring → Alerts → Review → Rebalance → Switch → Exit → Goal Completion
```

Each stage below states what the investor is actually thinking, what the product
does, and what it deliberately does not do.

---

## Stage 0 — Arrival (0–90 seconds)

**They are thinking:** "Is this another app that wants to sell me a fund?"

The first screen asks for age, monthly income and monthly expenses. Nothing else.
No sign-up wall, no KYC, no scheme names. Three numbers produce a real output —
monthly surplus and a first read on savings rate — which is the fastest available
proof that this tool is about them and not about a product.

**Not done:** no "top funds today", no returns carousel, no account creation
before value is delivered.

---

## Stage 1 — Financial health (2–4 minutes)

**They are thinking:** "Am I doing okay?"

Seven more Tier-1 questions complete the picture: savings, existing investments,
loans, emergency fund, experience, goals, horizon. The output is a 0–100 score
across ten dimensions, each with a finding and one action.

The score is **capped** when a foundation is missing. Someone with no emergency
fund and a well-performing equity portfolio cannot score above 70, and the cap
is shown with its reason. This is the first moment the product does something
a sales funnel would not: it tells a would-be investor to build a cash buffer
before investing more.

**Screen truth:** exactly one "do this next", chosen as the lowest-scoring
dimension with weight ≥ 8. Ten simultaneous recommendations is the same as none.

---

## Stage 2 — Goals (3–6 minutes)

**They are thinking:** "I want to retire at 55 and educate my child."

Each goal captures target in today's rupees, horizon, current corpus, current
contribution, priority, and whether the amount or the date can move. The product
inflates the target to the year it falls due — usually the first genuinely
uncomfortable number, and shown early on purpose.

Output per goal: inflation-adjusted target, three scenario projections, a
probability, a required return, a required contribution, and a probability band
in words (`ON_TRACK`, `LIKELY`, `AT_RISK`, `OFF_TRACK`, `UNLIKELY`).

**Not done:** no single projected corpus, ever. Three scenarios and a p10–p90
range, always.

---

## Stage 3 — Risk (2–3 minutes)

**They are thinking:** "I'm fine with risk, I'm young."

Four tolerance questions, plus one about what they actually did in the last
market fall. Capacity is derived from facts already collected — buffer, EMIs,
dependants, income stability, savings rate, horizon — and never asked.
Requirement falls out of the goal arithmetic.

The three answers routinely disagree, and the disagreement is the product:

> Your appetite is Growth, your capacity is Moderate, and this goal needs
> Aggressive. Capacity binds — a preference does not pay an EMI during a
> drawdown. And the gap between what the goal needs and what you can carry
> does not close by adding risk; it closes by changing the plan.

**Not done:** the product never emits "You are an Aggressive investor".

---

## Stage 4 — Strategy (1 minute, mostly automatic)

**They are thinking:** "So what should I actually hold?"

A published allocation policy is selected by goal type, horizon and binding risk
band, then capped at the band's equity ceiling, then small-cap-limited, then
glide-path adjusted if the goal is within five years. Presented as four sleeves,
each with a stated job.

If the cap bit, the product says so: *"The published policy for this goal would
hold more equity, but your binding risk band caps it at 60%. The plan adjusts;
the ceiling does not."*

---

## Stage 5 — Fund selection (2–4 minutes)

**They are thinking:** "Finally, the fund names."

Two to three candidates per sleeve. Each shows a quality score with its five
pillars, a separate suitability score with its components, the expense ratio,
the plan type, the risk-o-meter, a conflict disclosure, and any suitability
caveat in plain words.

Rejected schemes are visible on request with the gate they failed. Transparency
about exclusion is what makes the shortlist credible.

**Not done:** no twenty-fund grid, no star ratings, no "most popular".

---

## Stage 6 — Portfolio and SIP (2 minutes)

**They are thinking:** "How much, and can I afford it?"

The SIP screen is the highest-leverage screen in the product. It shows the
step-up ladder — 0%, 5%, 10%, 15% — priced in goal probability and in what the
final-year instalment becomes. For most investors a 10% step-up moves probability
further than every fund decision they will ever make.

If the required contribution exceeds their surplus, the product says the goal or
the date has to move, and does not offer more equity as a substitute.

---

## Stage 7 — Monitoring (continuous, invisible)

**They are thinking:** nothing. They are living their life.

Monthly signal evaluation per holding, quarterly portfolio review, annual profile
refresh. Most evaluations produce HOLD and no notification. Silence is the
correct output of a working plan.

---

## Stage 8 — Alerts (rare by design)

**They are thinking:** "Why is this app messaging me?"

Three notifications a week maximum, eight a month, RED exempt. Every alert
carries eight mandatory fields, including *what happens if you do nothing*.

Nothing is sent for a market fall. If a drawdown exceeds 15%, at most once a
quarter, the investor gets one contextual message that shows goal probability
before and after — and contains no call to transact.

---

## Stage 9 — Review (quarterly, investor-initiated or prompted)

**They are thinking:** "Am I still on track?"

Goal probability trend, allocation drift against target, overlap look-through,
fund scores with what changed, and progress against contribution plan. Framed
as progress, not performance: *"Your goals are 2.1% ahead of plan"* rather than
*"Your portfolio is up 14%"*.

---

## Stage 10 — Rebalance

**They are thinking:** "Do I need to sell something?"

Almost always no. The engine's preference order is: do nothing (inside the
band) → redirect new contributions (free) → gradual → taxable sale, which
requires drift beyond 1.5× the band, a positive net-of-tax benefit, and the goal
inside its glide path.

The common answer is: *"Redirecting your existing contributions closes this in
about seven months with no tax and no exit load."*

---

## Stage 11 — Switch review

**They are thinking:** "Should I move to the fund my colleague mentioned?"

The switch screen is a comparison plus a net figure. Expected improvement,
split into the certain part (cost) and the forecast part (quality), minus tax,
exit load and friction, with a breakeven in years and a mandatory list of what
would make the recommendation wrong.

Most proposed switches fail this test, and the product says so plainly:
*"The expected improvement does not cover the cost of making the change."*

---

## Stage 12 — Exit review

**They are thinking:** "Markets are falling. Should I get out?"

A price fall is not one of the six exit conditions, so the honest answer is no
and the product gives it. Behavioural guardrails engage: the five reality-check
questions, goal probability before and after, the tax that would be crystallised,
and a 24-hour cooling-off period on a deep drawdown.

The investor can always proceed. The product's job is to make sure the decision
is made with the facts in view and the reflex given time to pass.

The exits that *are* legitimate — money needed, goal met, risk capacity changed,
scheme materially changed, allocation no longer appropriate, planned withdrawal —
come with a sequencing plan that splits redemptions across financial years where
the timeline allows.

---

## Stage 13 — Goal completion

**They are thinking:** "It actually worked."

The glide path has already de-risked the corpus over the preceding five years,
on a schedule visible from year one. Withdrawal is sequenced for tax. The
completed goal is archived with its full decision history — every recommendation,
what was accepted, what was declined, and what happened next.

This archive is the product's real asset. It is what lets the platform, and the
investor, learn from a decade rather than from a quarter.

---

## Cross-cutting: the copilot

Available at every stage, answering in one fixed format: Answer, Why, Your
situation, Risks, Alternatives, Tax/cost, Recommendation, Confidence, Sources.

It is grounded in a fact pack assembled from the deterministic engines. It
cannot produce a number that is not in that pack. Asked something it has no
verified basis for, it says so — which is a feature, and the reason its answers
can be trusted at all.

---

## Failure paths, which are part of the journey

| Situation | What the investor sees |
|---|---|
| Profile incomplete | The specific missing fields, each with why it is being asked |
| Fund data below quality floor | "I don't have sufficient verified data on this scheme" |
| Tax rule unverified | The recommendation is withheld, not shown with a guessed figure |
| Confidence below threshold | "I don't have a strong enough basis to recommend anything here" |
| Goal unreachable at any risk | "The contribution, the target or the date has to move" |
| Not registered as an adviser | Analysis remains; personalised advice is withheld with the reason |

Every one of these is a stopping point that the product treats as a legitimate
outcome rather than an error to be routed around.
