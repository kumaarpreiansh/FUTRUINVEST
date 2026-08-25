import unittest

import conftest_path as fx
from mfos.errors import ConfigError, RuleNotVerified


class TestRegistry(unittest.TestCase):
    def setUp(self):
        self.r = fx.registry()

    def test_all_rulesets_load_with_versions(self):
        for rs in (self.r.tax, self.r.compliance, self.r.scoring,
                   self.r.allocation, self.r.signals, self.r.alerts, self.r.ai_models):
            self.assertTrue(rs.version, f"{rs.id} has no version")
            self.assertIn("version", rs.stamp)

    def test_scoring_weights_sum_to_100(self):
        self.assertEqual(sum(p["weight"] for p in self.r.scoring["pillars"].values()), 100)

    def test_allocation_policies_sum_to_100(self):
        for policy in self.r.allocation["policies"]:
            total = sum(sum(sleeve.values()) for sleeve in policy["sleeves"].values())
            if total:
                self.assertAlmostEqual(total, 100, msg=policy["id"])

    def test_locked_safety_gates_are_off(self):
        for gate in ("auto_execution_enabled", "withdrawals_enabled",
                     "commission_influences_ranking", "allow_llm_generated_numbers"):
            self.assertFalse(self.r.gate(gate), f"{gate} must be false")

    def test_platform_does_not_claim_registration(self):
        self.assertFalse(self.r.is_registered_adviser)

    def test_unverified_rule_cannot_be_used(self):
        with self.assertRaises(RuleNotVerified):
            self.r.tax.require_verified("TAX-HYBRID-CLASSIFY-001")

    def test_verified_rule_can_be_used(self):
        rule = self.r.tax.require_verified("TAX-EQ-LTCG-001")
        self.assertEqual(rule["rate_pct"], 12.5)

    def test_every_regulatory_rule_has_provenance(self):
        for rule in self.r.compliance.rules():
            for field in ("rule_id", "title", "source", "verification_status", "domain"):
                self.assertIn(field, rule, f"{rule.get('rule_id')} missing {field}")

    def test_every_tax_rule_declares_verification(self):
        for rule in self.r.tax.rules():
            self.assertIn(rule.get("verification_status"),
                          ("VERIFIED_PRIMARY", "VERIFIED_SECONDARY", "UNVERIFIED"),
                          rule.get("rule_id"))

    def test_open_legal_items_are_surfaced_not_hidden(self):
        items = self.r.open_legal_items()
        self.assertTrue(items, "legal review queue should not be silently empty")
        self.assertTrue(all("rule_id" in i for i in items))

    def test_sandbox_refused_on_production_build(self):
        r = fx.registry()
        r.platform["platform"]["build_stage"] = "production"
        with self.assertRaises(ConfigError):
            r.enable_sandbox_advisory("nope")

    def test_sandbox_does_not_open_execution_gates(self):
        r = fx.registry(sandbox=True)
        self.assertTrue(r.is_registered_adviser)      # in memory only
        self.assertFalse(r.gate("execution_enabled"))
        self.assertFalse(r.gate("auto_execution_enabled"))
        self.assertFalse(r.gate("withdrawals_enabled"))

    def test_signal_triggers_all_have_implementations(self):
        from mfos.signals import DAMPENERS, TRIGGERS
        for spec in self.r.signals["triggers"]:
            self.assertIn(spec["id"], TRIGGERS, f"no predicate for {spec['id']}")
        for spec in self.r.signals["dampeners"]:
            self.assertIn(spec["id"], DAMPENERS, f"no predicate for {spec['id']}")

    def test_every_ai_model_declares_prohibited_use(self):
        for model in self.r.ai_models["models"]:
            if model.get("status") == "ACTIVE" and model["kind"] == "LLM":
                self.assertTrue(model.get("prohibited_use"))

    def test_llm_may_not_produce_numbers(self):
        self.assertFalse(self.r.ai_models["global_constraints"]["llm_may_produce_numbers"])


if __name__ == "__main__":
    unittest.main()
