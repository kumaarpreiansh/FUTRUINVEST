import copy
import unittest
from datetime import datetime, timedelta
from decimal import Decimal

import conftest_path as fx
from mfos.funds import Scheme, data_quality_score
from mfos.portfolio import (Holding, Portfolio, analyse_overlap, pairwise_overlap,
                            rebalance_plan, target_allocation)
from mfos.risk import assess as assess_risk
from mfos.scoring import PROHIBITED, score_universe
from mfos.selection import eligibility, shortlist
from mfos.synthetic import demo_universe


class TestDataQuality(unittest.TestCase):
    def setUp(self):
        self.r = fx.registry()
        self.u = fx.universe()

    def test_synthetic_universe_passes_the_quality_floor(self):
        for scheme in self.u:
            self.assertTrue(data_quality_score(scheme, self.r)["usable"], scheme.scheme_code)

    def test_missing_critical_fields_fail_the_floor(self):
        scheme = Scheme("X1", "Unknown Scheme", "AMC")
        result = data_quality_score(scheme, self.r)
        self.assertFalse(result["usable"])
        self.assertTrue(any(x["issue"] == "MISSING_CRITICAL" for x in result["reasons"]))

    def test_stale_nav_is_penalised(self):
        scheme = copy.deepcopy(self.u.get("FLX01"))
        fresh = data_quality_score(scheme, self.r)["score"]
        scheme.fields["nav"].observations = [
            type(o)(o.value, o.source, datetime.utcnow() - timedelta(hours=800))
            for o in scheme.fields["nav"].observations
        ]
        stale = data_quality_score(scheme, self.r)["score"]
        self.assertLess(stale, fresh)

    def test_source_hierarchy_resolves_disagreement(self):
        scheme = copy.deepcopy(self.u.get("FLX01"))
        now = datetime.utcnow()
        scheme.observe("expense_ratio_pct", 9.99, "VENDOR", now)
        self.assertNotEqual(scheme.fields["expense_ratio_pct"].value, 9.99)
        self.assertTrue(scheme.disagreements())

    def test_unusable_scheme_raises_rather_than_returning_a_guess(self):
        from mfos.errors import DataQualityFailure
        from mfos.funds import require_usable
        with self.assertRaises(DataQualityFailure):
            require_usable(Scheme("X2", "Unknown", "AMC"), self.r)


class TestFundScoring(unittest.TestCase):
    def setUp(self):
        self.r = fx.registry()
        self.u = fx.universe()
        self.scores = fx.scores()

    def test_no_commission_influence_on_ranking(self):
        """The central conflict-of-interest guarantee, verified rather than asserted.

        Every scheme is given a large, deliberately ranking-inverting commission
        and the whole universe is re-scored. If any score moves, a commercial
        field has leaked into the scoring path.
        """
        before = {k: v.get("overall") for k, v in self.scores.items()}
        universe = demo_universe()
        for i, scheme in enumerate(universe):
            scheme.distributor_commission_pct = 3.0 - (i * 0.1)
            scheme.amc_relationship = "PREFERRED_PARTNER"
        after = {k: v.get("overall") for k, v in score_universe(universe, self.r).items()}
        self.assertEqual(before, after)

    def test_scoring_declares_its_prohibited_inputs(self):
        declared = set(self.r.scoring["prohibited_inputs"])
        self.assertIn("distributor_commission_pct", declared)
        self.assertIn("amc_revenue_share", declared)
        self.assertIn("distributor_commission_pct", PROHIBITED)

    def test_direct_plan_beats_its_regular_twin(self):
        direct, regular = self.scores["FLX01"], self.scores["FLX04"]
        self.assertGreater(direct["overall"], regular["overall"])
        self.assertGreater(direct["pillars"]["cost"], regular["pillars"]["cost"])

    def test_skill_is_detected_above_noise(self):
        good, poor = self.scores["FLX01"], self.scores["FLX03"]
        self.assertGreater(good["pillars"]["consistency"], poor["pillars"]["consistency"])

    def test_index_fund_scores_well_on_cost_and_tracking(self):
        index = self.scores["LRG03"]
        self.assertGreaterEqual(index["pillars"]["cost"], 90)
        self.assertGreater(index["pillars"]["tracking"], 50)

    def test_every_score_carries_provenance(self):
        for result in self.scores.values():
            if result.get("overall") is None:
                continue
            self.assertEqual(result["ruleset_version"], self.r.scoring.version)
            self.assertIn("data_quality", result)
            self.assertIn("peer_group", result)
            self.assertIn("methodology_url", result)

    def test_peers_are_within_sebi_category_only(self):
        scheme = self.u.get("FLX01")
        for peer in self.u.peers(scheme):
            self.assertEqual(peer.sebi_category, scheme.sebi_category)

    def test_missing_component_reduces_coverage_not_score_to_zero(self):
        result = self.scores["DBT01"]
        self.assertIsNone(result["pillars"]["risk"])       # no peers for percentiles
        self.assertLess(result["coverage_pct"], 100)
        self.assertGreater(result["overall"], 0)


