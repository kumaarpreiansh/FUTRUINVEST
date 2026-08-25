import unittest
from datetime import date
from decimal import Decimal

import conftest_path as fx
from mfos import behaviour, exits, signals, switching
from mfos.alerts import Alert, AlertDispatcher, from_signal, validate
from mfos.errors import ComplianceBlock
from mfos.tax import ExitLoadBand, Lot, TaxEngine


def deteriorating(**extra):
    ctx = dict(rolling_3y_outperformance_rate=0.28, category_quartile_3y=4,
               category_quartile_5y=4, sharpe_percentile=18, sharpe_percentile_3y_ago=62,
               expense_ratio_increase_bps=22, expense_ratio_pct=1.35,
               category_median_expense=0.95, top10_weight_pct=64,
               category_median_top10=45, history_months=96)
    ctx.update(extra)
    return ctx


class TestSignalEngine(unittest.TestCase):
    def setUp(self):
        self.r = fx.registry()

    def evaluate(self, ctx):
        return signals.evaluate(ctx, self.r, scheme_code="X", goal_id="G1")

    def test_a_falling_market_is_not_a_trigger(self):
        signal = self.evaluate(signals.build_context(
            history_months=96, market_drawdown_pct=45, nav_down_6m_pct=38,
            index_down_1y_pct=30))
        self.assertEqual(signal.state, "HOLD")
        self.assertEqual(signal.fired, [])

    def test_one_trigger_never_reaches_an_action_state(self):
        signal = self.evaluate(signals.build_context(
            rolling_3y_outperformance_rate=0.10, history_months=96))
        self.assertFalse(signal.is_actionable)

    def test_two_triggers_are_still_held_below_switch_review(self):
        signal = self.evaluate(signals.build_context(
            rolling_3y_outperformance_rate=0.10, category_quartile_3y=4,
            category_quartile_5y=4, sharpe_percentile=10,
            sharpe_percentile_3y_ago=70, history_months=96))
        self.assertIn(signal.state, ("REVIEW", "WATCH"))

    def test_multiple_triggers_reach_switch_review(self):
        self.assertEqual(self.evaluate(deteriorating()).state, "SWITCH_REVIEW")

    def test_performance_alone_never_reaches_exit_review(self):
        signal = self.evaluate(deteriorating(
            downside_capture=-140, category_median_downside_capture=-100))
        self.assertNotEqual(signal.state, "EXIT_REVIEW")
        self.assertIn("not to leave the asset class", signal.suppressed_reason)

    def test_material_event_can_reach_exit_review(self):
        signal = self.evaluate(deteriorating(
            scheme_material_change=True, scheme_material_change_detail="merger"))
        self.assertEqual(signal.state, "EXIT_REVIEW")

    def test_liquidity_need_routes_straight_to_exit_review(self):
        signal = self.evaluate(signals.build_context(
            declared_liquidity_need_within_months=3, history_months=96))
        self.assertEqual(signal.state, "EXIT_REVIEW")

    def test_dampeners_can_cancel_an_action(self):
        expensive = self.evaluate(deteriorating(
            exit_load_applicable=True, estimated_tax_pct_of_value=5.0,
            months_since_last_action_on_holding=4))
        self.assertEqual(expensive.state, "HOLD")
        self.assertTrue(expensive.dampened)

    def test_thin_history_suppresses_action(self):
        signal = self.evaluate(deteriorating(history_months=20))
        self.assertLess(signal.score, self.evaluate(deteriorating()).score)

    def test_persistence_requires_repeat_observation(self):
        ctx = deteriorating()
        first = signals.evaluate(ctx, self.r, scheme_code="X", goal_id="G1",
                                 observations=[])
        confirmed = signals.evaluate(ctx, self.r, scheme_code="X", goal_id="G1",
                                     observations=[{"TRG-PERF-ROLLING-UNDER",
                                                    "TRG-RISKADJ-DETERIORATION",
                                                    "TRG-PERF-CATEGORY-QUARTILE",
                                                    "TRG-EXPENSE-INCREASE",
                                                    "TRG-CONCENTRATION"}])
        self.assertLess(first.score, confirmed.score)

    def test_allocation_drift_emits_rebalance_not_switch(self):
        signal = self.evaluate(signals.build_context(
            allocation_band_breached=True, allocation_drift_pp=9,
            rolling_3y_outperformance_rate=0.30, history_months=96))
        self.assertEqual(signal.state, "REBALANCE")

    def test_every_signal_records_its_ruleset_version(self):
        self.assertEqual(self.evaluate(deteriorating()).ruleset_version,
                         self.r.signals.version)


