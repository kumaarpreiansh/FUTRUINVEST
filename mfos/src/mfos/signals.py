"""Investment Signal Engine.

Produces one of: HOLD, WATCH, REVIEW, REBALANCE, SWITCH_REVIEW, REDUCE,
EXIT_REVIEW — for a specific holding, for a specific investor, for a specific
goal. Never for "the market".

Two structural commitments, both enforced here rather than left to judgement:

  * No single trigger produces an action signal. Reaching SWITCH_REVIEW or
    EXIT_REVIEW requires three independent triggers, or one event the
    rule-set marks material. This is what stops one bad quarter from becoming
    a switch recommendation.
  * Dampeners subtract. Exit load, tax cost, a recent action on the same
    holding, thin history, and a barely-better alternative all pull the score
    down. An action has to be worth its own cost before it is proposed.

Trigger predicates live in code and are reviewed; their thresholds live in
config/signals/signal_rules.v1.yaml and are versioned. Neither can be changed
without the other being visible in a diff.
"""

from dataclasses import dataclass, field

from .errors import ConfigError

# ── trigger predicates ───────────────────────────────────────────────────────
# Each returns (fired: bool, evidence: dict|None). `ctx` is the evaluation
# context assembled by build_context(). A predicate that lacks its input
# returns False — an unknown is never treated as a trigger.

def _num(ctx, key):
    value = ctx.get(key)
    return value if isinstance(value, (int, float)) else None


def _t_rolling_under(ctx, p):
    rate = _num(ctx, "rolling_3y_outperformance_rate")
    if rate is None:
        return False, None
    return rate < p["max_win_rate"], {"rolling_3y_win_rate": round(rate, 3),
                                      "threshold": p["max_win_rate"]}


def _t_category_quartile(ctx, p):
    q3, q5 = ctx.get("category_quartile_3y"), ctx.get("category_quartile_5y")
    if q3 is None or q5 is None:
        return False, None
    fired = q3 == p["worst_quartile"] and q5 == p["worst_quartile"]
    return fired, {"quartile_3y": q3, "quartile_5y": q5}


def _t_riskadj(ctx, p):
    now, before = _num(ctx, "sharpe_percentile"), _num(ctx, "sharpe_percentile_3y_ago")
    if now is None or before is None:
        return False, None
    fired = now < p["now_below_percentile"] and before >= p["was_at_least_percentile"]
    return fired, {"sharpe_percentile_now": round(now, 1),
                   "sharpe_percentile_then": round(before, 1)}


def _t_downside(ctx, p):
    own, median = _num(ctx, "downside_capture"), _num(ctx, "category_median_downside_capture")
    if own is None or median is None or median == 0:
        return False, None
    fired = abs(own) > abs(median) * p["multiple_of_category_median"]
    return fired, {"downside_capture": round(own, 1), "category_median": round(median, 1)}


def _t_manager_change(ctx, p):
    days = _num(ctx, "manager_changed_within_days")
    if days is None:
        return False, None
    return days <= p["within_days"], {"days_since_change": days}


def _t_mandate_drift(ctx, p):
    drift = _num(ctx, "mandate_drift_score")
    if drift is None:
        return False, None
    return drift > p["max_drift_score"], {"mandate_drift_score": round(drift, 3)}


def _t_scheme_change(ctx, p):
    return bool(ctx.get("scheme_material_change")), {
        "change": ctx.get("scheme_material_change_detail")}


def _t_expense(ctx, p):
    bps = _num(ctx, "expense_ratio_increase_bps")
    own, median = _num(ctx, "expense_ratio_pct"), _num(ctx, "category_median_expense")
    if bps is None or own is None or median is None:
        return False, None
    fired = bps >= p["min_increase_bps"] and own > median
    return fired, {"increase_bps": bps, "expense_ratio": own, "category_median": median}


def _t_concentration(ctx, p):
    own, median = _num(ctx, "top10_weight_pct"), _num(ctx, "category_median_top10")
    if own is None or median is None:
        return False, None
    return own > median + p["points_above_category_median"], {
        "top10": round(own, 1), "category_median_top10": round(median, 1)}


def _t_aum(ctx, p):
    drop, capacity = _num(ctx, "aum_drop_6m_pct"), _num(ctx, "aum_capacity_ratio")
    fired = ((drop is not None and drop > p["max_drop_6m_pct"])
             or (capacity is not None and capacity > p["max_capacity_ratio"]))
    return fired, {"aum_drop_6m_pct": drop, "aum_capacity_ratio": capacity}


def _t_liquidity(ctx, p):
    illiquid = _num(ctx, "illiquid_weight_pct")
    limit = _num(ctx, "category_illiquid_limit_pct") or p["category_illiquid_limit_pct"]
    if illiquid is None:
        return False, None
    return illiquid > limit, {"illiquid_weight_pct": illiquid, "limit": limit}


def _t_overlap(ctx, p):
    overlap = _num(ctx, "max_pairwise_overlap_pct")
    if overlap is None:
        return False, None
    return overlap > p["max_overlap_pct"], {
        "overlap_pct": overlap, "with": ctx.get("max_overlap_with")}


