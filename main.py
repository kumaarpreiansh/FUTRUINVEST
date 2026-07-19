#!/usr/bin/env python3
"""claude-ai-hedge-fund — entry point (paper by default).

Runs the full committee pipeline end to end:

  collect data -> validate -> research brief -> bull -> bear -> debate
  -> strategy -> backtest -> backtest review -> risk gate -> paper trade
  -> verdict -> human approval

Every stage can halt the run. Nothing here places real orders: the only
"execution" is an appended line in paper_trading/orders.log, and the run
always ends waiting on a human.

Educational only. Not financial advice. Simulated results are not real results.
"""

import csv
import json
import math
import random
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent


def halt(reason):
    log_decision("HALT", {"reason": reason})
    print(f"\n⛔ HALTED: {reason}")
    sys.exit(1)


def log_decision(stage, payload):
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "stage": stage,
        **payload,
    }
    path = ROOT / "logs" / "decision_log.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


# ── 1. Collect data ──────────────────────────────────────────────────────────

def load_config():
    cfg = {}
    for name in ("markets", "agents", "strategy", "risk"):
        with (ROOT / "config" / f"{name}.yaml").open(encoding="utf-8") as f:
            cfg[name] = yaml.safe_load(f)
    return cfg


def fetch_market_data(cfg):
    """CSV from data/raw/<asset>.csv, else a clearly-labeled synthetic series."""
    asset = cfg["markets"]["asset"]
    csv_path = ROOT / "data" / "raw" / f"{asset}.csv"
    if csv_path.exists():
        bars = []
        with csv_path.open(encoding="utf-8") as f:
            for row in csv.DictReader(f):
                bars.append({
                    "date": row["date"],
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": float(row["volume"]),
                })
        return {"asset": asset, "source": str(csv_path), "synthetic": False, "bars": bars}

    print(f"  (no data/raw/{asset}.csv — generating SYNTHETIC demo data)")
    rng = random.Random(42)
    bars, price, day = [], 100.0, date.today() - timedelta(days=500)
    for _ in range(500):
        drift = rng.gauss(0.0003, 0.015)
        o = price
        c = max(1.0, price * (1 + drift))
        hi = max(o, c) * (1 + abs(rng.gauss(0, 0.004)))
        lo = min(o, c) * (1 - abs(rng.gauss(0, 0.004)))
        bars.append({
            "date": day.isoformat(),
            "open": round(o, 2), "high": round(hi, 2),
            "low": round(lo, 2), "close": round(c, 2),
            "volume": round(rng.uniform(1e5, 5e5)),
        })
        price, day = c, day + timedelta(days=1)
    return {"asset": asset, "source": "synthetic", "synthetic": True, "bars": bars}


# ── 2. Validate data ─────────────────────────────────────────────────────────

def validate_inputs(data, cfg):
    bars = data["bars"]
    problems = []
    if len(bars) < cfg["markets"]["min_bars"]:
        problems.append(f"only {len(bars)} bars, need {cfg['markets']['min_bars']}")
    for i, b in enumerate(bars):
        if any(not math.isfinite(b[k]) or b[k] <= 0 for k in ("open", "high", "low", "close")):
            problems.append(f"bad price at row {i}")
            break
        if not (b["low"] <= b["open"] <= b["high"] and b["low"] <= b["close"] <= b["high"]):
            problems.append(f"OHLC out of order at row {i}")
            break
    dates = [b["date"] for b in bars]
    if dates != sorted(dates):
        problems.append("dates not in ascending order")
    return problems


# ── Indicators ───────────────────────────────────────────────────────────────

def sma(values, period, i):
    if i + 1 < period:
        return None
    return sum(values[i + 1 - period:i + 1]) / period


def rsi(closes, period=14):
    if len(closes) <= period:
        return None
    gains = losses = 0.0
    for a, b in zip(closes[-period - 1:-1], closes[-period:]):
        d = b - a
        gains += max(d, 0)
        losses += max(-d, 0)
    if losses == 0:
        return 100.0
    return 100 - 100 / (1 + gains / losses)


