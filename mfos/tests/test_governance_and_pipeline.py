import os
import tempfile
import unittest
from datetime import date, datetime, timedelta
from decimal import Decimal

import conftest_path as fx
from mfos import confidence as conf
from mfos import copilot
from mfos.audit import AuditLog, new_record, snapshot_schemes
from mfos.compliance import ComplianceEngine, Consent, verify_consent
from mfos.errors import ComplianceBlock, ConsentRequired, InsufficientData
from mfos.pipeline import RecommendationPipeline
from mfos.portfolio import Holding, Portfolio


class TestCompliance(unittest.TestCase):
    def setUp(self):
        self.r = fx.registry()
        self.engine = ComplianceEngine(self.r)

    def test_advice_is_blocked_without_registration(self):
        result = self.engine.check_advice_permitted()
        self.assertFalse(result.allowed)
        self.assertIn("DISC-NOT-REGISTERED-001", result.required_disclosures)

    def test_prohibited_language_is_blocked(self):
        for phrase in ("guaranteed returns", "risk-free", "assured returns"):
            result = self.engine.check_language(f"This scheme offers {phrase} of 15%.")
            self.assertFalse(result.allowed, phrase)

    def test_clean_language_passes_with_disclosures(self):
        result = self.engine.check_language(
            "Projections are estimates based on published assumptions.",
            contains_projection=True, mentions_scheme=True)
        self.assertTrue(result.allowed)
        self.assertIn("DISC-PROJECTION-001", result.required_disclosures)
        self.assertIn("DISC-MF-RISK-001", result.required_disclosures)

    def test_return_figures_require_the_past_performance_disclosure(self):
        result = self.engine.check_language("It returned 12% last year.",
                                            contains_return_figure=True)
        self.assertIn("DISC-PAST-PERF-001", result.required_disclosures)

    def test_incomplete_profile_fails_suitability(self):
        from mfos.profile import InvestorProfile
        result = self.engine.check_suitability(
            profile=InvestorProfile("X"), risk_assessment=None)
        self.assertFalse(result.allowed)

    def test_transaction_requires_kyc_consent_and_open_gates(self):
        profile = fx.sample_profile(kyc_status="NOT_STARTED")
        result = self.engine.check_transaction(
            profile=profile,
            transaction={"scheme_code": "FLX01", "direction": "BUY", "amount": "50000"},
            consent=None)
        self.assertFalse(result.allowed)
        blocked_rules = {b["rule_id"] for b in result.blocks}
        self.assertIn("KYC-KRA-001", blocked_rules)
        self.assertIn("SEBI-IA-CONSENT-001", blocked_rules)
        self.assertIn("PLATFORM-GATE", blocked_rules)

    def test_readiness_reports_blocking_rules(self):
        readiness = self.engine.readiness()
        self.assertFalse(readiness["launch_ready"])
        self.assertIn("SEBI-IA-REG-001", readiness["blocking_rules"])

    def test_register_lists_every_rule_with_status(self):
        register = self.engine.register()
        self.assertEqual(len(register), len(self.r.compliance.rules()))
        for row in register:
            self.assertIn("compliance_status", row)
            self.assertIn("source", row)


class TestConsent(unittest.TestCase):
    def valid_consent(self, **overrides):
        defaults = dict(
            investor_id="TEST-1", scheme_code="FLX01", direction="BUY", amount="50000",
            granted_at=datetime.utcnow(),
            expires_at=datetime.utcnow() + timedelta(minutes=15),
            method="IN_APP_2FA", tax_estimate_shown=True, conflicts_shown=True)
        defaults.update(overrides)
        return Consent(**defaults)

    def transaction(self, **overrides):
        txn = {"scheme_code": "FLX01", "direction": "BUY", "amount": "50000"}
        txn.update(overrides)
        return txn

    def test_valid_consent_passes(self):
        self.assertTrue(verify_consent(self.valid_consent(), self.transaction()))

    def test_missing_consent_is_refused(self):
        with self.assertRaises(ConsentRequired):
            verify_consent(None, self.transaction())

    def test_consent_cannot_be_reused_for_another_scheme(self):
        with self.assertRaises(ConsentRequired):
            verify_consent(self.valid_consent(), self.transaction(scheme_code="FLX02"))

    def test_consent_cannot_be_reused_for_another_amount(self):
        with self.assertRaises(ConsentRequired):
            verify_consent(self.valid_consent(), self.transaction(amount="500000"))

    def test_expired_consent_is_refused(self):
        expired = self.valid_consent(expires_at=datetime.utcnow() - timedelta(minutes=1))
        with self.assertRaises(ConsentRequired):
            verify_consent(expired, self.transaction())

    def test_consent_without_tax_disclosure_is_invalid(self):
        with self.assertRaises(ConsentRequired):
            verify_consent(self.valid_consent(tax_estimate_shown=False), self.transaction())

    def test_consent_without_conflict_disclosure_is_invalid(self):
        with self.assertRaises(ConsentRequired):
            verify_consent(self.valid_consent(conflicts_shown=False), self.transaction())