def _t_allocation_drift(ctx, p):
    return bool(ctx.get("allocation_band_breached")), {
        "drift_pp": ctx.get("allocation_drift_pp")}


def _t_suitability(ctx, p):
    return bool(ctx.get("suitability_breached")), {"reason": ctx.get("suitability_breach_reason")}


def _t_goal_proximity(ctx, p):
    years, equity = _num(ctx, "years_to_goal"), _num(ctx, "holding_equity_pct")
    start, target = _num(ctx, "glide_start_years"), _num(ctx, "glide_target_equity_pct")
    if None in (years, equity, start, target):
        return False, None
    return (years <= start and equity > target), {
        "years_to_goal": years, "equity_pct": equity, "glide_target": target}


def _t_goal_reached(ctx, p):
    projected, target = _num(ctx, "projected_corpus"), _num(ctx, "inflation_adjusted_target")
    years = _num(ctx, "years_to_goal")
    if None in (projected, target, years):
        return False, None
    return (projected >= target and years <= p["max_years_to_goal"]), {
        "projected_corpus": projected, "target": target, "years_to_goal": years}


def _t_liquidity_need(ctx, p):
    months = _num(ctx, "declared_liquidity_need_within_months")
    if months is None:
        return False, None
    return months <= p["within_months"], {"months": months}


def _t_profile_change(ctx, p):
    return bool(ctx.get("risk_capacity_band_dropped")), {
        "from": ctx.get("previous_capacity_band"), "to": ctx.get("capacity_band")}


TRIGGERS = {
    "TRG-PERF-ROLLING-UNDER": _t_rolling_under,
    "TRG-PERF-CATEGORY-QUARTILE": _t_category_quartile,
    "TRG-RISKADJ-DETERIORATION": _t_riskadj,
    "TRG-DOWNSIDE-CAPTURE": _t_downside,
    "TRG-MANAGER-CHANGE": _t_manager_change,
    "TRG-MANDATE-DRIFT": _t_mandate_drift,
    "TRG-SCHEME-CHANGE": _t_scheme_change,
    "TRG-EXPENSE-INCREASE": _t_expense,
    "TRG-CONCENTRATION": _t_concentration,
    "TRG-AUM-SHOCK": _t_aum,
    "TRG-LIQUIDITY": _t_liquidity,
    "TRG-OVERLAP": _t_overlap,
    "TRG-ALLOCATION-DRIFT": _t_allocation_drift,
    "TRG-SUITABILITY-BREACH": _t_suitability,
    "TRG-GOAL-PROXIMITY": _t_goal_proximity,
    "TRG-GOAL-REACHED": _t_goal_reached,
    "TRG-LIQUIDITY-NEED": _t_liquidity_need,
    "TRG-PROFILE-CHANGE": _t_profile_change,
}


# ── dampener predicates ──────────────────────────────────────────────────────

def _d_exit_load(ctx, p):
    return bool(ctx.get("exit_load_applicable")), {"exit_load": ctx.get("exit_load_amount")}


def _d_tax(ctx, p):
    pct = _num(ctx, "estimated_tax_pct_of_value")
    if pct is None:
        return False, None
    return pct > p["max_tax_pct_of_value"], {"tax_pct_of_value": round(pct, 2)}


def _d_recent(ctx, p):
    months = _num(ctx, "months_since_last_action_on_holding")
    if months is None:
        return False, None
    return months < p["min_months_since_action"], {"months_since_last_action": months}


def _d_history(ctx, p):
    months = _num(ctx, "history_months")
    if months is None:
        return False, None
    return months < p["min_history_months"], {"history_months": months}


def _d_marginal(ctx, p):
    best, current = _num(ctx, "best_alternative_score"), _num(ctx, "current_score")
    if best is None or current is None:
        return False, None
    return (best - current) < p["min_score_advantage"], {
        "advantage": round(best - current, 1), "required": p["min_score_advantage"]}


DAMPENERS = {
    "DMP-EXIT-LOAD": _d_exit_load,
    "DMP-TAX-COST": _d_tax,
    "DMP-RECENT-ACTION": _d_recent,
    "DMP-SHORT-HISTORY": _d_history,
    "DMP-MARGINAL-ALTERNATIVE": _d_marginal,
}


# ── result ───────────────────────────────────────────────────────────────────

@dataclass
class Signal:
    scheme_code: str
    goal_id: str
    state: str
    score: float
    fired: list = field(default_factory=list)
    dampened: list = field(default_factory=list)
    material_events: list = field(default_factory=list)
    suppressed_reason: str = None
    ruleset_version: str = ""

    @property
    def is_actionable(self):
        return self.state not in ("HOLD", "WATCH")

    def as_dict(self):
        return {
            "scheme_code": self.scheme_code,
            "goal_id": self.goal_id,
            "state": self.state,
            "score": round(self.score, 1),
            "triggers_fired": self.fired,
            "dampeners_applied": self.dampened,
            "material_events": self.material_events,
            "suppressed_reason": self.suppressed_reason,
            "actionable": self.is_actionable,
            "ruleset_version": self.ruleset_version,
            "note": (
                "A signal is a prompt to look, not an instruction to transact. "
                "Nothing here executes."
            ),
        }


