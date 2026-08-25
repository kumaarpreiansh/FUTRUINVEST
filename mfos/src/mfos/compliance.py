"""Compliance Engine.

COMPLIANCE OVERRIDES AI. That sentence is only meaningful if it is a code path,
so this module sits between every recommendation and the investor, and between
every transaction and the execution boundary. It can only ever say no.

It reads the versioned regulatory rule repository. It contains no regulatory
knowledge of its own: if a rule is not in the repository, the behaviour it
governs is blocked, not permitted. Fail-closed is the only safe default when
the cost of being wrong is a regulatory breach.
"""

import re
from dataclasses import dataclass, field
from datetime import date

from .errors import ComplianceBlock, ConsentRequired


@dataclass
class ComplianceResult:
    allowed: bool
    blocks: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    required_disclosures: list = field(default_factory=list)
    rules_evaluated: list = field(default_factory=list)
    ruleset_version: str = ""

    def raise_if_blocked(self):
        if not self.allowed:
            raise ComplianceBlock(
                self.blocks[0]["message"] if self.blocks else "Blocked by compliance.",
                detail={"blocks": self.blocks},
                rule_ids=[b["rule_id"] for b in self.blocks],
            )
        return self

    def as_dict(self):
        return {
            "allowed": self.allowed,
            "blocks": self.blocks,
            "warnings": self.warnings,
            "required_disclosures": self.required_disclosures,
            "rules_evaluated": self.rules_evaluated,
            "ruleset_version": self.ruleset_version,
        }