class TestConfidence(unittest.TestCase):
    def setUp(self):
        self.r = fx.registry()

    def test_confidence_never_reaches_certainty(self):
        best = conf.assess(self.r, data_quality=100, freshness_hours=1,
                           independent_sources=4, supporting=10, conflicting=0,
                           history_months=240)
        self.assertLess(best.score, 100)

    def test_conflicting_evidence_lowers_confidence(self):
        clean = conf.assess(self.r, data_quality=90, supporting=5, conflicting=0)
        messy = conf.assess(self.r, data_quality=90, supporting=5, conflicting=4)
        self.assertGreater(clean.score, messy.score)

    def test_poor_inputs_make_it_insufficient(self):
        weak = conf.assess(self.r, data_quality=50, freshness_hours=900,
                           independent_sources=0, supporting=1, conflicting=5,
                           history_months=10)
        self.assertFalse(weak.sufficient)
        self.assertEqual(weak.band, "INSUFFICIENT")

    def test_large_amounts_route_to_a_human(self):
        good = conf.assess(self.r, data_quality=95, supporting=6, conflicting=0)
        review = conf.needs_human_review(self.r, good, amount=5_000_000,
                                         goal_type="RETIREMENT", action="EXIT_REVIEW")
        self.assertTrue(review["required"])

    def test_method_limitations_are_published(self):
        for method in ("goal_probability", "fund_score", "switch_analysis", "tax_estimate"):
            self.assertTrue(conf.METHOD_LIMITATIONS[method])


class TestCopilotGrounding(unittest.TestCase):
    def setUp(self):
        self.r = fx.registry()
        self.engine = ComplianceEngine(self.r)
        self.facts = (copilot.FactPack()
                      .add("portfolio_return_period_pct", -12.4, "Your holdings")
                      .add("benchmark_return_period_pct", -11.8, "Blended benchmark")
                      .add("period_label", "the last six months", "Platform"))

    def test_answer_uses_the_mandated_sections(self):
        answer = copilot.answer("WHY_PORTFOLIO_DOWN", self.facts,
                                compliance_engine=self.engine)
        for section in ("answer", "why", "risks", "alternatives", "tax_and_cost",
                        "recommendation", "sources"):
            self.assertIn(section, answer.as_dict())

    def test_unknown_intent_is_refused(self):
        with self.assertRaises(InsufficientData):
            copilot.answer("PREDICT_NEXT_YEAR", self.facts)

    def test_missing_facts_refuse_rather_than_estimate(self):
        with self.assertRaises(InsufficientData):
            copilot.answer("SHOULD_I_INCREASE_SIP", copilot.FactPack())

    def test_grounded_numbers_pass(self):
        self.assertEqual(
            copilot.verify_numbers("It fell 12.4% against 11.8%.", self.facts), [])

    def test_invented_numbers_are_caught(self):
        self.assertIn("18.7",
                      copilot.verify_numbers("It will recover 18.7% next year.", self.facts))

    def test_llm_output_with_invented_numbers_is_rejected(self):
        answer = copilot.answer("WHY_PORTFOLIO_DOWN", self.facts)
        explainer = copilot.GroundedExplainer(
            self.r, client=lambda prompt: "You will make 22.5% next year.")
        result = explainer.explain(answer, self.facts, self.engine)
        self.assertEqual(result["generated_by"], "deterministic_fallback")
        self.assertIn("22.5", result["checks"]["ungrounded_numbers"])

    def test_llm_output_with_prohibited_language_is_rejected(self):
        answer = copilot.answer("WHY_PORTFOLIO_DOWN", self.facts)
        explainer = copilot.GroundedExplainer(
            self.r, client=lambda prompt: "This scheme has guaranteed returns.")
        result = explainer.explain(answer, self.facts, self.engine)
        self.assertEqual(result["generated_by"], "deterministic_fallback")
        self.assertTrue(result["checks"]["language_blocks"])

    def test_model_outage_falls_back_silently(self):
        def broken(prompt):
            raise RuntimeError("upstream down")
        answer = copilot.answer("WHY_PORTFOLIO_DOWN", self.facts)
        result = copilot.GroundedExplainer(self.r, client=broken).explain(answer, self.facts)
        self.assertEqual(result["generated_by"], "deterministic_fallback")
        self.assertTrue(result["text"])

    def test_no_model_configured_still_answers(self):
        answer = copilot.answer("WHY_PORTFOLIO_DOWN", self.facts)
        result = copilot.GroundedExplainer(self.r).explain(answer, self.facts)
        self.assertEqual(result["generated_by"], "deterministic")

    def test_unregistered_model_cannot_be_used(self):
        with self.assertRaises(InsufficientData):
            copilot.GroundedExplainer(self.r, model_id="not_in_registry")

    def test_unvalidated_model_is_blocked(self):
        with self.assertRaises(InsufficientData):
            copilot.GroundedExplainer(self.r, model_id="anomaly_detector")