def evaluate(ctx, registry, *, scheme_code, goal_id, observations=None):
    """Evaluate all triggers for one holding.

    `observations` is the history of prior evaluations for this holding, used
    for persistence: a trigger must have been seen on enough separate
    evaluations before it is allowed to count. Pass None to skip persistence
    (used in tests and in the first evaluation of a new holding).
    """
    rules = registry.signals
    persistence = rules["persistence"]
    score = 0.0
    fired, dampened, material, emitted = [], [], [], []

    for spec in rules["triggers"]:
        tid = spec["id"]
        predicate = TRIGGERS.get(tid)
        if predicate is None:
            raise ConfigError(f"no predicate implemented for trigger {tid}")
        ok, evidence = predicate(ctx, spec.get("params") or {})
        if not ok:
            continue

        # persistence gate
        needed = persistence["min_confirmations"]
        if observations is not None and spec.get("persistence_days", 0) > 0:
            seen = sum(1 for o in observations if tid in o)
            if seen + 1 < needed:
                dampened.append({
                    "id": tid, "effect": 0,
                    "why": f"Seen {seen + 1} of {needed} required confirmations — "
                           "not yet persistent enough to count.",
                })
                continue

        score += spec["weight"]
        fired.append({
            "id": tid, "family": spec["family"], "weight": spec["weight"],
            "description": spec["description"], "evidence": evidence,
        })
        if spec.get("material_event"):
            material.append(tid)
        if spec.get("emits"):
            emitted.append(spec["emits"])

    for spec in rules["dampeners"]:
        did = spec["id"]
        predicate = DAMPENERS.get(did)
        if predicate is None:
            raise ConfigError(f"no predicate implemented for dampener {did}")
        ok, evidence = predicate(ctx, spec.get("params") or {})
        if ok:
            score += spec["weight"]
            dampened.append({
                "id": did, "effect": spec["weight"],
                "why": spec["description"], "evidence": evidence,
            })

    score = max(0.0, score)
    state, suppressed = _band_for(score, len(fired), bool(material), rules,
                                  emits_exit="EXIT_REVIEW" in emitted)

    # A trigger that explicitly emits a state takes precedence when it is at
    # least as serious — REBALANCE and REDUCE are different KINDS of action,
    # not just different intensities.
    if emitted and state != "HOLD":
        priority = ["REBALANCE", "REDUCE", "EXIT_REVIEW"]
        for want in reversed(priority):
            if want in emitted:
                if want == "EXIT_REVIEW" and state != "EXIT_REVIEW" and not material:
                    continue
                state = want
                break

    return Signal(
        scheme_code=scheme_code,
        goal_id=goal_id,
        state=state,
        score=score,
        fired=fired,
        dampened=dampened,
        material_events=material,
        suppressed_reason=suppressed,
        ruleset_version=rules.version,
    )


def _band_for(score, trigger_count, has_material_event, rules, emits_exit=False):
    """Map a score to a state, enforcing the minimum-trigger and exit rules."""
    bands = rules["bands"]
    order = ["EXIT_REVIEW", "SWITCH_REVIEW", "REVIEW", "WATCH", "HOLD"]
    for name in order:
        band = bands[name]
        if score < band["min_score"]:
            continue
        min_triggers = band.get("min_triggers")
        if min_triggers and trigger_count < min_triggers and not has_material_event:
            return (
                "REVIEW",
                f"Score reached {name}, but only {trigger_count} independent trigger(s) "
                f"fired and none was a material event. {min_triggers} are required before "
                "an action signal. Held at REVIEW.",
            )
        if band.get("requires_material_event_or_emitter") and not (has_material_event or emits_exit):
            return (
                "SWITCH_REVIEW",
                "Score reached EXIT_REVIEW, but every trigger was about how the scheme has "
                "performed — nothing changed about the scheme itself or about your goal. "
                "A weak fund is a reason to change the fund, not to leave the asset class. "
                "Held at SWITCH_REVIEW.",
            )
        return name, None
    return "HOLD", None


def build_context(**kwargs):
    """Assemble an evaluation context. Explicit, so a reader can see exactly
    what the engine is allowed to look at."""
    return dict(kwargs)


def explain(signal):
    """Investor-facing explanation, assembled from evidence, not generated."""
    if not signal.fired:
        return "Nothing material has changed for this holding."
    lines = [f"Signal: {signal.state.replace('_', ' ').title()}"]
    lines.append("What triggered this:")
    for t in sorted(signal.fired, key=lambda x: -x["weight"]):
        lines.append(f"  • {t['description']}")
    if signal.dampened:
        lines.append("What argues against acting:")
        for d in signal.dampened:
            lines.append(f"  • {d['why']}")
    if signal.suppressed_reason:
        lines.append(f"Held back: {signal.suppressed_reason}")
    return "\n".join(lines)