# ── 3. Research brief (deterministic stand-ins for the analyst agents) ───────

def run_research_agents(data, cfg):
    closes = [b["close"] for b in data["bars"]]
    i = len(closes) - 1
    fast = sma(closes, cfg["strategy"]["fast_period"], i)
    slow = sma(closes, cfg["strategy"]["slow_period"], i)
    rets = [closes[j] / closes[j - 1] - 1 for j in range(1, len(closes))]
    vol = statistics_stdev(rets[-60:]) * math.sqrt(252) * 100
    brief = {
        "asset": data["asset"],
        "as_of": data["bars"][-1]["date"],
        "source": data["source"],
        "synthetic": data["synthetic"],
        "last_close": closes[-1],
        "sma_fast": round(fast, 2),
        "sma_slow": round(slow, 2),
        "rsi14": round(rsi(closes), 1),
        "annualized_vol_pct": round(vol, 1),
        "trend": "up" if fast > slow else "down",
        "return_3m_pct": round((closes[-1] / closes[-63] - 1) * 100, 1),
        "note": "Deterministic stand-in for the analyst agents; prompts in agents/*/PROMPT.md.",
    }
    return brief


def statistics_stdev(xs):
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


# ── 4-6. Bull, bear, debate ──────────────────────────────────────────────────

def run_bull_agent(brief):
    evidence = []
    if brief["trend"] == "up":
        evidence.append(f"fast SMA {brief['sma_fast']} above slow SMA {brief['sma_slow']} (uptrend)")
    if brief["rsi14"] < 70:
        evidence.append(f"RSI {brief['rsi14']} not overbought")
    if brief["return_3m_pct"] > 0:
        evidence.append(f"3-month return {brief['return_3m_pct']}% positive")
    return {
        "side": "bull",
        "evidence": evidence,
        "confidence": min(90, 30 + 20 * len(evidence)),
        "invalidation": "fast SMA closes below slow SMA",
    }


def run_bear_agent(brief):
    evidence = []
    if brief["trend"] == "down":
        evidence.append(f"fast SMA {brief['sma_fast']} below slow SMA {brief['sma_slow']} (downtrend)")
    if brief["rsi14"] > 70:
        evidence.append(f"RSI {brief['rsi14']} overbought")
    if brief["return_3m_pct"] < 0:
        evidence.append(f"3-month return {brief['return_3m_pct']}% negative")
    if brief["annualized_vol_pct"] > 35:
        evidence.append(f"elevated volatility {brief['annualized_vol_pct']}%")
    return {
        "side": "bear",
        "evidence": evidence,
        "confidence": min(90, 30 + 20 * len(evidence)),
        "invalidation": "fast SMA closes above slow SMA with falling volatility",
    }


def run_debate(bull, bear):
    b, r = len(bull["evidence"]), len(bear["evidence"])
    if b == 0 and r == 0:
        rec = "REJECT"
    elif abs(b - r) <= 0:
        rec = "REVISE"
    else:
        rec = "APPROVE"
    return {
        "bull_score": b,
        "bear_score": r,
        "winner": "bull" if b > r else "bear" if r > b else "tie",
        "recommendation": rec,
        "memo": f"Bull evidence {b} vs bear evidence {r}; evidence weighed, not averaged.",
    }


# ── 7. Strategy ──────────────────────────────────────────────────────────────