class TestSwitching(unittest.TestCase):
    def setUp(self):
        self.r = fx.registry()
        self.u = fx.universe()
        self.scores = fx.scores()
        engine = TaxEngine(self.r)
        self.tax = engine.estimate_switch(
            lots=[Lot(1800, "150", date(2020, 8, 20))], units_to_sell=1800,
            current_nav=self.u.get("FLX04").nav, tax_class="EQUITY_ORIENTED",
            sale_date=date.today(),
            exit_load_bands=self.u.get("FLX04").exit_load_bands)

    def test_marginal_improvement_is_rejected(self):
        result = switching.analyse_switch(
            current_scheme=self.u.get("FLX01"), current_score=self.scores["FLX01"]["overall"],
            candidate_scheme=self.u.get("FLX07"), candidate_score=self.scores["FLX07"]["overall"],
            holding_value=Decimal("500000"), tax_estimate=self.tax, years_to_goal=15)
        self.assertEqual(result.recommendation, "HOLD")

    def test_regular_to_direct_survives_its_costs(self):
        result = switching.analyse_switch(
            current_scheme=self.u.get("FLX04"), current_score=self.scores["FLX04"]["overall"],
            candidate_scheme=self.u.get("FLX01"), candidate_score=self.scores["FLX01"]["overall"],
            holding_value=Decimal("900000"), tax_estimate=self.tax, years_to_goal=20)
        self.assertEqual(result.recommendation, "SWITCH_REVIEW")
        self.assertGreater(float(result.cost_advantage_annual), 0)

    def test_tax_and_load_are_always_subtracted(self):
        result = switching.analyse_switch(
            current_scheme=self.u.get("FLX04"), current_score=61.0,
            candidate_scheme=self.u.get("FLX01"), candidate_score=79.0,
            holding_value=Decimal("900000"), tax_estimate=self.tax, years_to_goal=20)
        self.assertLess(float(result.net_benefit), float(result.expected_improvement))

    def test_counter_arguments_are_mandatory(self):
        result = switching.analyse_switch(
            current_scheme=self.u.get("FLX04"), current_score=61.0,
            candidate_scheme=self.u.get("FLX01"), candidate_score=79.0,
            holding_value=Decimal("900000"), tax_estimate=self.tax, years_to_goal=20)
        self.assertTrue(result.against)
        self.assertIn("what_could_make_this_wrong", result.as_dict())


class TestExits(unittest.TestCase):
    def test_price_fall_is_not_an_exit_condition(self):
        result = exits.assess({"years_to_goal": 12, "portfolio_drawdown_pct": 40,
                               "nav_down_1y_pct": 35},
                              scheme_code="X", goal_id="G1")
        self.assertFalse(result.triggered)
        self.assertEqual(result.as_dict()["state"], "HOLD")

    def test_all_six_exit_types_are_reachable(self):
        cases = {
            "GOAL_EXIT": {"declared_liquidity_need_within_months": 3},
            "ALLOCATION_EXIT": {"years_to_goal": 2, "min_years_for_asset_class": 7},
            "RISK_EXIT": {"years_to_goal": 12, "risk_capacity_band_dropped": True,
                          "holding_equity_pct": 80},
            "THESIS_EXIT": {"years_to_goal": 12, "scheme_material_change": True},
            "TAX_AWARE_EXIT": {"years_to_goal": 12, "planned_withdrawal_due": True},
        }
        for expected, ctx in cases.items():
            result = exits.assess(ctx, scheme_code="X", goal_id="G1")
            self.assertEqual(result.exit_type, expected, ctx)

    def test_quality_exit_needs_a_multi_trigger_signal(self):
        r = fx.registry()
        signal = signals.evaluate(deteriorating(), r, scheme_code="X", goal_id="G1")
        result = exits.assess({"years_to_goal": 12}, scheme_code="X", goal_id="G1",
                              signal=signal)
        self.assertEqual(result.exit_type, "QUALITY_EXIT")
        self.assertIn("switch review", result.recommended_route)

    def test_triggered_exits_carry_counter_arguments(self):
        result = exits.assess({"declared_liquidity_need_within_months": 3},
                              scheme_code="X", goal_id="G1")
        self.assertTrue(result.counter_arguments)

    def test_sequencing_splits_across_exemption_sized_tranches(self):
        lots = [Lot(1000, "100", date(2019, 1, 1)), Lot(1000, "100", date(2020, 1, 1))]
        plan = exits.sequencing_plan(lots, 125000, 250)
        self.assertGreater(plan["tranches"], 1)
        self.assertIn("caveat", plan)