class ComplianceEngine:
    def __init__(self, registry):
        self.registry = registry
        self.rules = registry.compliance

    # ── rule access ──────────────────────────────────────────────────────────
    def rule(self, rule_id):
        return self.rules.rule(rule_id)

    def disclosure(self, disclosure_id):
        d = self.rules["disclaimers"].get(disclosure_id)
        if not d:
            raise ComplianceBlock(f"Unknown disclosure {disclosure_id!r}")
        return d

    # ── the advisory gate ────────────────────────────────────────────────────
    def check_advice_permitted(self):
        """May this platform give personalised advice at all, right now?

        This is the first gate in the pipeline, and while registration is not
        in place it is the one that stops everything downstream.
        """
        result = ComplianceResult(allowed=True, ruleset_version=self.rules.version)
        rule = self.rule("SEBI-IA-REG-001")
        result.rules_evaluated.append(rule["rule_id"])

        if not self.registry.is_registered_adviser:
            result.allowed = False
            result.blocks.append({
                "rule_id": rule["rule_id"],
                "message": (
                    "This platform is not registered as an Investment Adviser, so it cannot "
                    "give personalised investment advice. Analysis, education and planning "
                    "tools remain available; a recommendation to buy, sell or switch a "
                    "specific scheme does not."
                ),
                "remedy": "Complete SEBI IA registration, then record the number in platform.yaml.",
            })
            result.required_disclosures.append("DISC-NOT-REGISTERED-001")
        if not self.registry.gate("personalised_advice_enabled"):
            result.allowed = False
            result.blocks.append({
                "rule_id": rule["rule_id"],
                "message": "The personalised advice gate is switched off in platform.yaml.",
                "remedy": "Enable it only once the operating model is settled and registered.",
            })
        return result

    # ── suitability ──────────────────────────────────────────────────────────
    def check_suitability(self, *, profile, risk_assessment, scheme=None, asset_class=None,
                          target_allocation=None):
        """SUITABILITY OVERRIDES PERFORMANCE, enforced before a name is shown."""
        result = ComplianceResult(allowed=True, ruleset_version=self.rules.version)
        rule = self.rule("SEBI-IA-SUITABILITY-001")
        result.rules_evaluated.append(rule["rule_id"])

        if not profile.is_minimally_complete:
            result.allowed = False
            result.blocks.append({
                "rule_id": rule["rule_id"],
                "message": "The investor profile is incomplete; suitability cannot be established.",
                "missing": profile.missing(tier=1),
            })
        if profile.is_stale(high_impact=True):
            result.warnings.append({
                "rule_id": rule["rule_id"],
                "message": (f"Profile last updated {profile.updated_on}. Re-confirm before acting "
                            "on a material recommendation."),
            })
        if risk_assessment is None:
            result.allowed = False
            result.blocks.append({
                "rule_id": rule["rule_id"],
                "message": "No risk assessment on file.",
            })
        elif scheme is not None:
            from .risk import riskometer_permitted
            in_plan = target_allocation is not None and scheme.asset_class in target_allocation
            if not in_plan and scheme.riskometer and not riskometer_permitted(
                    risk_assessment.binding_band, scheme.riskometer):
                result.allowed = False
                result.blocks.append({
                    "rule_id": rule["rule_id"],
                    "message": (f"{scheme.scheme_name} carries a {scheme.riskometer} risk-o-meter, "
                                f"above the {risk_assessment.binding_band} band, and is not part "
                                "of this investor's plan."),
                })
        return result

    # ── conflicts ────────────────────────────────────────────────────────────
    def check_conflicts(self, schemes):
        """Disclosure is mandatory; influence is prohibited."""
        result = ComplianceResult(allowed=True, ruleset_version=self.rules.version)
        rule = self.rule("SEBI-IA-CONFLICT-001")
        result.rules_evaluated.append(rule["rule_id"])

        if self.registry.gate("commission_influences_ranking"):
            result.allowed = False
            result.blocks.append({
                "rule_id": rule["rule_id"],
                "message": "Commercial terms are configured to influence ranking. That is not "
                           "permitted and the recommendation is blocked.",
            })
        for scheme in schemes or []:
            if scheme.amc_relationship or scheme.distributor_commission_pct:
                result.required_disclosures.append("CONFLICT-" + scheme.scheme_code)
                result.warnings.append({
                    "rule_id": rule["rule_id"],
                    "message": (f"Commercial relationship with {scheme.amc} must be disclosed "
                                f"alongside {scheme.scheme_name}."),
                })
        return result

    # ── communication ────────────────────────────────────────────────────────
    def check_language(self, text, *, contains_return_figure=False,
                       contains_projection=False, mentions_scheme=False):
        """Every outbound string passes through here — LLM output included.

        A violation blocks delivery. It is never quietly rewritten: a silent
        rewrite hides the fact that a model produced prohibited language, and
        that fact is exactly what the AI governance layer needs to see.
        """
        result = ComplianceResult(allowed=True, ruleset_version=self.rules.version)
        rule = self.rule("COMM-NO-GUARANTEE-001")
        result.rules_evaluated.append(rule["rule_id"])

        lowered = (text or "").lower()
        for phrase in self.registry.platform["prohibited_language"]:
            if re.search(r"\b" + re.escape(phrase) + r"\b", lowered):
                result.allowed = False
                result.blocks.append({
                    "rule_id": rule["rule_id"],
                    "message": f"Prohibited phrase in outbound content: {phrase!r}.",
                    "phrase": phrase,
                })

        if contains_return_figure:
            result.required_disclosures.append("DISC-PAST-PERF-001")
            result.rules_evaluated.append("COMM-PAST-PERF-001")
        if contains_projection:
            result.required_disclosures.append("DISC-PROJECTION-001")
        if mentions_scheme:
            result.required_disclosures.append("DISC-MF-RISK-001")
        if not self.registry.is_registered_adviser and mentions_scheme:
            result.required_disclosures.append("DISC-NOT-REGISTERED-001")
        return result

    # ── execution boundary ───────────────────────────────────────────────────
    def check_transaction(self, *, profile, transaction, consent=None):
        """Nothing crosses into execution without every one of these.

        The consent check implements the rule that an adviser cannot transact
        on a client's behalf without specific consent for each transaction —
        so consent is per-transaction, bound to this exact scheme, amount and
        direction, and it expires.
        """
        result = ComplianceResult(allowed=True, ruleset_version=self.rules.version)

        kyc_rule = self.rule("KYC-KRA-001")
        result.rules_evaluated.append(kyc_rule["rule_id"])
        if profile.kyc_status != "VALIDATED":
            result.allowed = False
            result.blocks.append({
                "rule_id": kyc_rule["rule_id"],
                "message": f"KYC status is {profile.kyc_status}. No transaction can be placed.",
            })

        aml_rule = self.rule("AML-PMLA-001")
        result.rules_evaluated.append(aml_rule["rule_id"])
        if aml_rule.get("compliance_status") != "MET_BY_DESIGN":
            result.allowed = False
            result.blocks.append({
                "rule_id": aml_rule["rule_id"],
                "message": ("AML/CFT controls are not yet implemented "
                            f"(status: {aml_rule.get('compliance_status')}). "
                            "Fail-closed: no transaction is permitted."),
            })

        consent_rule = self.rule("SEBI-IA-CONSENT-001")
        result.rules_evaluated.append(consent_rule["rule_id"])
        try:
            verify_consent(consent, transaction)
        except ConsentRequired as exc:
            result.allowed = False
            result.blocks.append({
                "rule_id": consent_rule["rule_id"],
                "message": exc.message,
            })

        if self.registry.gate("auto_execution_enabled"):
            result.allowed = False
            result.blocks.append({
                "rule_id": consent_rule["rule_id"],
                "message": "Automated execution is enabled. That is prohibited; blocked.",
            })
        if not self.registry.gate("execution_enabled"):
            result.allowed = False
            result.blocks.append({
                "rule_id": "PLATFORM-GATE",
                "message": ("Execution is disabled on this build. Recommendations are produced; "
                            "nothing is ever placed."),
            })
        return result

    # ── regulatory change management surface ─────────────────────────────────
    def register(self, on=None):
        """The compliance register: every rule, its status, and what it gates."""
        on = on or date.today()
        live = {r["rule_id"] for r in self.rules.effective_rules(on)}
        rows = []
        for rule in self.rules.rules():
            rows.append({
                "rule_id": rule["rule_id"],
                "title": rule.get("title"),
                "domain": rule.get("domain"),
                "source": rule.get("source"),
                "source_url": rule.get("source_url"),
                "effective_from": str(rule.get("effective_from")),
                "effective_to": str(rule.get("effective_to")),
                "in_force": rule["rule_id"] in live,
                "enforcement": rule.get("enforcement"),
                "control_id": rule.get("control_id"),
                "verification_status": rule.get("verification_status"),
                "compliance_status": rule.get("compliance_status"),
                "legal_review_required": bool(rule.get("legal_review_required")),
                "system_impact": rule.get("system_impact"),
                "watch": rule.get("watch"),
            })
        return sorted(rows, key=lambda r: (r["compliance_status"] != "NOT_MET", r["rule_id"]))

    def readiness(self):
        """Can this build be launched? A short, blunt answer."""
        rows = self.register()
        blocking = [r for r in rows
                    if r["enforcement"] == "BLOCK" and r["compliance_status"] not in
                    ("MET", "MET_BY_DESIGN")]
        unverified = [r for r in rows if r["verification_status"] == "UNVERIFIED"]
        return {
            "launch_ready": not blocking,
            "blocking_rules": [r["rule_id"] for r in blocking],
            "unverified_rules": [r["rule_id"] for r in unverified],
            "open_legal_items": len(self.registry.open_legal_items()),
            "verdict": (
                "Not launchable as an advisory service. "
                f"{len(blocking)} blocking rule(s) unmet, {len(unverified)} rule(s) unverified. "
                "The engines run; the regulated behaviours are gated off."
                if blocking else "All blocking rules met."
            ),
        }