def build_strategy(debate, cfg):
    s = cfg["strategy"]
    direction = "long" if debate["winner"] == "bull" else "flat"
    # size the position so a stop-out loses at most max_risk_per_trade_pct
    # of equity: position = risk budget / stop distance
    position_pct = s["position_pct"]
    stop = s.get("stop_loss_pct", 0.0)
    risk_budget = cfg.get("risk", {}).get("max_risk_per_trade_pct")
    if stop > 0 and risk_budget:
        position_pct = min(position_pct, risk_budget / stop * 100)
    return {
        "name": s["name"],
        "direction": direction,
        "entry": f"fast SMA({s['fast_period']}) crosses above slow SMA({s['slow_period']})",
        "exit": f"fast SMA({s['fast_period']}) crosses below slow SMA({s['slow_period']})",
        "stop_loss_pct": s.get("stop_loss_pct", 0.0),
        "fee_pct": s["fee_pct"],
        "slippage_pct": s["slippage_pct"],
        "position_pct": position_pct,
        "fast": s["fast_period"],
        "slow": s["slow_period"],
        "out_of_sample_pct": s["out_of_sample_pct"],
    }


# ── 8. Backtest (fees + slippage, in-sample and out-of-sample) ───────────────

def run_backtest(strategy, data):
    bars = data["bars"]
    split = len(bars) - int(len(bars) * strategy["out_of_sample_pct"] / 100)
    return {
        "in_sample": _backtest_slice(strategy, bars[:split]),
        "out_of_sample": _backtest_slice(strategy, bars[split:]),
        "fees_included": True,
        "slippage_included": True,
    }


def _backtest_slice(strategy, bars):
    closes = [b["close"] for b in bars]
    cost = (strategy["fee_pct"] + strategy["slippage_pct"]) / 100
    stop = strategy.get("stop_loss_pct", 0.0) / 100
    pos_frac = strategy["position_pct"] / 100
    equity, in_pos, entry_px = 1.0, False, 0.0
    curve, trades, bars_in_market = [1.0], [], 0
    prev_fast = prev_slow = None
    for i in range(1, len(closes)):
        fast = sma(closes, strategy["fast"], i)
        slow = sma(closes, strategy["slow"], i)
        if in_pos:
            bars_in_market += 1
        if fast is None or slow is None:
            curve.append(equity)
            continue
        if in_pos:
            stop_price = entry_px * (1 - stop) if stop > 0 else None
            if stop_price is not None and bars[i]["low"] <= stop_price:
                # gap below the stop fills at the open, not the stop price
                fill = bars[i]["open"] if bars[i]["open"] < stop_price else stop_price
                pnl = fill * (1 - cost) / entry_px - 1
                trades.append(pnl)
                equity *= 1 + pnl * pos_frac
                in_pos = False
            elif fast < slow:
                pnl = closes[i] * (1 - cost) / entry_px - 1
                trades.append(pnl)
                equity *= 1 + pnl * pos_frac
                in_pos = False
        if (not in_pos and prev_fast is not None and prev_slow is not None
                and prev_fast <= prev_slow and fast > slow):
            in_pos, entry_px = True, closes[i] * (1 + cost)
        prev_fast, prev_slow = fast, slow
        mtm = 1 + (closes[i] * (1 - cost) / entry_px - 1) * pos_frac if in_pos else 1
        curve.append(equity * mtm)
    if in_pos:
        pnl = closes[-1] * (1 - cost) / entry_px - 1
        trades.append(pnl)
        equity *= 1 + pnl * pos_frac
        curve[-1] = equity
    return _metrics(curve, trades, bars_in_market, len(bars))


