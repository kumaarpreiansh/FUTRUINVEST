# FUTRUINVEST — AI-led Mutual Fund Investment Operating System

A goal-first investment operating system for Indian mutual fund investors.
Reference implementation plus the full design set.

> **Not a SEBI-registered Investment Adviser. Nothing here is investment advice.**
> Execution and withdrawals are permanently disabled in this repository. Every
> regulatory and tax rule is versioned configuration carrying a source and a
> verification status — several are marked `UNVERIFIED`, and the engines refuse
> to compute with them.

## Run it

```bash
pip install -r ../requirements.txt      # PyYAML is the only dependency used here

python mfos/demo.py                     # full end-to-end run on synthetic data

cd mfos/tests && python3 -m unittest discover -s .    # 176 tests
```

The demo shows an investor being profiled, three risk dimensions disagreeing,
goals projected and stressed, a fund universe scored and shortlisted, signals
firing and being dampened, a switch priced net of tax, six exit conditions, the
behavioural guardrails, the alert budget, and the pipeline halting at compliance
before running all sixteen stages in a labelled sandbox with execution still
closed.

## What it does

| | |
|---|---|
| **Asks what the money is for** before it says what to buy |
| **Separates three risk questions** — capacity, tolerance, requirement — and names the conflicts instead of averaging them away |
| **Projects in ranges**, never a single number, with seven stress tests reported as changes in goal probability |
| **Scores funds on consistency, not last year's return**, within SEBI category peer groups |
| **Prices every switch net of tax, exit load and friction**, with a breakeven in years |
| **Has no price-based exit** — six exit reasons, none of them a market fall |
| **Refuses** when data quality, rule verification or confidence is insufficient |
| **Never lets the LLM produce a number** — every figure is deterministic and reproducible |

## Layout

```
mfos/
  docs/          A–T design set, legal review register, decision log
  config/        versioned rule-sets: tax, compliance, scoring,
                 allocation, signals, alerts, AI model registry
  src/mfos/      the engines
  tests/         176 tests
  demo.py        end-to-end run on synthetic data
```

### The engines

| Module | Does |
|---|---|
| `ruleset.py` | Loads versioned rule-sets; refuses unverified rules; validates config at start-up |
| `metrics.py` | XIRR, CAGR, rolling returns, Sharpe, Sortino, alpha, beta, drawdown, capture |
| `goals.py` | Goal engine; closed-form goal probability |
| `scenarios.py` | Three scenarios, seven stresses, glide path, digital twin |
| `sip.py` | SIP lifecycle, step-up ladder, required-contribution solve |
| `tax.py` | FIFO lots, exemption tracking, exit load, switch-as-redemption |
| `profile.py` | Progressive profiling, versioning, fingerprints |
| `health.py` | Financial health score with foundation caps |
| `risk.py` | Capacity, tolerance, requirement, and their conflicts |
| `funds.py` | Multi-source data layer, source hierarchy, quality scoring |
| `scoring.py` | 0–100 fund score over five pillars |
| `selection.py` | Suitability gates then ranking |
| `portfolio.py` | Sleeve construction, overlap, look-through, rebalancing |
| `signals.py` | Seven-state signal engine with dampeners |
| `switching.py` | Net-of-cost switch analysis |
| `exits.py` | Six exit types, tax-aware sequencing |
| `behaviour.py` | Six behavioural guardrails |
| `alerts.py` | Alert construction, budget, de-duplication |
| `compliance.py` | Rule gates, language scan, consent verification |
| `confidence.py` | Confidence from checkable inputs |
| `audit.py` | Append-only, replayable decision record |
| `copilot.py` | Grounded answers with numeric verification |
| `pipeline.py` | The sixteen stages |
| `synthetic.py` | Clearly-labelled fake data for demo and tests |

## The five rules everything else follows from

1. **Compliance overrides AI.** A blocked recommendation is not produced.
2. **Data quality overrides AI.** Below the floor, the answer is "I don't have
   enough verified data".
3. **Suitability overrides performance.** Failing schemes are removed, not
   down-ranked.
4. **The LLM never produces a number.**
5. **Nothing executes without specific, per-transaction consent** — and here,
   nothing executes at all.

## Status

Pre-launch. `ComplianceEngine.readiness()` reports:

```
Not launchable as an advisory service. 3 blocking rule(s) unmet,
1 rule(s) unverified. The engines run; the regulated behaviours are gated off.
```

Twenty rules await legal review. See
[docs/LEGAL-REVIEW-REGISTER.md](docs/LEGAL-REVIEW-REGISTER.md).

## Start here

[docs/00-INDEX.md](docs/00-INDEX.md)