class TestAudit(unittest.TestCase):
    def setUp(self):
        self.r = fx.registry()
        self.dir = tempfile.mkdtemp()
        self.log = AuditLog(os.path.join(self.dir, "audit.jsonl"))

    def test_record_pins_versions_and_profile(self):
        profile = fx.sample_profile()
        record = new_record("TEST-1", self.r, profile)
        self.assertEqual(record.profile_version, profile.version)
        self.assertEqual(record.profile_fingerprint, profile.fingerprint())
        self.assertEqual(record.ruleset_versions["tax"]["version"], self.r.tax.version)

    def test_response_supersedes_rather_than_edits(self):
        record = new_record("TEST-1", self.r, fx.sample_profile())
        self.log.write(record)
        self.log.record_response(record, "ACCEPTED", {"placed": False})
        rows = self.log.read_all()
        self.assertEqual(len(rows), 2)
        self.assertIsNone(rows[0]["investor_response"])
        self.assertEqual(rows[1]["investor_response"], "ACCEPTED")
        self.assertTrue(self.log.verify_chain()["intact"])

    def test_profile_edit_changes_the_fingerprint(self):
        profile = fx.sample_profile()
        before = profile.fingerprint()
        profile.update(dependants=4)
        self.assertNotEqual(before, profile.fingerprint())
        self.assertEqual(profile.version, 2)

    def test_snapshot_captures_provenance(self):
        snapshot = snapshot_schemes([fx.universe().get("FLX01")])
        entry = snapshot["FLX01"]
        self.assertIn("provenance", entry)
        self.assertIn("nav", entry["provenance"])
        self.assertEqual(entry["provenance"]["nav"]["source"], "AMFI")


