import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main


def demo_cfg():
    return {
        "markets": {"asset": "DEMO", "min_bars": 250},
        "strategy": {
            "name": "t", "fast_period": 20, "slow_period": 50,
            "fee_pct": 0.1, "slippage_pct": 0.05, "position_pct": 100.0,
            "stop_loss_pct": 6.0, "out_of_sample_pct": 30,
        },
        "risk": {
            "max_risk_per_trade_pct": 1.0, "max_portfolio_drawdown_pct": 15.0,
            "max_open_positions": 5, "kill_switch": True,
            "require_human_approval": True,
            "live_trading": False, "allow_withdrawals": False,
        },
    }


class TestData(unittest.TestCase):
    def test_synthetic_data_validates(self):
        cfg = demo_cfg()
        data = main.fetch_market_data(cfg)
        self.assertTrue(data["synthetic"])
        self.assertEqual(main.validate_inputs(data, cfg), [])

    def test_short_history_rejected(self):
        cfg = demo_cfg()
        data = main.fetch_market_data(cfg)
        data["bars"] = data["bars"][:10]
        self.assertTrue(main.validate_inputs(data, cfg))


class TestBacktest(unittest.TestCase):
    def test_backtest_includes_costs_and_oos_split(self):
        cfg = demo_cfg()
        data = main.fetch_market_data(cfg)
        strat = main.build_strategy({"winner": "bull"}, cfg)
        bt = main.run_backtest(strat, data)
        self.assertTrue(bt["fees_included"] and bt["slippage_included"])
        for key in ("in_sample", "out_of_sample"):
            m = bt[key]
            self.assertIn("max_drawdown_pct", m)
            self.assertGreaterEqual(m["max_drawdown_pct"], 0)


def make_bars(closes):
    bars = []
    prev = closes[0]
    for i, c in enumerate(closes):
        o = prev
        bars.append({
            "date": f"2025-{1 + i // 28:02d}-{1 + i % 28:02d}",
            "open": o, "high": max(o, c) * 1.001,
            "low": min(o, c) * 0.999, "close": c, "volume": 1000,
        })
        prev = c
    return bars


class TestStopLoss(unittest.TestCase):
    def strategy(self, stop_pct):
        return {
            "name": "t", "fast": 3, "slow": 6, "fee_pct": 0.0,
            "slippage_pct": 0.0, "position_pct": 100.0,
            "stop_loss_pct": stop_pct, "out_of_sample_pct": 0,
        }

    def crash_series(self):
        # flat, dip-and-cross-up to trigger a fresh-cross entry, then one
        # sharp intrabar crash followed by a slow drift so the crossover
        # exit lags well behind the stop
        closes = [100.0] * 6 + [99.0, 98.0, 101.0, 103.0, 105.0, 106.0]
        closes += [74.0] + [74.0 * 0.998 ** i for i in range(1, 15)]
        return make_bars(closes)

    def test_stop_caps_single_trade_loss(self):
        bars = self.crash_series()
        with_stop = main._backtest_slice(self.strategy(5.0), bars)
        self.assertEqual(with_stop["num_trades"], 1)
        # worst trade loss is near the 5% stop, far from the full crash depth
        self.assertGreater(with_stop["avg_trade_pct"], -8.0)

    def test_no_stop_rides_the_crash_deeper(self):
        bars = self.crash_series()
        no_stop = main._backtest_slice(self.strategy(0.0), bars)
        with_stop = main._backtest_slice(self.strategy(5.0), bars)
        self.assertLess(no_stop["total_return_pct"], with_stop["total_return_pct"])

    def test_no_reentry_while_trend_unchanged(self):
        # after a stop-out, no new entry until the fast SMA crosses up again
        bars = self.crash_series()
        result = main._backtest_slice(self.strategy(5.0), bars)
        self.assertEqual(result["num_trades"], 1)


class TestRiskGate(unittest.TestCase):
    def test_live_trading_halts(self):
        cfg = demo_cfg()
        cfg["risk"]["live_trading"] = True
        strat = main.build_strategy({"winner": "bull"}, cfg)
        bt = {"in_sample": {"max_drawdown_pct": 1.0}}
        with self.assertRaises(SystemExit):
            main.run_risk_checks(strat, bt, cfg)

    def test_withdrawals_halt(self):
        cfg = demo_cfg()
        cfg["risk"]["allow_withdrawals"] = True
        strat = main.build_strategy({"winner": "bull"}, cfg)
        bt = {"in_sample": {"max_drawdown_pct": 1.0}}
        with self.assertRaises(SystemExit):
            main.run_risk_checks(strat, bt, cfg)

    def test_drawdown_breach_blocks(self):
        cfg = demo_cfg()
        strat = main.build_strategy({"winner": "bull"}, cfg)
        bt = {"in_sample": {"max_drawdown_pct": 99.0}}
        result = main.run_risk_checks(strat, bt, cfg)
        self.assertFalse(result["approved"])
        self.assertIn("drawdown_ok", result["blocked_on"])

    def test_clean_trade_approved(self):
        cfg = demo_cfg()
        strat = main.build_strategy({"winner": "bull"}, cfg)
        bt = {"in_sample": {"max_drawdown_pct": 5.0}}
        self.assertTrue(main.run_risk_checks(strat, bt, cfg)["approved"])


class TestLLMWiring(unittest.TestCase):
    def setUp(self):
        import os
        self._saved = {k: os.environ.pop(k, None)
                       for k in ("ANTHROPIC_API_KEY", "LLM_API_KEY")}

    def tearDown(self):
        import os
        for k, v in self._saved.items():
            if v is not None:
                os.environ[k] = v

    def test_no_key_returns_none(self):
        import llm
        self.assertIsNone(llm.run_llm_agent("bull", {"asset": "X"}, demo_cfg()))

    def test_agents_fall_back_deterministic_without_key(self):
        brief = {"asset": "X", "trend": "down", "sma_fast": 1, "sma_slow": 2,
                 "rsi14": 50.0, "return_3m_pct": -5.0, "annualized_vol_pct": 20.0}
        bull = main.run_bull_agent(brief, demo_cfg())
        bear = main.run_bear_agent(brief, demo_cfg())
        self.assertEqual(bull["source"], "deterministic")
        self.assertEqual(bear["source"], "deterministic")
        self.assertIn("evidence", bull)
        self.assertIn("invalidation", bear)

    def test_prompt_files_exist_for_wired_roles(self):
        from pathlib import Path
        root = Path(main.__file__).resolve().parent
        for role in ("bull", "bear"):
            self.assertTrue((root / "agents" / role / "PROMPT.md").exists())


class TestDebate(unittest.TestCase):
    def test_no_evidence_rejects(self):
        d = main.run_debate(
            {"evidence": []}, {"evidence": []}
        )
        self.assertEqual(d["recommendation"], "REJECT")

    def test_clear_winner_approves(self):
        d = main.run_debate({"evidence": ["a", "b"]}, {"evidence": []})
        self.assertEqual(d["recommendation"], "APPROVE")
        self.assertEqual(d["winner"], "bull")


if __name__ == "__main__":
    unittest.main()
