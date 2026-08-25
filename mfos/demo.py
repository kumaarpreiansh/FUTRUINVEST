#!/usr/bin/env python3
"""End-to-end demonstration of the MF Investment Operating System.

Runs on clearly-labelled SYNTHETIC scheme data. No real NAV, AUM, expense
ratio, manager or holding appears anywhere in this run.

It shows, in order:

  1. an investor being profiled, scored for financial health, and risk-assessed
     on three separate dimensions that disagree with each other
  2. goals projected as ranges and stress-tested
  3. a fund universe scored and shortlisted, with schemes being rejected
  4. the recommendation pipeline halting on compliance, because this platform
     is not a registered Investment Adviser
  5. the same pipeline in an explicitly-labelled sandbox, running all sixteen
     stages and stopping at a closed execution gate
  6. the behavioural guardrails, the alert budget, and the compliance register

Run:  python mfos/demo.py
"""

import os
import sys
from datetime import date
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from mfos import DISCLAIMER, alerts, behaviour, exits, scenarios, signals, sip, switching
from mfos.audit import AuditLog
from mfos.compliance import ComplianceEngine
from mfos.goals import Goal, probability_band, required_return
from mfos.health import score_financial_health
from mfos.money import fmt_inr, lakhs
from mfos.pipeline import RecommendationPipeline
from mfos.portfolio import Holding, Portfolio, analyse_overlap, target_allocation
from mfos.profile import InvestorProfile, next_questions
from mfos.risk import assess as assess_risk
from mfos.ruleset import Registry
from mfos.scoring import score_universe
from mfos.synthetic import demo_universe
from mfos.tax import Lot, TaxEngine

HERE = os.path.dirname(os.path.abspath(__file__))


def rule(title):
    print("\n" + "─" * 78)
    print(title)
    print("─" * 78)


def _f(value):
    return "—" if value is None else f"{value:.0f}"


def build_investor():
    """Deliberately conflicted: high appetite, thin buffer, ambitious goal."""
    return InvestorProfile(
        investor_id="INV-DEMO-001",
        age=34,
        monthly_income=Decimal("220000"),
        monthly_expenses=Decimal("105000"),
        current_savings=Decimal("400000"),
        existing_investments=Decimal("1500000"),
        total_loan_emi=Decimal("40000"),
        emergency_fund=Decimal("190000"),
        investment_experience="BEGINNER",
        primary_goals=["RETIREMENT", "CHILD_EDUCATION"],
        primary_horizon_years=20,
        dependants=2,
        income_stability="VARIABLE",
        life_insurance_cover=Decimal("5000000"),
        health_insurance_cover=Decimal("500000"),
        existing_sips=Decimal("63000"),
        marginal_tax_rate_pct=30.0,
        outstanding_loan_amount=Decimal("3800000"),
        risk_tolerance_answers={
            "drop_reaction": "BUY_MORE",
            "acceptable_annual_loss": "40+",
            "experience": "UNDER_2Y",
            "volatility_preference": "VOLATILE_12PCT",
        },
        past_market_behaviour="NEVER_EXPERIENCED",
        kyc_status="NOT_STARTED",
    )