class TestPipeline(unittest.TestCase):
    def setUp(self):
        self.u = fx.universe()
        self.profile = fx.sample_profile()
        self.goal = fx.sample_goal(years_to_goal=15,
                                   target_amount_today=Decimal("8000000"),
                                   monthly_contribution=Decimal("30000"))

    def run_pipeline(self, sandbox=False, **kwargs):
        registry = fx.registry(sandbox=sandbox)
        return RecommendationPipeline(registry, self.u).run(
            profile=self.profile, goal=self.goal, **kwargs)

    def test_shipped_build_halts_at_compliance(self):
        result = self.run_pipeline()
        self.assertEqual(result["status"], "HALTED")
        self.assertEqual(result["halted_at"]["name"], "apply_compliance")

    def test_incomplete_profile_halts_at_stage_one_with_questions(self):
        from mfos.profile import InvestorProfile
        registry = fx.registry(sandbox=True)
        result = RecommendationPipeline(registry, self.u).run(
            profile=InvestorProfile("EMPTY"), goal=self.goal)
        self.assertEqual(result["status"], "HALTED")
        self.assertEqual(result["halted_at"]["stage"], 1)
        self.assertTrue(result["halted_at"]["output"]["next_step"])

    def test_sandbox_run_completes_all_sixteen_stages(self):
        result = self.run_pipeline(sandbox=True)
        self.assertEqual(result["status"], "COMPLETE")
        self.assertEqual(len(result["stages"]), 16)
        self.assertTrue(all(s["status"] == "OK" for s in result["stages"]))

    def test_execution_stage_is_always_disabled(self):
        result = self.run_pipeline(sandbox=True)
        execute = next(s for s in result["stages"] if s["name"] == "execute")
        self.assertEqual(execute["output"]["status"], "DISABLED")

    def test_recommendation_is_a_state_not_an_order(self):
        result = self.run_pipeline(sandbox=True)
        self.assertIn(result["recommendation"]["state"],
                      ("HOLD", "WATCH", "REVIEW", "ADD", "REBALANCE",
                       "SWITCH_REVIEW", "REDUCE", "EXIT_REVIEW"))

    def test_explanation_passes_the_language_scan(self):
        result = self.run_pipeline(sandbox=True)
        engine = ComplianceEngine(fx.registry())
        self.assertTrue(engine.check_language(result["explanation"]).allowed)

    def test_explanation_includes_counter_arguments(self):
        result = self.run_pipeline(sandbox=True)
        self.assertIn("What could make this wrong", result["explanation"])

    def test_audit_row_is_complete(self):
        result = self.run_pipeline(sandbox=True)
        row = result["audit"]
        for field in ("recommendation_id", "profile_fingerprint", "ruleset_versions",
                      "regulatory_rules_used", "confidence", "explanation",
                      "content_hash", "fund_data_snapshot", "models_used"):
            self.assertTrue(row.get(field) or row.get(field) == 0, field)

    def test_material_scheme_change_reaches_switch_review(self):
        """A merger means you no longer own what you bought — change the fund.

        It does not mean leave the asset class, so the ceiling here is
        SWITCH_REVIEW rather than EXIT_REVIEW.
        """
        result = self.run_pipeline(sandbox=True, signal_context={
            "scheme_code": "FLX02", "history_months": 96,
            "scheme_material_change": True,
            "scheme_material_change_detail": "fundamental attribute change",
            "rolling_3y_outperformance_rate": 0.25,
            "category_quartile_3y": 4, "category_quartile_5y": 4,
        })
        self.assertEqual(result["recommendation"]["state"], "SWITCH_REVIEW")

    def test_declared_liquidity_need_reaches_exit_review(self):
        result = self.run_pipeline(sandbox=True, signal_context={
            "scheme_code": "FLX02", "history_months": 96,
            "declared_liquidity_need_within_months": 3,
            "years_to_goal": 15, "holding_equity_pct": 80,
            "glide_start_years": 5, "glide_target_equity_pct": 10,
        })
        self.assertEqual(result["recommendation"]["state"], "EXIT_REVIEW")

    def test_projections_are_always_ranges(self):
        result = self.run_pipeline(sandbox=True)
        for scenario in ("conservative", "base", "optimistic"):
            self.assertIn(scenario, result["projections"])
            self.assertIn("range_p10_p90", result["projections"][scenario])

    def test_halted_run_still_writes_an_audit_row(self):
        directory = tempfile.mkdtemp()
        log = AuditLog(os.path.join(directory, "audit.jsonl"))
        RecommendationPipeline(fx.registry(), self.u, log).run(
            profile=self.profile, goal=self.goal)
        rows = log.read_all()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["recommendation"]["action"], "NO_RECOMMENDATION")


class TestCrossCuttingSafety(unittest.TestCase):
    """Invariants that must hold no matter which engine changes."""

    def setUp(self):
        self.r = fx.registry()

    def test_no_module_hardcodes_a_tax_rate(self):
        import glob
        source_dir = os.path.join(fx.ROOT, "src", "mfos")
        offenders = []
        for path in glob.glob(os.path.join(source_dir, "*.py")):
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            for literal in ("12.5", "1.25 lakh", "125000"):
                if literal in text and "tax" in os.path.basename(path):
                    offenders.append((os.path.basename(path), literal))
        self.assertEqual(offenders, [], "tax rates must live in the rule-set, not in code")

    def test_disclaimers_exist_for_every_mandatory_id(self):
        for disclosure_id in self.r.platform["mandatory_disclaimer_ids"]:
            self.assertIn(disclosure_id, self.r.compliance["disclaimers"])

    def test_prohibited_language_list_is_not_empty(self):
        self.assertGreater(len(self.r.platform["prohibited_language"]), 5)

    def test_synthetic_data_is_labelled(self):
        import mfos.synthetic as synthetic
        self.assertIn("SYNTHETIC", synthetic.__doc__)
        self.assertIn("None of this is real", synthetic.__doc__)


if __name__ == "__main__":
    unittest.main()