# ── consent ──────────────────────────────────────────────────────────────────

@dataclass
class Consent:
    """A specific, per-transaction consent. Not a blanket authorisation."""

    investor_id: str
    scheme_code: str
    direction: str                # BUY | REDEEM | SWITCH
    amount: str
    granted_at: object
    expires_at: object
    method: str                   # IN_APP_2FA | ESIGN | RECORDED_CALL
    recommendation_id: str = None
    disclosures_shown: list = field(default_factory=list)
    tax_estimate_shown: bool = False
    conflicts_shown: bool = False


def verify_consent(consent, transaction, now=None):
    """Consent must match this exact transaction, and still be alive."""
    from datetime import datetime
    now = now or datetime.utcnow()

    if consent is None:
        raise ConsentRequired(
            "No consent recorded for this transaction. An adviser cannot transact on your "
            "behalf without your specific consent for this exact transaction.",
            rule_ids=["SEBI-IA-CONSENT-001"],
        )
    for field_name in ("scheme_code", "direction", "amount"):
        if str(getattr(consent, field_name)) != str(transaction.get(field_name)):
            raise ConsentRequired(
                f"Consent does not match this transaction ({field_name} differs). "
                "Consent is specific to one transaction and cannot be reused.",
                detail={"field": field_name,
                        "consented": str(getattr(consent, field_name)),
                        "requested": str(transaction.get(field_name))},
                rule_ids=["SEBI-IA-CONSENT-001"],
            )
    if consent.expires_at and now > consent.expires_at:
        raise ConsentRequired(
            "This consent has expired. Please review the transaction again.",
            rule_ids=["SEBI-IA-CONSENT-001"],
        )
    if not consent.tax_estimate_shown:
        raise ConsentRequired(
            "Consent was captured without showing the tax impact. Not valid.",
            rule_ids=["SEBI-IA-CONSENT-001"],
        )
    if not consent.conflicts_shown:
        raise ConsentRequired(
            "Consent was captured without showing conflict disclosures. Not valid.",
            rule_ids=["SEBI-IA-CONFLICT-001"],
        )
    return True
