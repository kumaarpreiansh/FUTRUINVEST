import unittest
from datetime import date, timedelta
from decimal import Decimal

import conftest_path as fx
from mfos.errors import InsufficientData, RuleNotVerified
from mfos.risk import (BAND_INDEX, assess, assess_capacity, assess_requirement,
                       assess_tolerance, reconcile, riskometer_permitted)
from mfos.tax import ExitLoadBand, Lot, TaxEngine


class TestTaxEngine(unittest.TestCase):
    def setUp(self):
        self.r = fx.registry()
        self.engine = TaxEngine(self.r)
        self.today = date(2026, 8, 25)

    def lots(self):
        return [Lot(1000, "100", date(2020, 5, 1)),      # long term
                Lot(500, "180", date(2026, 6, 1))]        # short term

    def test_fifo_sells_oldest_first(self):
        selected = self.engine.select_lots_fifo(self.lots(), 1200)
        self.assertEqual(selected[0][0].purchase_date, date(2020, 5, 1))
        self.assertEqual(selected[0][1], Decimal("1000"))
        self.assertEqual(selected[1][1], Decimal("200"))

    def test_cannot_sell_more_units_than_held(self):
        with self.assertRaises(InsufficientData):
            self.engine.select_lots_fifo(self.lots(), 5000)

    def test_long_and_short_term_are_separated(self):
        est = self.engine.estimate_redemption(
            lots=self.lots(), units_to_sell=1300, current_nav="250",
            tax_class="EQUITY_ORIENTED", sale_date=self.today)
        self.assertGreater(est.long_term_gain, 0)
        self.assertGreater(est.short_term_gain, 0)

    def test_annual_exemption_is_applied_once(self):
        est = self.engine.estimate_redemption(
            lots=[Lot(1000, "100", date(2020, 5, 1))], units_to_sell=1000,
            current_nav="250", tax_class="EQUITY_ORIENTED", sale_date=self.today)
        self.assertEqual(est.exemption_applied, Decimal("125000.00"))
        used = self.engine.estimate_redemption(
            lots=[Lot(1000, "100", date(2020, 5, 1))], units_to_sell=1000,
            current_nav="250", tax_class="EQUITY_ORIENTED", sale_date=self.today,
            ltcg_exemption_already_used=Decimal("100000"))
        self.assertEqual(used.exemption_applied, Decimal("25000.00"))
        self.assertGreater(used.total_tax, est.total_tax)

    def test_tax_is_labelled_a_lower_bound_while_surcharge_is_unverified(self):
        est = self.engine.estimate_redemption(
            lots=self.lots(), units_to_sell=1000, current_nav="250",
            tax_class="EQUITY_ORIENTED", sale_date=self.today)
        self.assertTrue(est.is_lower_bound)
        self.assertIsNone(est.surcharge)
        self.assertTrue(any("lower bound" in c for c in est.caveats))

    def test_unverified_class_is_refused_not_guessed(self):
        with self.assertRaises(RuleNotVerified):
            self.engine.estimate_redemption(
                lots=self.lots(), units_to_sell=100, current_nav="250",
                tax_class="HYBRID", sale_date=self.today)

    def test_slab_taxed_scheme_asks_for_the_rate(self):
        with self.assertRaises(InsufficientData) as ctx:
            self.engine.estimate_redemption(
                lots=self.lots(), units_to_sell=100, current_nav="250",
                tax_class="SPECIFIED_MUTUAL_FUND", sale_date=self.today)
        self.assertIn("marginal rate", ctx.exception.message)

    def test_slab_taxed_scheme_computes_with_the_rate(self):
        est = self.engine.estimate_redemption(
            lots=self.lots(), units_to_sell=1000, current_nav="250",
            tax_class="SPECIFIED_MUTUAL_FUND", sale_date=self.today,
            investor_marginal_rate_pct=30.0)
        self.assertGreater(est.base_tax, 0)
        self.assertEqual(est.long_term_gain, Decimal("0.00"))

    def test_exit_load_only_on_young_lots(self):
        lots = [Lot(1000, "100", date(2020, 5, 1)), Lot(500, "180", date(2026, 6, 1))]
        est = self.engine.estimate_redemption(
            lots=lots, units_to_sell=1300, current_nav="250",
            tax_class="EQUITY_ORIENTED", sale_date=self.today,
            exit_load_bands=[ExitLoadBand(365, Decimal("1.0"))])
        # only the 300 units from the recent lot bear the load
        self.assertEqual(est.exit_load, Decimal("750.00"))

    def test_switch_is_priced_as_a_redemption(self):
        kwargs = dict(lots=self.lots(), units_to_sell=1000, current_nav="250",
                      tax_class="EQUITY_ORIENTED", sale_date=self.today)
        redeem = self.engine.estimate_redemption(**kwargs)
        switch = self.engine.estimate_switch(**kwargs)
        self.assertEqual(redeem.total_tax, switch.total_tax)
        self.assertIn("TAX-SWITCH-IS-REDEMPTION-001", switch.rule_ids)
        self.assertTrue(any("holding period starts again" in c for c in switch.caveats))

    def test_every_estimate_cites_its_rules_and_version(self):
        est = self.engine.estimate_redemption(
            lots=self.lots(), units_to_sell=1000, current_nav="250",
            tax_class="EQUITY_ORIENTED", sale_date=self.today)
        self.assertIn("TAX-EQ-LTCG-001", est.rule_ids)
        self.assertEqual(est.ruleset_version, self.r.tax.version)

    def test_net_proceeds_equal_gross_less_costs(self):
        est = self.engine.estimate_redemption(
            lots=self.lots(), units_to_sell=1300, current_nav="250",
            tax_class="EQUITY_ORIENTED", sale_date=self.today,
            exit_load_bands=[ExitLoadBand(365, Decimal("1.0"))])
        self.assertEqual(est.net_proceeds,
                         est.gross_proceeds - est.exit_load - est.base_tax - est.cess)


