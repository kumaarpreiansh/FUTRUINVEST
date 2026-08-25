# O. MVP Roadmap

## Phase 0 — Before any of it (the real gate)

Nothing in Phase 1 ships to a real investor until these are done. They are not
engineering tasks and they are on the critical path.

| Item | Owner | Blocks |
|---|---|---|
| Decide the operating model: IA, distributor, or segregated | Legal + founders | Everything |
| SEBI IA registration, if that is the model | Legal | All personalised advice |
| Verify every tax rule against primary sources | Tax counsel | Tax engine |
| Populate surcharge bands | Tax counsel | Removing the "lower bound" label |
| Verify fee caps and permitted modes | Legal | Billing |
| Build the AML/CFT programme | Compliance | Any transaction |
| DPDP consent architecture and compliance calendar | Legal + eng | Production data |
| Advertising review of every investor-facing string | Legal | Launch |
| Bias testing on the explainer model | AI governance | LLM in production |
| Grievance redressal and ODR process | Compliance | Launch |

`ComplianceEngine.readiness()` reports the current state of the ones that are
machine-checkable.

## Phase 1 — MVP

**Objective:** an investor can understand their position, set goals, and get an
explainable plan. Whether they can act on it depends on Phase 0.

| # | Feature | Status here |
|---|---|---|
| 1 | Investor onboarding, progressive | `profile.py` — built |
| 2 | Financial health score | `health.py` — built |
| 3 | Goals with probability | `goals.py` — built |
| 4 | Three-dimensional risk | `risk.py` — built |
| 5 | Mutual fund database with quality gating | `funds.py` — built (synthetic data) |
| 6 | Fund comparison and scoring | `scoring.py` — built |
| 7 | Portfolio tracking, overlap, look-through | `portfolio.py` — built |
| 8 | SIP planner with step-up | `sip.py` — built |
| 9 | AI copilot, grounded | `copilot.py` — built |
| 10 | Portfolio health | built |
| 11 | Alerts with budget | `alerts.py` — built |
| 12 | Explainable recommendations | `pipeline.py` — built |

**Still required for a real Phase 1:** live data licences and ingestion, the
mobile and web clients, identity and KYC integration, the persistence layer from
[K](K-DATABASE-SCHEMA.md), and everything in Phase 0.

**Phase 1 exit criteria**
- Recommendation replay works on 100% of a sampled month
- Ungrounded-number rate is zero across the grounding suite
- Median data quality above 85 across the live universe
- Alerts per investor per month at or below 8
- Zero recommendations delivered while a blocking rule is unmet

## Phase 2 — Acting on the plan

| # | Feature | Status here | Gated on |
|---|---|---|---|
| 13 | Switch intelligence | `switching.py` — built | Verified tax rules |
| 14 | Rebalancing | `portfolio.py` — built | Verified tax rules |
| 15 | Tax engine, production grade | `tax.py` — built, rules unverified | Tax counsel |
| 16 | Advanced scenario modelling | `scenarios.py` — built | — |
| 17 | Direct-plan execution | Boundary built, permanently gated | IA registration, AML, execution rails |
| 18 | Human adviser escalation | Triggers built | Advisers on staff |

**Phase 2 exit criteria**
- Tax estimate variance against real statements under 2%
- Switch recommendations per investor per year below 0.5
- Taxable rebalances per portfolio per year near zero
- 100% of executions preceded by valid, specific, single-use consent
- Human review actioned within one business day at the 95th percentile

## Phase 3 — Depth

| # | Feature | Notes |
|---|---|---|
| 19 | Automated portfolio monitoring | Signal engine at scale, scheduled per holding |
| 20 | Advanced predictive analytics | Only for data quality and behaviour. **Not for return prediction.** |
| 21 | Personalised financial coaching | Education sequenced to the investor's actual gaps |
| 22 | Advanced behavioural intelligence | Individual-level intervention effectiveness |
| 23 | Family financial planning | Household capacity, shared goals, joint suitability |

Item 20 deserves a note: predictive analytics here means anomaly detection on
data feeds and classification of behavioural risk. Predicting fund returns is
outside the product's philosophy, and if a model appeared to do it, showing that
to retail investors would still be the wrong thing to do with it.

## What is deliberately never on the roadmap

Direct equity. Intraday anything. Leverage. Derivatives. Crypto. Social/copy
investing. A returns leaderboard. Automated rebalancing without consent. Any
feature whose success metric is transaction count.

## Sequencing logic

Phase 1 is *understanding*. Phase 2 is *acting*. Phase 3 is *depth*.

The order is deliberate: a platform that can execute before it can explain is a
distribution funnel. Building the explanation, the audit trail and the
behavioural guardrails first means execution arrives into a system that already
knows how to justify itself.

The engines are built ahead of their phase because the *design* had to be proved
end to end before committing to the data licences and the regulatory path. What
gates each phase is not engineering; it is verification, registration and
counsel.
