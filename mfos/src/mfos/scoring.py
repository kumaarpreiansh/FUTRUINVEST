"""Fund Intelligence Engine — the 0-100 Fund Score.

What this score is: a measure of how well a scheme does the job its own
category describes, judged against its own benchmark and its own peers.

What it is not: a recommendation. A scheme can score 92 and still be entirely
wrong for a given investor. Suitability is computed separately in selection.py
and it can veto any score.

Two deliberate properties:

* Ranking by trailing return is the industry default and it is close to
  useless — it selects for whatever factor was in favour last year. So
  consistency (rolling-window win rate) carries more weight than return, and
  raw trailing return carries none directly.
* Commercial fields are structurally unreachable from this module. The
  prohibited_inputs list in the rule-set is enforced by a test that re-scores
  the universe with commissions perturbed and asserts nothing moves.
"""

from . import metrics as M
from .funds import data_quality_score

# Fields this module is forbidden to read. Guarded by test_no_commission_influence.
PROHIBITED = ("distributor_commission_pct", "amc_relationship")


def compute_metrics(scheme, risk_free=0.065):
    """Every performance number the score uses, computed once per scheme."""
    nav, bench = scheme.nav_history, scheme.benchmark_history
    out = {
        "history_months": scheme.history_months,
        "cagr_3y": M.trailing_cagr(nav, 3),
        "cagr_5y": M.trailing_cagr(nav, 5),
        "volatility": M.volatility(nav),
        "max_drawdown": M.max_drawdown(nav),
        "sharpe": M.sharpe(nav, risk_free),
        "sortino": M.sortino(nav, risk_free),
        "expense_ratio_pct": scheme.expense_ratio_pct,
        "aum_cr": scheme.aum_cr,
        "top10_weight_pct": scheme.top10_weight_pct,
        "turnover_pct": scheme.portfolio_turnover_pct,
    }
    if bench:
        out.update({
            "beta": M.beta(nav, bench),
            "alpha": M.alpha(nav, bench, risk_free),
            "tracking_error": M.tracking_error(nav, bench),
            "rolling_win_3y": M.rolling_outperformance_rate(nav, bench, 3),
            "rolling_win_5y": M.rolling_outperformance_rate(nav, bench, 5),
            "benchmark_cagr_3y": M.trailing_cagr(bench, 3),
        })
        up, down = M.capture_ratios(nav, bench)
        out["upside_capture"] = up
        out["downside_capture"] = down
        if out.get("cagr_3y") is not None and out.get("benchmark_cagr_3y") is not None:
            out["excess_cagr_3y"] = out["cagr_3y"] - out["benchmark_cagr_3y"]
        te = out.get("tracking_error")
        if te is not None and out.get("excess_cagr_3y") is not None:
            out["tracking_difference"] = out["excess_cagr_3y"]
    stats = M.rolling_stats(nav, 3)
    if stats:
        out["rolling_3y_stdev"] = stats["stdev"]
        out["rolling_3y_share_negative"] = stats["share_negative"]
        out["rolling_3y_min"] = stats["min"]
    return out


def _pop(peer_metrics, key):
    return [m.get(key) for m in peer_metrics if m.get(key) is not None]


def _pctile(value, peer_metrics, key, higher_is_better=True):
    return M.percentile_rank(value, _pop(peer_metrics, key), higher_is_better)


def _scale(value, lo, hi):
    """Linear 0-100 with clamping — used where a percentile makes no sense."""
    if value is None:
        return None
    if hi == lo:
        return 50.0
    return max(0.0, min(100.0, (value - lo) / (hi - lo) * 100.0))


def _weighted(components, weights):
    """Weighted mean over available components, renormalising for gaps.

    A missing component reduces coverage; it never silently scores zero.
    """
    usable = [(components[k], weights[k]) for k in weights if components.get(k) is not None]
    if not usable:
        return None, 0.0
    total_w = sum(w for _, w in usable)
    value = sum(v * w for v, w in usable) / total_w
    coverage = total_w / sum(weights.values())
    return value, coverage


