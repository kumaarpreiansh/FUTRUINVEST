import math
import random
import unittest
from datetime import date, timedelta
from decimal import Decimal

import conftest_path as fx
from mfos import metrics as M
from mfos import scenarios as SC
from mfos import sip as SIP
from mfos.goals import (Goal, contribution_schedule, inflation_adjusted_target,
                        probability_of_reaching, project_goal, required_return)
from mfos.money import fmt_inr, money


def series(days=2000, drift=0.0004, vol=0.01, seed=1, start_nav=100.0):
    rng = random.Random(seed)
    start = date.today() - timedelta(days=days)
    nav, out = start_nav, []
    for i in range(days):
        nav *= (1 + rng.gauss(drift, vol))
        out.append((start + timedelta(days=i), nav))
    return out


class TestMetrics(unittest.TestCase):
    def test_cagr_of_known_doubling(self):
        s = [(date(2020, 1, 1), 100.0), (date(2030, 1, 1), 200.0)]
        self.assertAlmostEqual(M.cagr(s), 2 ** 0.1 - 1, places=3)

    def test_cagr_refuses_under_one_year(self):
        s = [(date(2026, 1, 1), 100.0), (date(2026, 6, 1), 120.0)]
        self.assertIsNone(M.cagr(s))

    def test_xirr_on_simple_flows(self):
        flows = [(date(2020, 1, 1), -100000.0), (date(2025, 1, 1), 161051.0)]
        self.assertAlmostEqual(M.xirr(flows), 0.10, places=2)

    def test_xirr_needs_both_signs(self):
        self.assertIsNone(M.xirr([(date(2020, 1, 1), -100.0), (date(2021, 1, 1), -100.0)]))

    def test_max_drawdown_is_positive_fraction(self):
        s = [(date(2020, 1, 1), 100.0), (date(2020, 6, 1), 150.0), (date(2021, 1, 1), 75.0)]
        self.assertAlmostEqual(M.max_drawdown(s), 0.5, places=6)

    def test_short_series_return_none_not_zero(self):
        s = series(days=10)
        for fn in (M.volatility, M.sharpe, M.sortino, M.max_drawdown):
            value = fn(s)
            self.assertTrue(value is None or isinstance(value, float))
        self.assertIsNone(M.volatility(s))

    def test_rolling_returns_and_win_rate(self):
        good = series(seed=1, drift=0.0006)
        bench = series(seed=1, drift=0.0004)
        rate = M.rolling_outperformance_rate(good, bench, 3)
        self.assertIsNotNone(rate)
        self.assertGreater(rate, 0.5)

    def test_percentile_and_quartile(self):
        pop = [1, 2, 3, 4, 5, 6, 7, 8]
        self.assertEqual(M.quartile(8, pop), 1)
        self.assertEqual(M.quartile(1, pop), 4)
        self.assertEqual(M.quartile(1, pop, higher_is_better=False), 1)

    def test_sip_xirr_differs_from_point_to_point(self):
        s = series(days=2000, seed=5)
        sip_return = M.sip_xirr(s, 10000)
        self.assertIsNotNone(sip_return)
        self.assertNotAlmostEqual(sip_return, M.cagr(s), places=4)


class TestGoals(unittest.TestCase):
    def setUp(self):
        self.r = fx.registry()

    def test_projection_is_deterministic(self):
        goal = fx.sample_goal()
        a = project_goal(goal, 10.0, 14.0, 6.0)
        b = project_goal(goal, 10.0, 14.0, 6.0)
        self.assertEqual(a.probability, b.probability)
        self.assertEqual(a.corpus_p50, b.corpus_p50)

    def test_inflation_raises_the_target(self):
        goal = fx.sample_goal()
        low = inflation_adjusted_target(goal, 4.0)
        high = inflation_adjusted_target(goal, 8.0)
        self.assertGreater(high, low)
        self.assertGreater(low, goal.target_amount_today)

    def test_more_contribution_raises_probability(self):
        low = project_goal(fx.sample_goal(monthly_contribution=Decimal("20000")),
                           10.0, 14.0, 6.0)
        high = project_goal(fx.sample_goal(monthly_contribution=Decimal("60000")),
                            10.0, 14.0, 6.0)
        self.assertGreater(high.probability, low.probability)

    def test_more_volatility_at_same_return_does_not_raise_probability(self):
        goal = fx.sample_goal(monthly_contribution=Decimal("60000"))
        calm = project_goal(goal, 10.0, 8.0, 6.0)
        wild = project_goal(goal, 10.0, 22.0, 6.0)
        self.assertLessEqual(wild.probability, calm.probability)
        self.assertGreater(wild.corpus_p90, calm.corpus_p90)
        self.assertLess(wild.corpus_p10, calm.corpus_p10)

    def test_probability_is_a_fraction(self):
        for contribution in (0, 10000, 100000, 500000):
            p = project_goal(fx.sample_goal(monthly_contribution=Decimal(contribution)),
                             10.0, 14.0, 6.0).probability
            self.assertGreaterEqual(p, 0.0)
            self.assertLessEqual(p, 1.0)

    def test_required_return_matches_projection(self):
        goal = fx.sample_goal()
        req = required_return(goal, 6.0)
        self.assertIsNotNone(req)
        at_required = project_goal(goal, req * 100, 0.0001, 6.0)
        self.assertGreater(at_required.probability, 0.45)

    def test_step_up_builds_a_growing_schedule(self):
        goal = fx.sample_goal(annual_step_up_pct=10, years_to_goal=3)
        flows = contribution_schedule(goal)
        self.assertEqual(len(flows), 36)
        self.assertGreater(flows[-1][1], flows[0][1])

    def test_zero_horizon_is_refused(self):
        from mfos.errors import InsufficientData
        with self.assertRaises(InsufficientData):
            project_goal(fx.sample_goal(years_to_goal=0), 10.0, 14.0, 6.0)