def main():
    registry = Registry(config_root=os.path.join(HERE, "config"))
    universe = demo_universe()
    profile = build_investor()

    print("FUTRUINVEST — Mutual Fund Investment Operating System")
    print(DISCLAIMER)
    print("\nAll scheme data in this run is SYNTHETIC and labelled as such.")

    # ── 1. profile ───────────────────────────────────────────────────────────
    rule("1. INVESTOR PROFILE — progressive, not an interrogation")
    print(f"Tier-1 completeness: {profile.completeness_pct(1)}%   "
          f"Tier-2: {profile.completeness_pct(2)}%   Tier-3: {profile.completeness_pct(3)}%")
    print(f"Monthly surplus: {fmt_inr(profile.monthly_surplus)}  "
          f"Savings rate: {profile.savings_rate_pct:.0f}%  "
          f"Emergency fund: {profile.emergency_fund_months:.1f} months  "
          f"EMI/income: {profile.emi_to_income_pct:.0f}%")
    print("\nNext questions, ranked by how much they change the answer:")
    for q in next_questions(profile, limit=3):
        print(f"  • {q['field']}  —  {q['why_asking']}")

    # ── 2. goals ─────────────────────────────────────────────────────────────
    retirement = Goal("G1", "Retirement at 55", "RETIREMENT", Decimal("30000000"), 21,
                      current_corpus=Decimal("1500000"), monthly_contribution=Decimal("45000"),
                      annual_step_up_pct=10, priority="HIGH")
    education = Goal("G2", "Child's degree", "CHILD_EDUCATION", Decimal("4500000"), 12,
                     current_corpus=Decimal("300000"), monthly_contribution=Decimal("18000"),
                     annual_step_up_pct=10, priority="HIGH")

    # ── 3. risk ──────────────────────────────────────────────────────────────
    rule("2. RISK — three questions, not one label")
    inflation = float(registry.allocation["capital_market_assumptions"]["inflation"]["base"])
    req = required_return(retirement, inflation)
    risk = assess_risk(profile, registry,
                       required_return_pct=(req * 100) if req else None,
                       horizon_years=retirement.years_to_goal)
    d = risk.as_dict()
    print(f"  Capacity   : {d['capacity']['band']:12} ({d['capacity']['score']})")
    print(f"  Tolerance  : {d['tolerance']['band']:12} ({d['tolerance']['score']})")
    print(f"  Requirement: {d['requirement']['band']:12} "
          f"(needs {d['requirement'].get('required_return_pct')}% a year)")
    print(f"\n  Binding band: {risk.binding_band} — {risk.binding_reason}")
    print(f"  Equity ceiling: {risk.max_equity_pct}%")
    for conflict in risk.conflicts:
        print(f"\n  [{conflict['severity']}] {conflict['type']}")
        print(f"    {conflict['explanation']}")
        print(f"    → {conflict['resolution']}")

    # ── 4. allocation and projections ────────────────────────────────────────
    rule("3. GOALS — ranges, never a single number")
    target = target_allocation(registry, retirement, risk)
    print(f"  Policy {target['policy_id']}  equity {target['equity_pct']}% "
          f"(ceiling {target['equity_ceiling_pct']}%)")
    if target["cap_note"]:
        print(f"  {target['cap_note']}")
    print(f"  Target: {target['weights']}")

    projections = scenarios.project_all_scenarios(retirement, target["weights"], registry)
    print(f"\n  {retirement.name} — target {lakhs(retirement.target_amount_today)} today, "
          f"{lakhs(projections['base'].inflation_adjusted_target)} at maturity after inflation")
    for name, proj in projections.items():
        p = proj.as_dict()
        print(f"    {name:12} return {p['expected_return_pct']:5}%  "
              f"median {lakhs(proj.corpus_p50):>12}  probability {p['probability_pct']:5}%")
    print(f"    Verdict: {probability_band(projections['base'].probability)}")
    print(f"    Required return {projections['base'].as_dict()['required_return_pct']}% vs "
          f"{projections['base'].expected_return_pct:.1f}% expected — the gap is the problem, "
          "and no fund choice closes it.")

    rule("4. STRESS TESTS — reported as probability, not as rupees lost")
    for stress in scenarios.run_all_stresses(retirement, target["weights"], registry):
        s = stress.as_dict()
        print(f"  {s['label']:32} {s['baseline_probability_pct']:5}% → "
              f"{s['stressed_probability_pct']:5}%  ({s['delta_pp']:+.1f}pp)")
        print(f"      {s['note']}")

    rule("5. SIP — the only lever the investor actually controls")
    for row in sip.step_up_comparison(retirement, target["weights"], registry):
        print(f"  Step-up {row['step_up_pct']:2}%/yr → probability {row['probability_pct']:5}% "
              f"({row['probability_gain_pp']:+.1f}pp), final-year SIP "
              f"{fmt_inr(row['final_year_monthly'])}")
    need = sip.required_sip_for_target_probability(retirement, target["weights"], registry, 0.75)
    print(f"\n  To reach 75%: {need.get('required_monthly', need.get('message'))}")

    # ── 5. funds ─────────────────────────────────────────────────────────────
    rule("6. FUND SCORES — within SEBI category, never across")
    scores = score_universe(universe, registry)
    ranked = sorted((s for s in scores.values() if s.get("overall") is not None),
                    key=lambda s: -s["overall"])
    print(f"  {'code':6} {'score':>6}  {'band':14} {'cons':>5} {'risk':>5} {'cost':>5} "
          f"{'qual':>5} {'trk':>5}  scheme")
    for s in ranked[:8]:
        p = s["pillars"]
        print(f"  {s['scheme_code']:6} {s['overall']:6.1f}  {s['band']:14} "
              f"{_f(p['consistency']):>5} {_f(p['risk']):>5} {_f(p['cost']):>5} "
              f"{_f(p['quality']):>5} {_f(p['tracking']):>5}  {s['scheme_name']}")
    print(f"\n  FLX01 scores {scores['FLX01']['overall']} and FLX04 {scores['FLX04']['overall']} — "
          "identical portfolios, Direct versus Regular. The gap is cost, not skill.")

    # ── 6. existing portfolio ────────────────────────────────────────────────
    rule("7. OVERLAP — the defect most Indian portfolios actually have")
    portfolio = Portfolio("INV-DEMO-001", [
        Holding("FLX01", Decimal("3000"), [Lot(3000, "160", date(2021, 4, 12))], goal_id="G1"),
        Holding("FLX02", Decimal("2400"), [Lot(2400, "140", date(2022, 6, 1))], goal_id="G1"),
        Holding("FLX04", Decimal("1800"), [Lot(1800, "150", date(2020, 8, 20))], goal_id="G1"),
        Holding("LRG01", Decimal("2000"), [Lot(2000, "120", date(2023, 1, 10))], goal_id="G1"),
    ])
    overlap = analyse_overlap(portfolio, universe, registry)
    print(f"  {overlap['headline']}")
    print(f"  Portfolio value: {lakhs(portfolio.value(universe))}")
    for pair in overlap["pairs"][:3]:
        print(f"    {pair['a']} / {pair['b']}: {pair['overlap_pct']}% "
              f"({'same' if pair['same_category'] else 'different'} category)")
    for finding in overlap["findings"][:3]:
        print(f"    [{finding['verdict']}] {finding['why']}")

    # ── 7. signals ───────────────────────────────────────────────────────────
    rule("8. SIGNALS — one trigger is never enough")
    weak = signals.build_context(rolling_3y_outperformance_rate=0.33, history_months=96)
    print(f"  One weak trigger              → "
          f"{signals.evaluate(weak, registry, scheme_code='FLX02', goal_id='G1').state}")
    crash = signals.build_context(history_months=96, market_drawdown_pct=32, nav_down_6m_pct=27)
    print(f"  Market down 32%               → "
          f"{signals.evaluate(crash, registry, scheme_code='FLX02', goal_id='G1').state}"
          "   (by design: a falling market is not a trigger)")

    deteriorating = signals.build_context(
        rolling_3y_outperformance_rate=0.28, category_quartile_3y=4, category_quartile_5y=4,
        sharpe_percentile=18, sharpe_percentile_3y_ago=62,
        expense_ratio_increase_bps=22, expense_ratio_pct=1.35, category_median_expense=0.95,
        top10_weight_pct=64, category_median_top10=45, history_months=96,
    )
    signal = signals.evaluate(deteriorating, registry, scheme_code="FLX02", goal_id="G1")
    print(f"  Five independent triggers     → {signal.state} (score {signal.score:.0f})")
    print("\n" + "\n".join("  " + line for line in signals.explain(signal).splitlines()))

    dampened = dict(deteriorating, exit_load_applicable=True,
                    estimated_tax_pct_of_value=4.2, months_since_last_action_on_holding=7)
    print(f"\n  Same triggers, but acting is expensive and recent → "
          f"{signals.evaluate(dampened, registry, scheme_code='FLX02', goal_id='G1').state}")

    # ── 8. switching ─────────────────────────────────────────────────────────
    rule("9. SWITCH INTELLIGENCE — priced net of tax, load and friction")
    tax_engine = TaxEngine(registry)
    current, candidate = universe.get("FLX04"), universe.get("FLX01")
    lots = [Lot(1800, "150", date(2020, 8, 20))]
    estimate = tax_engine.estimate_switch(
        lots=lots, units_to_sell=1800, current_nav=current.nav,
        tax_class="EQUITY_ORIENTED", sale_date=date.today(),
        exit_load_bands=current.exit_load_bands,
        investor_marginal_rate_pct=profile.marginal_tax_rate_pct,
    )
    analysis = switching.analyse_switch(
        current_scheme=current, current_score=scores["FLX04"]["overall"],
        candidate_scheme=candidate, candidate_score=scores["FLX01"]["overall"],
        holding_value=current.nav * 1800, tax_estimate=estimate, years_to_goal=21,
    )
    a = analysis.as_dict()
    print(f"  {current.scheme_name}  →  {candidate.scheme_name}")
    print(f"    Expected improvement {fmt_inr(a['expected_improvement_over_horizon'])} "
          f"(cost advantage {fmt_inr(a['of_which_cost_advantage_per_year'])}/yr)")
    print(f"    Tax {fmt_inr(a['tax_cost'])}  exit load {fmt_inr(a['exit_load'])}  "
          f"friction {fmt_inr(a['friction_cost'])}")
    print(f"    Net {fmt_inr(a['net_benefit'])}, breakeven {a['breakeven_years']} years "
          f"→ {a['recommendation']} at {a['confidence_pct']}% confidence")
    for reason in a["what_could_make_this_wrong"][:2]:
        print(f"    ✗ {reason}")

    marginal = switching.analyse_switch(
        current_scheme=universe.get("FLX01"), current_score=scores["FLX01"]["overall"],
        candidate_scheme=universe.get("FLX07"), candidate_score=scores["FLX07"]["overall"],
        holding_value=Decimal("480000"), tax_estimate=estimate, years_to_goal=21,
    )
    print(f"\n  A merely-better fund (gap "
          f"{scores['FLX07']['overall'] - scores['FLX01']['overall']:.0f} points) → "
          f"{marginal.recommendation}: {marginal.reasons[-1]}")

    # ── 9. exits ─────────────────────────────────────────────────────────────
    rule("10. EXIT INTELLIGENCE — six reasons, none of them a price fall")
    for label, ctx in (
        ("Goal is funded and near", {"years_to_goal": 2, "projected_corpus": 4.4e6,
                                     "inflation_adjusted_target": 4.1e6}),
        ("Money needed in 4 months", {"declared_liquidity_need_within_months": 4}),
        ("Scheme merged", {"years_to_goal": 12, "scheme_material_change": True,
                           "scheme_material_change_detail": "merged into another scheme"}),
        ("Market fell 30%", {"years_to_goal": 12, "portfolio_drawdown_pct": 30}),
    ):
        result = exits.assess(ctx, scheme_code="FLX01", goal_id="G1",
                              tax_estimate=estimate).as_dict()
        print(f"  {label:26} → {result['exit_type'] or 'no exit condition':18} "
              f"({result['state']})")

    # ── 10. behaviour ────────────────────────────────────────────────────────
    rule("11. BEHAVIOURAL GUARDRAILS — friction, not refusal")
    gate = behaviour.gate_action({
        "portfolio_drawdown_pct": 24, "intended_action": "REDEEM",
        "portfolio_actions_last_12m": 5, "months_since_last_action_on_holding": 6,
    })
    print(f"  Proceed: {gate['proceed']}   severity {gate['highest_severity']}   "
          f"cooling-off {gate['cooling_off_hours']}h")
    for intervention in gate["interventions"]:
        print(f"\n  [{intervention['guardrail']}] {intervention['message']}")
        for question in intervention["ask_before_proceeding"][:3]:
            print(f"      ? {question}")
    print(f"\n  {gate['principle']}")

    # ── 11. alerts ───────────────────────────────────────────────────────────
    rule("12. ALERTS — a budget, not a firehose")
    dispatcher = alerts.AlertDispatcher(registry)
    alert = alerts.from_signal(signal, investor_id=profile.investor_id,
                               scheme_name=universe.get("FLX02").scheme_name)
    outcome = dispatcher.dispatch(alert)
    print(f"  {alert.severity} — {alert.title}")
    print(f"    what happened    : {alert.what_happened[:96]}…")
    print(f"    if you do nothing: {alert.if_you_do_nothing}")
    print(f"    → {outcome['status']} ({outcome['why']})")
    repeat = dispatcher.dispatch(alert)
    print(f"    same alert again → {repeat['status']} ({repeat['why']})")

    # ── 12. financial health ─────────────────────────────────────────────────
    rule("13. FINANCIAL HEALTH — capped when the foundations are missing")
    health = score_financial_health(
        profile, goal_projections=[projections["base"]],
        portfolio_stats={"max_pairwise_overlap_pct": overlap["max_pairwise_overlap_pct"],
                         "max_amc_pct": overlap["max_amc_pct"],
                         "fund_count": overlap["fund_count"], "regular_plan_pct": 22},
        risk_assessment={"conflict": risk.conflict, "conflict_summary": risk.conflict_summary},
    )
    print(f"  Score {health['score']}/100 ({health['band']}), "
          f"uncapped {health['uncapped_score']}, coverage {health['coverage_pct']}%")
    for cap in health["caps_applied"]:
        print(f"    capped at {cap['cap']}: {cap['reason']}")
    for dim in health["breakdown"]:
        if dim["score"] is not None:
            print(f"    {dim['label']:24} {dim['score']:3}")
    print(f"\n  Do this next — {health['next_action']['dimension']}: "
          f"{health['next_action']['do']}")

    # ── 13. pipeline, as shipped ─────────────────────────────────────────────
    rule("14. PIPELINE AS SHIPPED — halts at compliance, on purpose")
    audit_log = AuditLog(os.path.join(HERE, "data", "audit.jsonl"))
    result = RecommendationPipeline(registry, universe, audit_log).run(
        profile=profile, goal=education, portfolio=portfolio)
    for stage in result["stages"]:
        print(f"    {stage['stage']:2} {stage['name']:24} {stage['status']}")
    print(f"\n  HALTED: {result['halted_at']['message']}")
    print(f"  {result['halted_at']['output'].get('note', '')}")

    # ── 14. pipeline, sandbox ────────────────────────────────────────────────
    rule("15. PIPELINE IN SANDBOX — all sixteen stages, execution still closed")
    sandbox = Registry(config_root=os.path.join(HERE, "config"))
    sandbox.enable_sandbox_advisory("demonstration only — no real registration exists")
    full = RecommendationPipeline(sandbox, universe, audit_log).run(
        profile=profile, goal=education, portfolio=portfolio,
        signal_context=dict(deteriorating, scheme_code="FLX02"))
    for stage in full["stages"]:
        print(f"    {stage['stage']:2} {stage['name']:24} {stage['status']}  {stage['message']}")
    print()
    print("\n".join("  " + line for line in full["explanation"].splitlines()))
    print(f"\n  Confidence {full['confidence']['confidence_pct']}% "
          f"({full['confidence']['band']}); human review required: "
          f"{full['human_review']['required']}")
    for reason in full["human_review"]["reasons"]:
        print(f"    - {reason}")
    execute_stage = next(s for s in full["stages"] if s["name"] == "execute")
    print(f"\n  Execution stage: {execute_stage['output']['status']} — "
          f"{execute_stage['output']['why']}")

    # ── 15. audit ────────────────────────────────────────────────────────────
    rule("16. AUDIT TRAIL — replayable, append-only")
    chain = audit_log.verify_chain()
    print(f"  Rows: {chain['rows']}   chain intact: {chain['intact']}")
    row = full["audit"]
    print(f"  Recommendation {row['recommendation_id']}")
    print(f"    profile v{row['profile_version']} fingerprint {row['profile_fingerprint']}")
    print(f"    rule-set versions: tax {row['ruleset_versions']['tax']['version']}, "
          f"compliance {row['ruleset_versions']['compliance']['version']}, "
          f"signals {row['ruleset_versions']['signals']['version']}")
    print(f"    regulatory rules used: {', '.join(row['regulatory_rules_used'])}")
    print(f"    triggers fired: {row['triggers_fired']}")
    print(f"    content hash: {row['content_hash'][:24]}…")

    # ── 16. compliance register ──────────────────────────────────────────────
    rule("17. COMPLIANCE REGISTER — what still blocks a real launch")
    engine = ComplianceEngine(registry)
    readiness = engine.readiness()
    print(f"  {readiness['verdict']}")
    print(f"  Blocking: {', '.join(readiness['blocking_rules'])}")
    print(f"  Unverified: {', '.join(readiness['unverified_rules'])}")
    print(f"  Open legal-review items: {readiness['open_legal_items']}")
    print("\n  Rules not yet met:")
    for entry in engine.register():
        if entry["compliance_status"] in ("NOT_MET", "IN_PROGRESS"):
            print(f"    [{entry['compliance_status']:11}] {entry['rule_id']:26} "
                  f"{entry['title'][:58]}")

    print("\n" + "=" * 78)
    print("Nothing above placed an order. Nothing above is investment advice.")
    print("=" * 78)


if __name__ == "__main__":
    main()