def score_fund(scheme, peer_metrics, registry, own_metrics=None):
    """Score one scheme against pre-computed peer metrics."""
    cfg = registry.scoring
    m = own_metrics or compute_metrics(scheme)
    dq = data_quality_score(scheme, registry)

    # Disqualifiers run before anything is computed.
    if not dq["usable"]:
        return {
            "scheme_code": scheme.scheme_code,
            "overall": None,
            "status": "NOT_SCORED",
            "reason": "Insufficient verified data to score this scheme.",
            "data_quality": dq,
            "ruleset_version": cfg.version,
        }
    provisional = m["history_months"] < registry.threshold("min_history_months_for_scoring")

    pillars_cfg = dict(cfg["pillars"])
    weights = {name: spec["weight"] for name, spec in pillars_cfg.items()}
    if scheme.is_passive:
        weights = dict((cfg.get("overrides") or {})["passive"]["pillars"])

    # ── consistency ──────────────────────────────────────────────────────────
    consistency = {
        "rolling_outperformance_rate_3y": _scale(m.get("rolling_win_3y"), 0.2, 0.8),
        "rolling_outperformance_rate_5y": _scale(m.get("rolling_win_5y"), 0.2, 0.8),
        "return_rank_stability": _scale(
            -(m.get("rolling_3y_stdev") or 0) if m.get("rolling_3y_stdev") is not None else None,
            -0.10, -0.02),
        "benchmark_excess_cagr": _scale(m.get("excess_cagr_3y"), -0.04, 0.06),
    }

    # ── risk ─────────────────────────────────────────────────────────────────
    risk = {
        "sharpe_percentile": _pctile(m.get("sharpe"), peer_metrics, "sharpe"),
        "sortino_percentile": _pctile(m.get("sortino"), peer_metrics, "sortino"),
        "max_drawdown_percentile": _pctile(m.get("max_drawdown"), peer_metrics, "max_drawdown", False),
        "downside_capture_percentile": _pctile(m.get("downside_capture"), peer_metrics,
                                               "downside_capture", False),
        "volatility_percentile": _pctile(m.get("volatility"), peer_metrics, "volatility", False),
    }

    # ── cost ─────────────────────────────────────────────────────────────────
    load_burden = 100.0
    if scheme.exit_load_bands:
        worst = max(float(b.load_pct) for b in scheme.exit_load_bands)
        longest = max(int(b.within_days) for b in scheme.exit_load_bands)
        load_burden = max(0.0, 100.0 - worst * 30.0 - (longest / 365.0) * 10.0)
    cost = {
        "expense_ratio_percentile": _pctile(m.get("expense_ratio_pct"), peer_metrics,
                                            "expense_ratio_pct", False),
        "exit_load_burden": load_burden,
        "plan_type_penalty": 100.0 if scheme.plan == "DIRECT" else 35.0,
    }

    # ── quality ──────────────────────────────────────────────────────────────
    tenure_years = None
    if scheme.manager_since and scheme.nav_date:
        tenure_years = (scheme.nav_date - scheme.manager_since).days / 365.25
    elif scheme.manager_since and scheme.nav_history:
        tenure_years = (scheme.nav_history[-1][0] - scheme.manager_since).days / 365.25

    aum = scheme.aum_cr
    aum_score = None
    if aum is not None:
        if aum < 100:
            aum_score = 25.0                 # fragile: high per-unit costs, closure risk
        elif aum < 500:
            aum_score = 65.0
        elif aum <= 25000:
            aum_score = 100.0
        else:
            aum_score = max(35.0, 100.0 - (aum - 25000) / 1000.0)   # capacity drag

    peer_top10 = _pop(peer_metrics, "top10_weight_pct")
    quality = {
        "manager_tenure": _scale(tenure_years, 0.5, 7.0),
        "manager_stability": _scale(-(scheme.manager_changes_5y or 0), -3, 0),
        "portfolio_concentration": (
            M.percentile_rank(scheme.top10_weight_pct, peer_top10, higher_is_better=False)
            if scheme.top10_weight_pct is not None and len(peer_top10) >= 2 else None
        ),
        "aum_adequacy": aum_score,
        "turnover_reasonableness": _scale(
            -(scheme.portfolio_turnover_pct or 0) if scheme.portfolio_turnover_pct is not None else None,
            -250, -20),
        "mandate_drift": _scale(-mandate_drift(scheme), -0.5, 0.0),
    }

    # ── tracking ─────────────────────────────────────────────────────────────
    tracking = {
        "tracking_error_percentile": (
            _pctile(m.get("tracking_error"), peer_metrics, "tracking_error", False)
            if scheme.is_passive else
            _scale(m.get("tracking_error"), 0.25, 0.02)      # active: too-low TE is closet indexing
        ),
        "tracking_difference": _scale(m.get("tracking_difference"), -0.02, 0.01),
    }

    raw_pillars = {
        "consistency": (consistency, pillars_cfg["consistency"]["components"]),
        "risk": (risk, pillars_cfg["risk"]["components"]),
        "cost": (cost, pillars_cfg["cost"]["components"]),
        "quality": (quality, pillars_cfg["quality"]["components"]),
        "tracking": (tracking, pillars_cfg["tracking"]["components"]),
    }

    pillar_scores, pillar_coverage, components_out = {}, {}, {}
    for name, (components, comp_weights) in raw_pillars.items():
        value, coverage = _weighted(components, comp_weights)
        pillar_scores[name] = value
        pillar_coverage[name] = round(coverage * 100, 1)
        components_out[name] = {k: (round(v, 1) if v is not None else None)
                               for k, v in components.items()}

    overall, coverage = _weighted(pillar_scores, weights)
    if overall is None:
        return {
            "scheme_code": scheme.scheme_code, "overall": None, "status": "NOT_SCORED",
            "reason": "No pillar could be computed from available data.",
            "data_quality": dq, "ruleset_version": cfg.version,
        }

    band = next(b["label"] for b in cfg["bands"] if overall >= b["min"])

    return {
        "scheme_code": scheme.scheme_code,
        "scheme_name": scheme.scheme_name,
        "overall": round(overall, 1),
        "band": band,
        "status": "PROVISIONAL" if provisional else "SCORED",
        "provisional_reason": ("Under three years of history — scored, but treat with caution."
                               if provisional else None),
        "pillars": {k: (round(v, 1) if v is not None else None) for k, v in pillar_scores.items()},
        "pillar_weights": weights,
        "pillar_coverage_pct": pillar_coverage,
        "components": components_out,
        "coverage_pct": round(coverage * 100, 1),
        "data_quality": dq,
        "peer_group": scheme.sebi_category,
        "peer_count": len(peer_metrics),
        "metrics": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in m.items()},
        "ruleset_version": cfg.version,
        "methodology_url": "docs/F-FUND-SCORING-METHODOLOGY.md",
    }


