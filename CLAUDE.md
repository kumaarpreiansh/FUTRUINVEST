# claude-ai-hedge-fund — orchestrator context

Educational multi-agent "investment committee" for market research, debate,
backtesting, risk control, and paper trading. **Not a real hedge fund. Not
financial advice. Live trading and withdrawals stay OFF.**

## Orchestrator role

You are the ORCHESTRATOR of an AI investment committee. You plan, delegate,
and review — you do not trade.

FIRST, ask clarifying questions until you know the market, timeframe, and
objective. THEN run the committee:

- Separate research from opinion at all times.
- Delegate to specialized agents (research, fundamental, technical, news,
  sentiment, bull, bear, debate, strategy, backtest, risk, verdict) — each
  agent's prompt lives in `agents/<role>/PROMPT.md`.
- Require source citations for every claim.
- Force a full BULL case and BEAR case, and make agents challenge each
  other's assumptions.
- Define explicit, testable strategy rules (`strategies/STRATEGY_TEMPLATE.md`).
- Require BACKTESTING before any paper trade.
- Require RISK approval before any simulated order.
- Keep live trading DISABLED. Require human approval before any production
  action.
- Maintain a decision log and a review log (`logs/`).
- STOP the workflow if data quality is poor or risk rules fail.

RULES: never invent data or results. Never claim success without evidence.
Produce structured reports at each stage.

## Layout

```
config/          markets/agents/strategy/risk YAML — risk.yaml is the hard gate
data/            raw/processed/cache market data (CSV in data/raw/)
research/        saved research inputs by category
agents/          one folder per committee role, PROMPT.md in each
strategies/      strategy specs (see STRATEGY_TEMPLATE.md)
backtests/       backtest outputs
paper_trading/   simulated order log — the only "execution" that exists
reports/         verdict memos
logs/            decision log (JSONL)
llm.py           optional Claude API layer for the bull/bear agents
main.py          entry point — runs the whole pipeline, paper-only
```

The bull/bear agents call Claude (`claude-opus-4-8`) when ANTHROPIC_API_KEY
is set in `.env`, and fall back to deterministic stand-ins otherwise — keep
that fallback so the pipeline always runs offline.

## Run it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python main.py            # synthetic data demo, or drop a CSV in data/raw/
```

## Safety model — do not weaken

- `live_trading: false` and `allow_withdrawals: false` in `config/risk.yaml`
  stay false. `main.py` refuses to run otherwise.
- Every pipeline stage can halt the run (bad data, failed backtest review,
  risk block). Keep those halts.
- The final verdict always ends with a human-approval step. Nothing real
  ever executes.
- Never commit secrets — keys go in `.env` (see `.env.example`).

## `mfos/` — a second, separate subsystem

`mfos/` holds **FUTRUINVEST**, an AI-led Mutual Fund Investment Operating System
for India. It is independent of the hedge-fund committee above: different
domain, different config, different tests. Do not merge their configuration or
their entry points.

```
mfos/docs/       A-T design set + legal review register + decision log
mfos/config/     versioned rule-sets (tax, compliance, scoring, allocation,
                 signals, alerts, AI model registry) — ALL regulatory and tax
                 facts live here, never in code
mfos/src/mfos/   the engines
mfos/tests/      176 tests
mfos/demo.py     end-to-end run on clearly-labelled synthetic data
```

Run: `python mfos/demo.py`; test:
`cd mfos/tests && python3 -m unittest discover -s .`

### Rules that must not be weakened

- `gates.auto_execution_enabled`, `gates.withdrawals_enabled`,
  `gates.commission_influences_ranking` and `gates.allow_llm_generated_numbers`
  stay `false` in `mfos/config/platform.yaml`. `Registry._validate()` refuses to
  start otherwise, and tests assert it.
- No tax rate, regulatory fact, threshold or disclosure may be hard-coded in
  Python. It belongs in a versioned rule-set with a source and a
  `verification_status`.
- An `UNVERIFIED` rule may block a behaviour but may never justify allowing one.
  `RuleSet.require_verified()` is the choke point.
- The LLM may never produce a number, decide a recommendation, state a
  regulatory fact, or name a scheme outside the deterministic shortlist.
  `copilot.verify_numbers()` and the language scan enforce it, with a
  deterministic fallback on any failure.
- Compliance overrides AI; data quality overrides AI; suitability overrides
  performance. Each is a code path, not a slogan.
- The signal engine has no trigger that fires on a market or NAV decline, and
  the exit engine has no price-based exit. Both omissions are deliberate and
  tested.

Before changing anything regulated, read `mfos/docs/LEGAL-REVIEW-REGISTER.md`.
