"""The recommendation pipeline — sixteen stages, every one able to stop the run.

Stages, in order, and each one's right to halt:

   1 Understand investor          incomplete profile -> ask, don't assume
   2 Understand goal              no goal -> nothing to be suitable for
   3 Determine risk               three dimensions, conflicts named
   4 Required allocation          capped by the binding risk band
   5 Analyse current portfolio    what is actually held, looked through
   6 Identify gaps                where the portfolio differs from the plan
   7 Analyse eligible funds       quality scores within SEBI peer groups
   8 Apply suitability rules      SUITABILITY OVERRIDES PERFORMANCE
   9 Apply compliance rules       COMPLIANCE OVERRIDES AI
  10 Apply tax considerations     no figure without a verified rule
  11 Calculate expected outcomes  ranges and probabilities, never a number
  12 Generate recommendation      states, not orders
  13 Explain recommendation       grounded, cited, with counter-arguments
  14 Obtain consent               specific, per transaction
  15 Execute                      disabled here, and gated three ways
  16 Monitor                      schedule the next review

Nothing in this file places an order. Stage 15 exists to be blocked, and its
being blocked is asserted by a test.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from . import audit as audit_mod
from . import behaviour, confidence as conf_mod, portfolio as pf, scenarios, selection, signals
from .compliance import ComplianceEngine
from .errors import (ComplianceBlock, ConsentRequired, DataQualityFailure,
                     InsufficientData, MFOSError, RuleNotVerified, SuitabilityBlock)
from .goals import probability_band, required_return
from .money import lakhs, money
from .profile import TIER_1, next_questions
from .risk import assess as assess_risk
from .scoring import score_universe
from .tax import TaxEngine


@dataclass
class StageResult:
    stage: int
    name: str
    status: str                  # OK | HALT | SKIPPED | BLOCKED
    output: dict = field(default_factory=dict)
    message: str = ""

    def as_dict(self):
        return {"stage": self.stage, "name": self.name, "status": self.status,
                "message": self.message, "output": self.output}


class Halt(Exception):
    def __init__(self, stage_result):
        super().__init__(stage_result.message)
        self.stage_result = stage_result


class RecommendationPipeline:
    def __init__(self, registry, universe, audit_log=None):
        self.registry = registry
        self.universe = universe
        self.compliance = ComplianceEngine(registry)
        self.tax = TaxEngine(registry)
        self.audit_log = audit_log
        self.stages = []

    # ── helpers ──────────────────────────────────────────────────────────────
    def _ok(self, stage, name, output=None, message=""):
        result = StageResult(stage, name, "OK", output or {}, message)
        self.stages.append(result)
        return result

    def _halt(self, stage, name, message, output=None, status="HALT"):
        result = StageResult(stage, name, status, output or {}, message)
        self.stages.append(result)
        raise Halt(result)

    # ── the run ──────────────────────────────────────────────────────────────
    def run(self, *, profile, goal, portfolio=None, current_holding=None,
            months_since_last_rebalance=999, signal_context=None):
        """Produce a recommendation for one goal. Returns a record and an audit row."""
        self.stages = []
        record = audit_mod.new_record(profile.investor_id, self.registry, profile)
        try:
            return self._run(record, profile, goal, portfolio, current_holding,
                             months_since_last_rebalance, signal_context)
        except Halt as halt:
            record.stage_results = {s.name: s.as_dict() for s in self.stages}
            record.recommendation = {
                "action": "NO_RECOMMENDATION",
                "halted_at": halt.stage_result.name,
                "reason": halt.stage_result.message,
                "next_step": halt.stage_result.output.get("next_step"),
            }
            if self.audit_log:
                self.audit_log.write(record)
            return {
                "status": "HALTED",
                "halted_at": halt.stage_result.as_dict(),
                "stages": [s.as_dict() for s in self.stages],
                "audit": record.as_dict(),
            }

    def _run(self, record, profile, goal, portfolio, current_holding,
             months_since_last_rebalance, signal_context):

        # ── 1. Understand investor ───────────────────────────────────────────
        missing = profile.missing(tier=1)
        if missing:
            self._halt(1, "understand_investor",
                       "I don't know enough about you to make a recommendation yet.",
                       {"missing": missing, "next_step": next_questions(profile, limit=3)})
        self._ok(1, "understand_investor", {
            "profile_version": profile.version,
            "completeness_tier1_pct": profile.completeness_pct(1),
            "completeness_tier2_pct": profile.completeness_pct(2),
            "monthly_surplus": str(profile.monthly_surplus),
            "next_questions": next_questions(profile, limit=2),
        })

        # ── 2. Understand goal ───────────────────────────────────────────────
        if goal is None:
            self._halt(2, "understand_goal",
                       "There is no goal to be suitable for. Everything else follows from one.")
        self._ok(2, "understand_goal", {
            "goal_id": goal.goal_id, "type": goal.goal_type,
            "years": goal.years_to_goal, "non_negotiable": goal.is_non_negotiable,
        })

        # ── 3. Determine risk ────────────────────────────────────────────────
        inflation = float(self.registry.allocation["capital_market_assumptions"]["inflation"]["base"])
        req_return = required_return(goal, inflation)
        risk = assess_risk(profile, self.registry,
                           required_return_pct=(req_return * 100) if req_return is not None else None,
                           horizon_years=goal.years_to_goal)
        self._ok(3, "determine_risk", risk.as_dict())
        critical = [c for c in risk.conflicts if c["severity"] == "CRITICAL"]
        if critical:
            self._halt(3, "determine_risk",
                       critical[0]["explanation"],
                       {"conflicts": risk.conflicts,
                        "next_step": critical[0]["resolution"]})

        # ── 4. Required allocation ───────────────────────────────────────────
        target = pf.target_allocation(self.registry, goal, risk)
        target["weights"] = scenarios.glide_path_allocation(
            target["weights"], self.registry, goal.years_to_goal)
        self._ok(4, "required_allocation", target)

        # ── 5. Analyse current portfolio ─────────────────────────────────────
        portfolio = portfolio or pf.Portfolio(profile.investor_id, [])
        overlap = pf.analyse_overlap(portfolio, self.universe, self.registry) \
            if portfolio.holdings else {"findings": [], "fund_count": 0, "headline": "No holdings yet."}
        current_weights = portfolio.asset_class_weights(self.universe)
        self._ok(5, "analyse_portfolio", {
            "value": str(portfolio.value(self.universe)),
            "asset_class_weights": {k: round(v, 2) for k, v in current_weights.items()},
            "overlap": overlap,
        })

        # ── 6. Identify gaps ─────────────────────────────────────────────────
        rebalance = pf.rebalance_plan(
            portfolio, self.universe, target, self.registry,
            monthly_inflow=goal.monthly_contribution,
            months_since_last_rebalance=months_since_last_rebalance,
            years_to_goal=goal.years_to_goal,
        ) if portfolio.holdings else {"action": "BUILD", "signal": "HOLD",
                                      "why": "Nothing held yet for this goal."}
        gaps = [asset for asset, weight in target["weights"].items()
                if current_weights.get(asset, 0.0) < weight - 5]
        self._ok(6, "identify_gaps", {"under_allocated": gaps, "rebalance": rebalance})

        # ── 7. Analyse eligible funds ────────────────────────────────────────
        try:
            scores = score_universe(self.universe, self.registry)
        except DataQualityFailure as exc:
            self._halt(7, "analyse_funds", exc.message, exc.detail, status="BLOCKED")
        scored = [s for s in scores.values() if s.get("overall") is not None]
        if not scored:
            self._halt(7, "analyse_funds",
                       "No scheme in the universe has data good enough to score. "
                       "I will not recommend something I cannot verify.",
                       status="BLOCKED")
        self._ok(7, "analyse_funds", {
            "scored": len(scored), "not_scored": len(scores) - len(scored),
        })

        # ── 8. Suitability ───────────────────────────────────────────────────
        shortlists = {}
        for asset_class in gaps or list(target["weights"]):
            shortlists[asset_class] = selection.shortlist(
                self.universe, self.registry, asset_class=asset_class,
                risk_assessment=risk, years_to_goal=goal.years_to_goal,
                portfolio=portfolio, scores=scores, target_allocation=target["weights"],
            )
        candidates = [c for sl in shortlists.values() for c in sl["candidates"]]
        if not candidates:
            self._halt(8, "apply_suitability",
                       "Nothing in the available universe is both suitable for you and of "
                       "sufficient quality for this goal.",
                       {"shortlists": shortlists}, status="BLOCKED")
        self._ok(8, "apply_suitability", {
            "shortlists": shortlists,
            "principle": "Schemes failing a suitability gate were removed, not down-ranked.",
        })

        # ── 9. Compliance ────────────────────────────────────────────────────
        checks = {
            "advice": self.compliance.check_advice_permitted().as_dict(),
            "suitability": self.compliance.check_suitability(
                profile=profile, risk_assessment=risk,
                target_allocation=target["weights"]).as_dict(),
            "conflicts": self.compliance.check_conflicts(
                [self.universe.get(c["scheme_code"]) for c in candidates]).as_dict(),
        }
        record.regulatory_rules_used = sorted({
            r for c in checks.values() for r in c["rules_evaluated"]
        })
        blocked = [name for name, c in checks.items() if not c["allowed"]]
        if blocked:
            first = next(c for c in checks.values() if not c["allowed"])
            self._halt(9, "apply_compliance", first["blocks"][0]["message"],
                       {"checks": checks,
                        "next_step": first["blocks"][0].get("remedy"),
                        "note": ("The analysis above is complete and available as information. "
                                 "What is blocked is presenting it as personalised advice.")},
                       status="BLOCKED")
        self._ok(9, "apply_compliance", {"checks": checks})

        # ── 10. Tax ──────────────────────────────────────────────────────────
        tax_result = {"applicable": False,
                      "note": "Nothing is being sold, so no tax arises from this recommendation."}
        if current_holding is not None:
            try:
                estimate = self.tax.estimate_switch(
                    lots=current_holding["lots"],
                    units_to_sell=current_holding["units"],
                    current_nav=current_holding["nav"],
                    tax_class=current_holding["tax_class"],
                    exit_load_bands=current_holding.get("exit_load_bands"),
                    investor_marginal_rate_pct=profile.marginal_tax_rate_pct,
                    ltcg_exemption_already_used=current_holding.get("ltcg_used", Decimal("0")),
                )
                tax_result = {"applicable": True, **estimate.as_dict()}
                record.tax_rules_used = estimate.rule_ids
            except (RuleNotVerified, InsufficientData) as exc:
                self._halt(10, "apply_tax", exc.message,
                           {"detail": exc.detail,
                            "next_step": ("I will not show you a recommendation whose tax "
                                          "consequence I cannot compute.")},
                           status="BLOCKED")
        self._ok(10, "apply_tax", tax_result)

        # ── 11. Expected outcomes ────────────────────────────────────────────
        projections = scenarios.project_all_scenarios(goal, target["weights"], self.registry)
        stresses = [s.as_dict() for s in scenarios.run_all_stresses(
            goal, target["weights"], self.registry)]
        base = projections["base"]
        self._ok(11, "expected_outcomes", {
            "scenarios": {k: v.as_dict() for k, v in projections.items()},
            "stress_tests": stresses,
            "probability_band": probability_band(base.probability),
        })

        # ── 12. Generate recommendation ──────────────────────────────────────
        signal = None
        if signal_context:
            signal = signals.evaluate(signal_context, self.registry,
                                      scheme_code=signal_context.get("scheme_code", "PORTFOLIO"),
                                      goal_id=goal.goal_id)
        action = self._decide(rebalance, signal, gaps, base)

        supporting = len(gaps) + (len(signal.fired) if signal else 0)
        conflicting = len(signal.dampened) if signal else 0
        dq_scores = [c["data_quality"] for c in candidates]
        confidence = conf_mod.assess(
            self.registry,
            data_quality=sum(dq_scores) / len(dq_scores) if dq_scores else None,
            freshness_hours=6,
            independent_sources=2,
            supporting=supporting,
            conflicting=conflicting,
            history_months=min((self.universe.get(c["scheme_code"]).history_months
                                for c in candidates), default=None),
            method_limitations=(conf_mod.METHOD_LIMITATIONS["goal_probability"]
                                + conf_mod.METHOD_LIMITATIONS["fund_score"]),
        )
        if not confidence.sufficient:
            self._halt(12, "generate_recommendation",
                       "I don't have a strong enough basis to recommend anything here.",
                       {"confidence": confidence.as_dict(),
                        "next_step": "Improve the inputs rather than lowering the bar."},
                       status="BLOCKED")

        review = conf_mod.needs_human_review(
            self.registry, confidence,
            amount=portfolio.value(self.universe), goal_type=goal.goal_type, action=action["state"],
        )
        self._ok(12, "generate_recommendation", {**action, "confidence": confidence.as_dict(),
                                                 "human_review": review})

        # ── 13. Explain ──────────────────────────────────────────────────────
        explanation = self._explain(action, target, base, candidates, tax_result, signal, risk)
        language = self.compliance.check_language(
            explanation, contains_return_figure=True, contains_projection=True, mentions_scheme=True)
        if not language.allowed:
            self._halt(13, "explain", "Generated explanation failed the language check.",
                       {"blocks": language.blocks}, status="BLOCKED")
        self._ok(13, "explain", {"explanation": explanation,
                                 "disclosures": language.required_disclosures})

        # ── 14. Consent ──────────────────────────────────────────────────────
        self._ok(14, "obtain_consent", {
            "consent_required": action["state"] not in ("HOLD", "WATCH"),
            "consent_is": ("specific to one transaction, captured after the tax estimate and "
                           "conflict disclosures are shown, and expiring"),
            "status": "NOT_REQUESTED",
            "why": "Nothing is presented for consent until a person has reviewed it."
                   if review["required"] else "Awaiting the investor's decision.",
        })

        # ── 15. Execute ──────────────────────────────────────────────────────
        self._ok(15, "execute", {
            "status": "DISABLED",
            "why": ("Execution is switched off on this build, automated execution is "
                    "permanently prohibited, and no consent exists. Three independent gates, "
                    "all closed."),
        }, message="No order was placed. None can be.")

        # ── 16. Monitor ──────────────────────────────────────────────────────
        self._ok(16, "monitor", {
            "next_review": "Monthly signal evaluation; quarterly portfolio review; "
                           "annual profile refresh.",
            "what_would_change_this": [
                "Your goal, income, dependants or liabilities changing",
                "A material change to any scheme held",
                "Allocation drifting outside its band",
                "The goal entering its glide path",
            ],
        })

        # ── assemble ─────────────────────────────────────────────────────────
        record.stage_results = {s.name: s.as_dict() for s in self.stages}
        record.portfolio_snapshot = {
            "value": str(portfolio.value(self.universe)),
            "holdings": [{"scheme_code": h.scheme_code, "units": str(h.units)}
                         for h in portfolio.holdings],
        }
        record.fund_data_snapshot = audit_mod.snapshot_schemes(
            [self.universe.get(c["scheme_code"]) for c in candidates])
        record.models_used = [
            {"model_id": "goal_probability", "version": "1.0.0"},
            {"model_id": "profile_gap_detector", "version": "0.9.0"},
        ]
        record.triggers_fired = [t["id"] for t in (signal.fired if signal else [])]
        record.signals = [signal.as_dict()] if signal else []
        record.recommendation = action
        record.confidence = confidence.as_dict()
        record.explanation = explanation
        record.disclosures_shown = language.required_disclosures
        record.conflicts_disclosed = [c["conflict_disclosure"] for c in candidates
                                      if c["conflict_disclosure"]["has_commercial_relationship"]]
        record.human_review = review
        record.investor_response = "PRESENTED"
        if self.audit_log:
            self.audit_log.write(record)

        return {
            "status": "COMPLETE",
            "recommendation": action,
            "confidence": confidence.as_dict(),
            "explanation": explanation,
            "human_review": review,
            "target_allocation": target,
            "shortlists": shortlists,
            "projections": {k: v.as_dict() for k, v in projections.items()},
            "stress_tests": stresses,
            "tax": tax_result,
            "stages": [s.as_dict() for s in self.stages],
            "audit": record.as_dict(),
        }

    # ── decision ─────────────────────────────────────────────────────────────
    def _decide(self, rebalance, signal, gaps, projection):
        """Turn the stage outputs into a single state. Deliberately conservative."""
        if signal is not None and signal.state in ("EXIT_REVIEW", "REDUCE", "SWITCH_REVIEW"):
            return {"state": signal.state,
                    "summary": f"{signal.state.replace('_', ' ').title()} on this holding.",
                    "signal": signal.as_dict()}
        if rebalance.get("signal") == "REBALANCE":
            return {"state": "REBALANCE", "summary": rebalance["why"], "plan": rebalance}
        if rebalance.get("action") == "BUILD" or gaps:
            return {"state": "ADD",
                    "summary": ("Build the missing parts of the target allocation with new "
                                "contributions."),
                    "under_allocated": gaps}
        if signal is not None and signal.state in ("REVIEW", "WATCH"):
            return {"state": signal.state, "summary": "Worth watching; not worth acting on.",
                    "signal": signal.as_dict()}
        return {"state": "HOLD",
                "summary": "Nothing material has changed. The plan is working.",
                "goal_probability_pct": round(projection.probability * 100, 1)}

    # ── explanation ──────────────────────────────────────────────────────────
    def _explain(self, action, target, projection, candidates, tax_result, signal, risk):
        lines = [f"Recommendation: {action['state'].replace('_', ' ').title()}",
                 "", action["summary"], ""]

        lines.append("Why:")
        if signal and signal.fired:
            for trigger in signal.fired[:5]:
                lines.append(f"  - {trigger['description']}")
        elif action["state"] == "ADD":
            for asset in action.get("under_allocated", []):
                lines.append(f"  - {asset} is below its target weight of "
                             f"{target['weights'].get(asset, 0):.0f}% for this goal.")
        else:
            lines.append("  - The evidence does not support making a change.")

        lines += ["", "Your situation:",
                  f"  - Binding risk band: {risk.binding_band} ({risk.binding_reason})",
                  f"  - Goal probability on base assumptions: "
                  f"{projection.probability * 100:.0f}% ({probability_band(projection.probability)})",
                  f"  - Projected corpus range: {lakhs(projection.corpus_p10)} to "
                  f"{lakhs(projection.corpus_p90)} (10th to 90th percentile)"]
        for conflict in risk.conflicts[:2]:
            lines.append(f"  - {conflict['explanation']}")

        if candidates:
            lines += ["", "Candidates considered:"]
            for c in candidates[:3]:
                lines.append(
                    f"  - {c['scheme_name']} ({c['plan']}): quality {c['fund_score']}, "
                    f"fit {c['suitability_score']}, expense {c['expense_ratio_pct']}%"
                )
                for note in c["suitability_notes"][:1]:
                    lines.append(f"      note: {note}")

        lines += ["", "What could make this wrong:",
                  "  - Assumptions are long-run averages; no decade has matched them exactly.",
                  "  - Fund quality measured on past data may not persist.",
                  "  - A change in your income, dependants or goals changes the answer."]

        lines += ["", "Tax and cost:"]
        if tax_result.get("applicable"):
            lines.append(f"  - Estimated tax {tax_result['estimated_tax']} "
                         f"(lower bound: {tax_result['estimated_tax_is_lower_bound']}), "
                         f"exit load {tax_result['exit_load']}")
            for caveat in tax_result.get("caveats", [])[:2]:
                lines.append(f"  - {caveat}")
        else:
            lines.append("  - " + tax_result["note"])

        lines += ["", "Projections are estimates based on stated assumptions, not promises. "
                      "Past performance does not indicate future results."]
        return "\n".join(lines)
