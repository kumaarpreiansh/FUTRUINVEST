# FUTRUINVEST — AI-led Mutual Fund Investment Operating System

Design documentation set. Written before the regulated behaviours ship, which is
the order the brief asked for and the only order that makes sense: the compliance
architecture decides what the product may do, and everything else is downstream
of that.

## Read in this order

| # | Document | What it settles |
|---|----------|-----------------|
| A | [Product Requirements](A-PRD.md) | What this is, who it is for, what it refuses to do |
| B | [User Journey](B-USER-JOURNEY.md) | The investor's path from first screen to goal completion |
| C | [Feature Map](C-FEATURE-MAP.md) | Every feature against the ten-point template |
| D | [AI Architecture](D-AI-ARCHITECTURE.md) | Which model does what, and what none of them may do |
| E | [Investment Decision Engine](E-INVESTMENT-DECISION-ENGINE.md) | The sixteen-stage pipeline and its halts |
| F | [Fund Scoring Methodology](F-FUND-SCORING-METHODOLOGY.md) | How a 0–100 fund score is built and why |
| G | [Portfolio Construction](G-PORTFOLIO-CONSTRUCTION.md) | Sleeves, ceilings, glide paths, rebalancing |
| H | [Switching and Exit Methodology](H-SWITCHING-EXIT-METHODOLOGY.md) | When to change a fund, and the six reasons to leave one |
| I | [Compliance Architecture](I-COMPLIANCE-ARCHITECTURE.md) | The regulatory model and the gates that enforce it |
| J | [Data Architecture](J-DATA-ARCHITECTURE.md) | Sources, hierarchy, quality scoring, lineage |
| K | [Database Schema](K-DATABASE-SCHEMA.md) | Tables, keys, versioning, retention |
| L | [API Architecture](L-API-ARCHITECTURE.md) | Services, contracts, idempotency, the execution boundary |
| M | [Security Architecture](M-SECURITY-ARCHITECTURE.md) | Threat model and controls |
| N | [UX Screen Map](N-UX-SCREEN-MAP.md) | Screens, states, and the copy rules |
| O | [MVP Roadmap](O-MVP-ROADMAP.md) | Three phases, and what gates each one |
| P | [Test Strategy](P-TEST-STRATEGY.md) | What is tested, and the invariants that must never break |
| Q | [AI Governance Framework](Q-AI-GOVERNANCE.md) | Model registry, validation, oversight, rollback |
| R | [Regulatory Change Management](R-REGULATORY-CHANGE-MANAGEMENT.md) | How a circular becomes a config change |
| S | [Business Model](S-BUSINESS-MODEL.md) | How it earns money without corrupting the advice |
| T | [Deployment Architecture](T-DEPLOYMENT-ARCHITECTURE.md) | Production topology, DR, observability |
| — | [Legal Review Register](LEGAL-REVIEW-REGISTER.md) | **Every open legal question, by rule ID** |
| — | [Decision Log](DECISION-LOG.md) | Design decisions and the reasoning behind them |

## The five sentences that shape everything else

1. **Compliance overrides AI.** A recommendation blocked by a rule is not
   down-weighted; it is not produced.
2. **Data quality overrides AI.** Below the quality floor, the answer is "I
   don't have enough verified data", not a hedged recommendation.
3. **Suitability overrides performance.** A scheme that fails a suitability gate
   is removed from consideration, never merely ranked lower.
4. **The LLM never produces a number.** Every figure comes from a deterministic
   engine; the model may only describe figures that already exist.
5. **Nothing executes without specific, per-transaction consent** — and in this
   repository, nothing executes at all.

## What is actually built here

`mfos/src/mfos/` is a working reference implementation of the deterministic
engines: metrics, goals, scenarios, SIP, tax, risk, fund scoring, selection,
portfolio construction, overlap, rebalancing, signals, switching, exits,
behavioural guardrails, alerts, compliance, confidence, audit, copilot and the
pipeline. `mfos/tests/` holds 176 tests over them. `mfos/demo.py` runs the whole
thing end to end on clearly-labelled synthetic data.

Every regulatory and tax rule lives in `mfos/config/` as versioned YAML with a
source and a verification status. Several are marked `UNVERIFIED`, and the
engines refuse to compute with them — see the Legal Review Register.

## Status

Pre-launch. Not registered with SEBI as an Investment Adviser. The personalised
advice gate is closed, the execution gate is closed, and the automated execution
and withdrawal gates are permanently closed. `ComplianceEngine.readiness()`
reports what still blocks a launch.
