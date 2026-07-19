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
            "out_of_sample_pct": 30,
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
