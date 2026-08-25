# claude-ai-hedge-fund

An educational, multi-agent "investment committee" for market research, debate,
backtesting, risk control, and **paper trading** — orchestrated by Claude
(prompts included), with a deterministic offline pipeline you can run today.

> 🛑 **Read first.** This is educational only and not personalized financial
> advice. It does not promise profits, returns, income, or predictions, and it
> is not a real hedge fund. Everything defaults to historical testing,
> simulation, and paper trading with human approval — **live trading and
> withdrawals stay OFF.**

## How it works

```
PRICE + NEWS + FUNDAMENTALS + SENTIMENT
              ↓
        RESEARCH BRIEF
              ↓
     BULL CASE + BEAR CASE
              ↓
        DEBATE ENGINE
              ↓
      STRATEGY PROPOSAL
              ↓
          BACKTEST
              ↓
          RISK GATE
              ↓
         PAPER TRADE
              ↓
       HUMAN APPROVAL
```

Instead of one model making every decision, the work is split across
specialized agents that each do one job, then debate. The orchestrator plans,
delegates, and reviews — it does not trade. You (a human) approve everything;
real execution is out of scope by default.

## The investment committee

| Agent | Responsibility | Input | Output |
|---|---|---|---|
| Research Agent | Gather + organize the picture | Raw data | Research brief |
| Fundamental Analyst | Financials + valuation | Fundamentals | Fundamental read |
| Technical Analyst | Charts + indicators | Price/volume | Technical read |
| News Analyst | Catalysts + events | Headlines | Impact summary |
| Sentiment Analyst | Crowd mood vs price | Social/market data | Sentiment read |
| Bull Researcher | Strongest case FOR | Research brief | Bull thesis |
| Bear Researcher | Strongest case AGAINST | Research brief | Bear thesis |
| Debate Engine | Stress-test both sides | Bull + bear theses | Committee memo |
| Strategy Agent | Turn the view into rules | Debate result | Strategy spec |
| Backtest Agent | Test rules on history | Strategy + data | Metrics |
| Risk Manager | Enforce limits, can veto | Every signal | Approve/block |
| Final Verdict Agent | Synthesize everything | All outputs | Verdict |
| **Human Reviewer** | **The final decision** | Verdict | Approve/reject |

Each role's LLM prompt lives in `agents/<role>/PROMPT.md`. The **bull and bear
researchers call Claude** (`claude-opus-4-8`, configurable in
`config/agents.yaml`) when `ANTHROPIC_API_KEY` is set in `.env`: each sends its
PROMPT.md as the system prompt plus the research brief as its only data, and
returns a structured thesis (evidence, confidence, invalidation conditions).
Every role — including bull/bear when no key is set or a call fails — falls
back to a deterministic rule-based stand-in, so the whole pipeline always runs
offline. The verdict memo records which mode produced each thesis.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

With no data present, the run uses a clearly-labeled **synthetic** price series
so you can exercise the pipeline end to end. To use real history, drop a CSV at
`data/raw/<ASSET>.csv` with columns `date,open,high,low,close,volume` and set
`asset` in `config/markets.yaml`.

Each run produces:

- `reports/verdict_*.md` — the committee memo ending in APPROVE / REVISE / REJECT
- `logs/decision_log.jsonl` — every stage's decision, timestamped
- `paper_trading/orders.log` — simulated fills only (no broker is connected)

## Layout

```
claude-ai-hedge-fund/
├── CLAUDE.md            # orchestrator context (for Claude Code)
├── README.md
├── .env.example         # key template (never commit real .env)
├── config/              # markets / agents / strategy / risk YAML
├── data/{raw,processed,cache}/
├── research/{prices,news,fundamentals,sentiment}/
├── agents/<role>/       # one PROMPT.md per committee role
├── strategies/          # STRATEGY_TEMPLATE.md — every field mandatory
├── backtests/  paper_trading/  reports/  logs/  alerts/
├── tests/
└── main.py              # entry point (paper by default)
```

## Risk layer

`config/risk.yaml` is the hard gate (example values only, not recommendations):
per-trade / daily / weekly loss caps, max drawdown, position and concentration
limits, a kill switch, mandatory human approval, and `live_trading: false` +
`allow_withdrawals: false` — **`main.py` refuses to run if either is enabled.**

Backtests always include fees and slippage, split in-sample vs out-of-sample,
and pass through a review for overfitting, too-few-trades, and drawdown before
the risk gate even looks at them.

⚠️ Backtests can look strong and still fail live. Simulated results are not
real results.

## Building it out

The pipeline is provider-neutral. Battle-tested open-source projects to slot
into each layer (verify each repo, license, and README yourself before use):

- **[OpenBB](https://github.com/OpenBB-finance/OpenBB)** — research/data layer (AGPL-3.0)
- **[TradingAgents](https://github.com/TauricResearch/TradingAgents)** — LLM agent debate (Apache-2.0, research only)
- **[TensorTrade](https://github.com/tensortrade-org/tensortrade)** — RL backtesting (Apache-2.0)
- **[Jesse](https://github.com/jesse-ai/jesse)** — crypto backtesting + paper trading (MIT; keep live OFF)
- **[Qlib](https://github.com/microsoft/qlib)** — quant/risk research (MIT)

## Tests

```bash
python -m unittest discover tests -v
```

---

## Also in this repository: `mfos/`

A separate, self-contained subsystem — **FUTRUINVEST, an AI-led Mutual Fund
Investment Operating System for India**. Different problem, different discipline:
goal-based planning and portfolio management for long-term mutual fund
investors, not equity research and backtesting.

```bash
python mfos/demo.py                                   # end-to-end, synthetic data
cd mfos/tests && python3 -m unittest discover -s .    # 176 tests
```

It shares this repository's central commitment — nothing real ever executes —
and adds a regulatory posture appropriate to giving investment advice in India:
every tax and compliance rule is versioned configuration with a source and a
verification status, the engines refuse to compute with unverified rules, and
the recommendation pipeline halts at the compliance stage because the platform
is not a registered Investment Adviser.

Start at [`mfos/README.md`](mfos/README.md) or the design set at
[`mfos/docs/00-INDEX.md`](mfos/docs/00-INDEX.md).

---

Educational only. Not financial, investment, legal, or tax advice. Trading
involves substantial risk of loss. Backtests and simulations do not guarantee
real results, and this is not a real hedge fund. Live trading and withdrawals
stay disabled. Verify all repositories and licenses before use.