def mandate_drift(scheme):
    """How far the actual portfolio sits from what the category promises.

    0.0 = on mandate. Uses the market-cap split where the category defines one.
    This is a cause-level signal: a large-cap fund quietly holding 30% small
    caps is taking risk the investor did not agree to.
    """
    if not scheme.market_cap_allocation or not scheme.sebi_category:
        return 0.0
    caps = scheme.market_cap_allocation
    large = caps.get("LARGE", 0.0)
    mid = caps.get("MID", 0.0)
    small = caps.get("SMALL", 0.0)
    cat = scheme.sebi_category
    if cat == "EQUITY_LARGE_CAP":
        return max(0.0, (80.0 - large) / 100.0) + max(0.0, (small - 5.0) / 100.0)
    if cat == "EQUITY_MID_CAP":
        return max(0.0, (65.0 - mid) / 100.0)
    if cat == "EQUITY_SMALL_CAP":
        return max(0.0, (65.0 - small) / 100.0)
    if cat == "EQUITY_LARGE_AND_MID":
        return max(0.0, (35.0 - large) / 100.0) + max(0.0, (35.0 - mid) / 100.0)
    return 0.0


def score_universe(universe, registry, sebi_category=None):
    """Score a whole peer group in one pass, sharing the peer metrics."""
    schemes = universe.category(sebi_category) if sebi_category else list(universe)
    by_category = {}
    for s in schemes:
        by_category.setdefault(s.sebi_category, []).append(s)

    results = {}
    min_peers = registry.scoring["ruleset"]["min_peers_for_percentile"]
    for category, members in by_category.items():
        peer_metrics = [compute_metrics(s) for s in members]
        if len(members) < min_peers:
            # Percentile ranking over a handful of peers is noise dressed as
            # precision. Score anyway, but say so.
            note = (f"Only {len(members)} peers in {category}; percentile-based "
                    "components are indicative only.")
        else:
            note = None
        for scheme, own in zip(members, peer_metrics):
            others = [m for m in peer_metrics if m is not own]
            result = score_fund(scheme, others, registry, own_metrics=own)
            if note:
                result["thin_peer_group_note"] = note
            results[scheme.scheme_code] = result
    return results