class TestSelection(unittest.TestCase):
    def setUp(self):
        self.r = fx.registry()
        self.u = fx.universe()
        self.profile = fx.sample_profile()
        self.goal = fx.sample_goal()
        self.risk = assess_risk(self.profile, self.r, required_return_pct=9.0,
                                horizon_years=20)
        self.target = target_allocation(self.r, self.goal, self.risk)

    def test_shortlist_is_short(self):
        result = shortlist(self.u, self.r, asset_class="EQUITY_FLEXI",
                           risk_assessment=self.risk, years_to_goal=20,
                           target_allocation=self.target["weights"])
        self.assertLessEqual(len(result["candidates"]), 3)
        self.assertGreater(result["considered"], len(result["candidates"]))

    def test_suitability_removes_rather_than_downranks(self):
        result = shortlist(self.u, self.r, asset_class="EQUITY_SMALL",
                           risk_assessment=self.risk, years_to_goal=2,
                           target_allocation={"DEBT_SHORT": 100})
        self.assertEqual(result["candidates"], [])
        gates = {g["gate"] for r in result["rejected"] for g in r["failed_gates"]}
        self.assertIn("NOT_IN_PLAN", gates)

    def test_horizon_gate_blocks_long_horizon_assets_for_short_goals(self):
        scheme = self.u.get("SML01")
        ok, reasons = eligibility(scheme, self.r, risk_assessment=self.risk,
                                  years_to_goal=3, asset_class="EQUITY_SMALL",
                                  target_allocation={"EQUITY_SMALL": 100})
        self.assertFalse(ok)
        self.assertIn("HORIZON", {x["gate"] for x in reasons})

    def test_overlap_with_existing_holding_penalises_fit(self):
        portfolio = Portfolio("TEST-1", [Holding("FLX01", Decimal("1000"))])
        result = shortlist(self.u, self.r, asset_class="EQUITY_FLEXI",
                           risk_assessment=self.risk, years_to_goal=20,
                           portfolio=portfolio, target_allocation=self.target["weights"])
        for candidate in result["candidates"]:
            if candidate["scheme_code"] == "FLX04":       # 100% overlap with FLX01
                self.assertLess(candidate["suitability_components"]["portfolio_fit"], 100)

    def test_every_candidate_carries_a_conflict_disclosure(self):
        result = shortlist(self.u, self.r, asset_class="EQUITY_FLEXI",
                           risk_assessment=self.risk, years_to_goal=20,
                           target_allocation=self.target["weights"])
        for candidate in result["candidates"]:
            self.assertIn("statement", candidate["conflict_disclosure"])