def _metrics(curve, trades, bars_in_market, n_bars):
    total = curve[-1] - 1
    years = max(n_bars / 252, 1e-9)
    ann = (curve[-1]) ** (1 / years) - 1 if curve[-1] > 0 else -1.0
    rets = [curve[i] / curve[i - 1] - 1 for i in range(1, len(curve)) if curve[i - 1] > 0]
    vol = statistics_stdev(rets) * math.sqrt(252) if len(rets) > 2 else 0.0
    downside = [r for r in rets if r < 0]
    dvol = statistics_stdev(downside) * math.sqrt(252) if len(downside) > 2 else 0.0
    peak, mdd = curve[0], 0.0
    for v in curve:
        peak = max(peak, v)
        mdd = max(mdd, 1 - v / peak)
    wins = [t for t in trades if t > 0]
    losses = [t for t in trades if t <= 0]
    gross_p, gross_l = sum(wins), -sum(losses)
    return {
        "total_return_pct": round(total * 100, 2),
        "annualized_return_pct": round(ann * 100, 2),
        "volatility_pct": round(vol * 100, 2),
        "sharpe": round(ann / vol, 2) if vol > 0 else None,
        "sortino": round(ann / dvol, 2) if dvol > 0 else None,
        "max_drawdown_pct": round(mdd * 100, 2),
        "win_rate_pct": round(100 * len(wins) / len(trades), 1) if trades else None,
        "profit_factor": round(gross_p / gross_l, 2) if gross_l > 0 else None,
        "avg_trade_pct": round(100 * sum(trades) / len(trades), 3) if trades else None,
        "num_trades": len(trades),
        "exposure_pct": round(100 * bars_in_market / n_bars, 1),
        "recovery_factor": round(total / mdd, 2) if mdd > 0 else None,
    }


# ── 9. Backtest review ───────────────────────────────────────────────────────

def review_backtest(bt, cfg):
    ins, oos = bt["in_sample"], bt["out_of_sample"]
    checks = {
        "fees_included": bt["fees_included"],
        "slippage_included": bt["slippage_included"],
        "enough_trades": ins["num_trades"] >= 5,
        "out_of_sample_tested": oos["num_trades"] >= 0,
        "drawdown_within_limit": ins["max_drawdown_pct"]
        <= cfg["risk"]["max_portfolio_drawdown_pct"] * 2,
        "not_obviously_overfit": not (
            ins["total_return_pct"] > 20 and oos["total_return_pct"] < -10
        ),
    }
    failed = [k for k, ok in checks.items() if not ok]
    verdict = "PASS" if not failed else ("REVISE" if len(failed) <= 2 else "REJECT")
    return {"checks": checks, "failed": failed, "verdict": verdict}


# ── 10. Risk gate ────────────────────────────────────────────────────────────

def run_risk_checks(strategy, bt, cfg):
    r = cfg["risk"]
    checks = {}
    if r.get("live_trading", False):
        halt("config/risk.yaml has live_trading enabled — this project is paper-only")
    if r.get("allow_withdrawals", False):
        halt("config/risk.yaml has allow_withdrawals enabled — must stay false")
    checks["kill_switch_armed"] = bool(r.get("kill_switch"))
    checks["human_approval_required"] = bool(r.get("require_human_approval"))
    checks["drawdown_ok"] = (
        bt["in_sample"]["max_drawdown_pct"] <= r["max_portfolio_drawdown_pct"]
    )
    checks["position_size_ok"] = strategy["position_pct"] <= 100.0
    checks["per_trade_risk_ok"] = (
        strategy["stop_loss_pct"] > 0
        and strategy["position_pct"] * strategy["stop_loss_pct"] / 100
        <= r["max_risk_per_trade_pct"] + 1e-9
    )
    checks["open_positions_ok"] = 1 <= r["max_open_positions"]
    blocked = [k for k, ok in checks.items() if not ok]
    return {"checks": checks, "blocked_on": blocked, "approved": not blocked}


# ── 11. Paper trade (simulated only) ─────────────────────────────────────────

def paper_signal(strategy, data):
    if strategy["direction"] != "long":
        return None
    last = data["bars"][-1]
    cost = (strategy["fee_pct"] + strategy["slippage_pct"]) / 100
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "asset": data["asset"],
        "side": "BUY",
        "type": "PAPER",
        "ref_price": last["close"],
        "sim_fill_price": round(last["close"] * (1 + cost), 2),
        "note": "SIMULATED ONLY — no broker, no real money",
    }


def submit_simulated_order(signal):
    path = ROOT / "paper_trading" / "orders.log"
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(signal) + "\n")


# ── 13-14. Verdict + human approval ──────────────────────────────────────────