class TestBehaviour(unittest.TestCase):
    def test_panic_adds_friction_but_never_blocks(self):
        gate = behaviour.gate_action({"portfolio_drawdown_pct": 25,
                                      "intended_action": "REDEEM"})
        self.assertTrue(gate["proceed"])
        self.assertTrue(gate["must_acknowledge"])
        self.assertGreater(gate["cooling_off_hours"], 0)

    def test_reality_check_questions_are_asked(self):
        result = behaviour.panic_protection({"portfolio_drawdown_pct": 25,
                                             "intended_action": "STOP_SIP"})
        self.assertEqual(result.questions, behaviour.REALITY_CHECK)

    def test_fomo_fires_on_a_hot_recent_run(self):
        result = behaviour.fomo_protection({"intended_action": "BUY",
                                            "candidate_trailing_1y_return_pct": 70,
                                            "category_median_1y_return_pct": 25})
        self.assertTrue(result.triggered)
        self.assertIn("1-year return as the headline figure", result.suppress)

    def test_calm_context_produces_no_friction(self):
        gate = behaviour.gate_action({"portfolio_drawdown_pct": 2,
                                      "intended_action": "BUY",
                                      "portfolio_actions_last_12m": 0})
        self.assertEqual(gate["interventions"], [])

    def test_overtrading_fires_on_repeated_changes(self):
        result = behaviour.overtrading_protection({"portfolio_actions_last_12m": 6})
        self.assertTrue(result.triggered)


class TestAlerts(unittest.TestCase):
    def setUp(self):
        self.r = fx.registry()
        self.signal = signals.evaluate(deteriorating(), self.r,
                                       scheme_code="FLX02", goal_id="G1")

    def alert(self, **overrides):
        alert = from_signal(self.signal, investor_id="TEST-1", scheme_name="Some Fund")
        for key, value in overrides.items():
            setattr(alert, key, value)
        return alert

    def test_hold_produces_no_alert(self):
        quiet = signals.evaluate(signals.build_context(history_months=96), self.r,
                                 scheme_code="X", goal_id="G1")
        self.assertIsNone(from_signal(quiet, investor_id="T", scheme_name="X"))

    def test_alert_must_be_complete(self):
        alert = self.alert(if_you_do_nothing="")
        with self.assertRaises(ComplianceBlock):
            validate(alert, self.r)

    def test_complete_alert_passes(self):
        self.assertTrue(validate(self.alert(), self.r))

    def test_duplicates_are_suppressed(self):
        dispatcher = AlertDispatcher(self.r)
        first = dispatcher.dispatch(self.alert())
        second = dispatcher.dispatch(self.alert())
        self.assertNotEqual(first["status"], "SUPPRESSED")
        self.assertEqual(second["status"], "SUPPRESSED")

    def test_budget_defers_non_urgent_alerts(self):
        dispatcher = AlertDispatcher(self.r)
        statuses = []
        for i in range(6):
            alert = self.alert(alert_id=f"A{i}", title=f"t{i}", scheme_code=f"S{i}",
                               severity="YELLOW")
            statuses.append(dispatcher.dispatch(alert)["status"])
        self.assertIn("DEFERRED_TO_DIGEST", statuses)

    def test_red_is_exempt_from_the_budget(self):
        dispatcher = AlertDispatcher(self.r)
        for i in range(6):
            dispatcher.dispatch(self.alert(alert_id=f"A{i}", title=f"t{i}",
                                           scheme_code=f"S{i}", severity="YELLOW"))
        red = dispatcher.dispatch(self.alert(alert_id="RED1", title="urgent",
                                             scheme_code="SX", severity="RED"))
        self.assertEqual(red["status"], "SENT")

    def test_policy_forbids_transactional_urgency(self):
        prohibited = " ".join(self.r.alerts["prohibited_alert_patterns"]).lower()
        for phrase in ("artificial urgency", "fomo", "leaderboard"):
            self.assertIn(phrase, prohibited)

    def test_market_movement_does_not_generate_alerts(self):
        self.assertFalse(self.r.alerts["market_movement"]["notify_on_index_move"])
        self.assertFalse(self.r.alerts["market_movement"]["notify_on_portfolio_drawdown"])


if __name__ == "__main__":
    unittest.main()