class TestPortfolio(unittest.TestCase):
    def setUp(self):
        self.r = fx.registry()
        self.u = fx.universe()
        self.profile = fx.sample_profile()
        self.goal = fx.sample_goal()
        self.risk = assess_risk(self.profile, self.r, required_return_pct=9.0,
                                horizon_years=20)

    def test_target_allocation_respects_the_equity_ceiling(self):
        cautious = fx.sample_profile(emergency_fund=Decimal("20000"),
                                     total_loan_emi=Decimal("130000"),
                                     dependants=3, income_stability="VOLATILE",
                                     risk_tolerance_answers={"drop_reaction": "SELL_ALL",
                                                             "acceptable_annual_loss": "5",
                                                             "experience": "NONE",
                                                             "volatility_preference": "STEADY_6PCT"})
        risk = assess_risk(cautious, self.r, required_return_pct=12.0, horizon_years=20)
        target = target_allocation(self.r, self.goal, risk)
        self.assertLessEqual(target["equity_pct"], risk.max_equity_pct + 1e-6)

    def test_allocation_always_sums_to_100(self):
        target = target_allocation(self.r, self.goal, self.risk)
        self.assertAlmostEqual(sum(target["weights"].values()), 100, places=1)

    def test_overlap_uses_minimum_weight_not_sum(self):
        a = Scheme("A", "A", "X"); a.holdings = {"S1": 10.0, "S2": 5.0}
        b = Scheme("B", "B", "Y"); b.holdings = {"S1": 4.0, "S3": 6.0}
        self.assertEqual(pairwise_overlap(a, b), 4.0)

    def test_same_category_overlap_is_flagged_problematic(self):
        portfolio = Portfolio("TEST-1", [Holding("FLX01", Decimal("1000")),
                                         Holding("FLX04", Decimal("1000"))])
        result = analyse_overlap(portfolio, self.u, self.r)
        self.assertTrue(result["action_needed"])
        self.assertEqual(result["findings"][0]["verdict"], "PROBLEMATIC")

    def test_cross_category_overlap_is_not_alarming(self):
        portfolio = Portfolio("TEST-1", [Holding("FLX01", Decimal("1000")),
                                         Holding("DBT01", Decimal("1000"))])
        result = analyse_overlap(portfolio, self.u, self.r)
        self.assertFalse(result["action_needed"])

    def test_rebalance_prefers_cashflow_over_selling(self):
        portfolio = Portfolio("TEST-1", [Holding("FLX01", Decimal("2000")),
                                         Holding("DBT01", Decimal("100"))])
        target = target_allocation(self.r, self.goal, self.risk)
        plan = rebalance_plan(portfolio, self.u, target, self.r,
                              monthly_inflow=Decimal("100000"),
                              months_since_last_rebalance=24, years_to_goal=20)
        self.assertIn(plan["action"], ("CASHFLOW_REBALANCE", "GRADUAL"))
        self.assertEqual(plan["tax_cost"], "0")

    def test_empty_portfolio_is_built_not_rebalanced(self):
        target = target_allocation(self.r, self.goal, self.risk)
        plan = rebalance_plan(Portfolio("TEST-1", []), self.u, target, self.r)
        self.assertEqual(plan["action"], "BUILD")

    def test_no_rebalance_when_the_portfolio_is_on_target(self):
        target = target_allocation(self.r, self.goal, self.risk)
        by_asset = {"EQUITY_FLEXI": "FLX01", "EQUITY_LARGE": "LRG01",
                    "EQUITY_MID": "MID01", "EQUITY_SMALL": "SML01",
                    "DEBT_MEDIUM": "DBT01", "GOLD": "GLD01"}
        total = 10_000_000.0
        holdings = []
        for asset, weight in target["weights"].items():
            scheme = self.u.get(by_asset[asset])
            holdings.append(Holding(scheme.scheme_code,
                                    Decimal(str(total * weight / 100 / scheme.nav))))
        plan = rebalance_plan(Portfolio("TEST-1", holdings), self.u, target, self.r,
                              monthly_inflow=Decimal("50000"),
                              months_since_last_rebalance=24, years_to_goal=20)
        self.assertEqual(plan["action"], "NONE")

    def test_recent_rebalance_defers(self):
        portfolio = Portfolio("TEST-1", [Holding("FLX01", Decimal("2000")),
                                         Holding("DBT01", Decimal("10"))])
        target = target_allocation(self.r, self.goal, self.risk)
        plan = rebalance_plan(portfolio, self.u, target, self.r,
                              monthly_inflow=Decimal("1000"),
                              months_since_last_rebalance=2, years_to_goal=20)
        self.assertEqual(plan["action"], "DEFER")


if __name__ == "__main__":
    unittest.main()