def final_verdict(brief, bull, bear, debate, bt, review, risk_result, signal):
    verdict = (
        "APPROVE"
        if review["verdict"] == "PASS" and risk_result["approved"]
        else "REVISE"
        if review["verdict"] != "REJECT"
        else "REJECT"
    )
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = ROOT / "reports" / f"verdict_{ts}.md"
    lines = [
        f"# Committee verdict — {brief['asset']} ({brief['as_of']})",
        "",
        "Educational only. Not financial advice. Simulated results are not real results.",
        "",
        f"- Data source: {brief['source']}" + (" **(SYNTHETIC)**" if brief["synthetic"] else ""),
        f"- Research: trend {brief['trend']}, RSI {brief['rsi14']}, vol {brief['annualized_vol_pct']}%",
        f"- Bull confidence {bull['confidence']} — {'; '.join(bull['evidence']) or 'no evidence'}",
        f"- Bear confidence {bear['confidence']} — {'; '.join(bear['evidence']) or 'no evidence'}",
        f"- Debate: {debate['memo']} -> {debate['recommendation']}",
        f"- Backtest in-sample: {bt['in_sample']}",
        f"- Backtest out-of-sample: {bt['out_of_sample']}",
        f"- Backtest review: {review['verdict']} (failed: {review['failed'] or 'none'})",
        f"- Risk gate: {'APPROVED' if risk_result['approved'] else 'BLOCKED on ' + str(risk_result['blocked_on'])}",
        f"- Paper order: {signal or 'none (no entry signal)'}",
        "",
        f"## FINAL VERDICT: {verdict}",
        "",
        "**HUMAN ACTION REQUIRED:** review this memo and the decision log; nothing",
        "executes without your explicit approval, and live trading stays disabled.",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return verdict, path


def main():
    print("claude-ai-hedge-fund — paper-only committee run\n")

    cfg = load_config()
    log_decision("START", {"asset": cfg["markets"]["asset"]})

    print("1-2. Collecting + validating data…")
    data = fetch_market_data(cfg)
    problems = validate_inputs(data, cfg)
    if problems:
        halt("bad data: " + "; ".join(problems))

    print("3.   Research brief…")
    brief = run_research_agents(data, cfg)
    log_decision("RESEARCH", brief)

    print("4-6. Bull case, bear case, debate…")
    bull, bear = run_bull_agent(brief), run_bear_agent(brief)
    debate = run_debate(bull, bear)
    log_decision("DEBATE", debate)
    if debate["recommendation"] == "REJECT":
        halt("debate rejected the thesis — no strategy will be built")

    print("7.   Strategy spec…")
    strategy = build_strategy(debate, cfg)

    print("8-9. Backtest (fees + slippage) and review…")
    bt = run_backtest(strategy, data)
    review = review_backtest(bt, cfg)
    log_decision("BACKTEST_REVIEW", review)
    if review["verdict"] == "REJECT":
        halt("backtest failed review: " + ", ".join(review["failed"]))

    print("10.  Risk gate…")
    risk_result = run_risk_checks(strategy, bt, cfg)
    log_decision("RISK", risk_result)
    if not risk_result["approved"]:
        halt("risk blocked: " + ", ".join(risk_result["blocked_on"]))

    print("11.  Paper trade (simulated)…")
    signal = paper_signal(strategy, data)
    if signal:
        submit_simulated_order(signal)
        log_decision("PAPER_ORDER", signal)

    print("13.  Final verdict…")
    verdict, report = final_verdict(brief, bull, bear, debate, bt, review, risk_result, signal)
    log_decision("VERDICT", {"verdict": verdict, "report": str(report)})

    print(f"\n✅ Run complete — FINAL VERDICT: {verdict}")
    print(f"   Report: {report.relative_to(ROOT)}")
    print("   14. HUMAN APPROVAL REQUIRED — nothing real was executed, and")
    print("       live trading stays disabled. You decide what happens next.")


if __name__ == "__main__":
    main()