class TestRiskEngine(unittest.TestCase):
    def setUp(self):
        self.r = fx.registry()

    def test_capacity_falls_with_a_thinner_buffer(self):
        strong = assess_capacity(fx.sample_profile(emergency_fund=Decimal("900000")), 20)
        weak = assess_capacity(fx.sample_profile(emergency_fund=Decimal("50000")), 20)
        self.assertGreater(strong["score"], weak["score"])

    def test_capacity_falls_with_dependants_and_debt(self):
        base = assess_capacity(fx.sample_profile(dependants=0,
                                                 total_loan_emi=Decimal("10000")), 20)
        loaded = assess_capacity(fx.sample_profile(dependants=3,
                                                   total_loan_emi=Decimal("90000")), 20)
        self.assertGreater(base["score"], loaded["score"])

    def test_tolerance_is_adjusted_by_past_behaviour(self):
        answers = {"drop_reaction": "BUY_MORE", "acceptable_annual_loss": "40+",
                   "experience": "5_10Y", "volatility_preference": "VOLATILE_12PCT"}
        brave = assess_tolerance(fx.sample_profile(risk_tolerance_answers=answers,
                                                   past_market_behaviour="BOUGHT_MORE"))
        panicked = assess_tolerance(fx.sample_profile(risk_tolerance_answers=answers,
                                                      past_market_behaviour="SOLD_IN_PANIC"))
        self.assertGreater(brave["score"], panicked["score"])
        self.assertEqual(brave["stated_score"], panicked["stated_score"])

    def test_unassessed_tolerance_is_unknown_not_zero(self):
        result = assess_tolerance(fx.sample_profile(risk_tolerance_answers={}))
        self.assertIsNone(result["score"])
        self.assertTrue(result["unknown"])

    def test_binding_band_is_the_lower_of_capacity_and_tolerance(self):
        result = assess(fx.sample_profile(emergency_fund=Decimal("30000"),
                                          total_loan_emi=Decimal("110000"),
                                          dependants=3, income_stability="VOLATILE"),
                        self.r, required_return_pct=8.0, horizon_years=15)
        self.assertLessEqual(BAND_INDEX[result.binding_band],
                             BAND_INDEX[result.tolerance["band"]])

    def test_requirement_never_raises_the_ceiling(self):
        cautious = fx.sample_profile(emergency_fund=Decimal("30000"),
                                     total_loan_emi=Decimal("110000"),
                                     dependants=3, income_stability="VOLATILE")
        modest = assess(cautious, self.r, required_return_pct=7.0, horizon_years=15)
        demanding = assess(cautious, self.r, required_return_pct=13.0, horizon_years=15)
        self.assertEqual(modest.binding_band, demanding.binding_band)
        self.assertEqual(modest.max_equity_pct, demanding.max_equity_pct)

    def test_conflicting_dimensions_are_named_not_averaged(self):
        result = assess(
            fx.sample_profile(
                emergency_fund=Decimal("20000"), total_loan_emi=Decimal("120000"),
                dependants=3, income_stability="VOLATILE",
                risk_tolerance_answers={"drop_reaction": "BUY_MORE",
                                        "acceptable_annual_loss": "40+",
                                        "experience": "OVER_10Y",
                                        "volatility_preference": "VOLATILE_12PCT"},
                past_market_behaviour="BOUGHT_MORE"),
            self.r, required_return_pct=9.0, horizon_years=15)
        types = {c["type"] for c in result.conflicts}
        self.assertIn("TOLERANCE_EXCEEDS_CAPACITY", types)
        self.assertIn("single_label_refused", result.as_dict())

    def test_impossible_goal_is_flagged_critical(self):
        result = assess(fx.sample_profile(), self.r, required_return_pct=30.0,
                        horizon_years=10)
        self.assertIn("GOAL_UNATTAINABLE_AT_ANY_RISK",
                      {c["type"] for c in result.conflicts})

    def test_riskometer_ceiling_binds_for_conservative_only(self):
        self.assertFalse(riskometer_permitted("CONSERVATIVE", "VERY_HIGH"))
        self.assertTrue(riskometer_permitted("GROWTH", "VERY_HIGH"))


if __name__ == "__main__":
    unittest.main()