class TestScenarios(unittest.TestCase):
    def setUp(self):
        self.r = fx.registry()
        self.alloc = {"EQUITY_FLEXI": 40, "EQUITY_LARGE": 15, "EQUITY_MID": 15,
                      "EQUITY_SMALL": 5, "DEBT_MEDIUM": 15, "GOLD": 10}

    def test_diversification_reduces_volatility(self):
        """Portfolio vol must be below the weighted sum of asset vols."""
        cma = self.r.allocation["capital_market_assumptions"]["classes"]
        naive = sum(w / 100 * float(cma[a]["volatility"]) for a, w in self.alloc.items())
        actual = SC.portfolio_assumptions(self.alloc, self.r)["volatility_pct"]
        self.assertLess(actual, naive)

    def test_scenarios_are_ordered(self):
        c = SC.portfolio_assumptions(self.alloc, self.r, "conservative")
        b = SC.portfolio_assumptions(self.alloc, self.r, "base")
        o = SC.portfolio_assumptions(self.alloc, self.r, "optimistic")
        self.assertLess(c["expected_return_pct"], b["expected_return_pct"])
        self.assertLess(b["expected_return_pct"], o["expected_return_pct"])
        self.assertGreater(c["inflation_pct"], o["inflation_pct"])

    def test_glide_path_reduces_equity_near_the_goal(self):
        far = SC.glide_path_allocation(self.alloc, self.r, 20)
        near = SC.glide_path_allocation(self.alloc, self.r, 1)
        equity = lambda a: sum(v for k, v in a.items() if k.startswith("EQUITY"))
        self.assertAlmostEqual(equity(far), equity(self.alloc))
        self.assertLess(equity(near), equity(far))
        self.assertAlmostEqual(sum(near.values()), 100, places=6)

    def test_every_stress_reduces_or_holds_probability(self):
        goal = fx.sample_goal(monthly_contribution=Decimal("60000"))
        for result in SC.run_all_stresses(goal, self.alloc, self.r):
            self.assertLessEqual(result.stressed_probability,
                                 result.baseline_probability + 1e-6, result.stress_id)

    def test_what_if_reports_a_delta(self):
        goal = fx.sample_goal()
        result = SC.what_if(goal, self.alloc, self.r, monthly_contribution=90000)
        self.assertGreater(result["probability_delta_pp"], 0)


class TestSIP(unittest.TestCase):
    def setUp(self):
        self.r = fx.registry()
        self.alloc = {"EQUITY_FLEXI": 50, "DEBT_MEDIUM": 35, "GOLD": 15}

    def test_step_up_ladder_is_monotonic(self):
        rows = SIP.step_up_comparison(fx.sample_goal(annual_step_up_pct=0),
                                      self.alloc, self.r)
        probs = [r["probability_pct"] for r in rows]
        self.assertEqual(probs, sorted(probs))

    def test_lifecycle_is_logged(self):
        s = SIP.SIP("S1", "G1", "FLX01", Decimal("10000"))
        s.pause().restart().step_up(10).modify(Decimal("12000"))
        actions = [h["action"] for h in s.history]
        self.assertEqual(actions, ["PAUSE", "RESTART", "STEP_UP", "MODIFY"])

    def test_unreachable_target_says_so(self):
        goal = fx.sample_goal(target_amount_today=Decimal("500000000"), years_to_goal=5)
        result = SIP.required_sip_for_target_probability(goal, self.alloc, self.r, 0.9,
                                                         max_monthly=50000)
        self.assertFalse(result["reachable"])
        self.assertIn("needs to change", result["message"])

    def test_discipline_ignores_transaction_count(self):
        s = SIP.SIP("S1", "G1", "FLX01", Decimal("10000"))
        metrics = SIP.discipline_metrics([s], 24)
        self.assertIn("sip_consistency_pct", metrics)
        self.assertNotIn("switches", metrics)
        self.assertNotIn("trades", metrics)


class TestMoney(unittest.TestCase):
    def test_indian_grouping(self):
        self.assertEqual(fmt_inr(12345678), "₹1,23,45,678")
        self.assertEqual(fmt_inr(1234), "₹1,234")
        self.assertEqual(fmt_inr(-1234.50), "-₹1,234.50")

    def test_money_is_decimal_not_float(self):
        self.assertIsInstance(money("10.005"), Decimal)
        self.assertEqual(str(money("10.005")), "10.01")


if __name__ == "__main__":
    unittest.main()
