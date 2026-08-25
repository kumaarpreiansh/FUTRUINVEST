"""Portfolio Construction, Overlap and Rebalancing.

Construction works in sleeves — core, growth, stability, diversifier — because
that is how a portfolio should be explained, not just built: every rupee has a
job, and the investor can be told what each sleeve is for. Not every goal gets
every sleeve; a three-year goal has no growth sleeve at all.

Overlap analysis exists because the most common defect in Indian retail
portfolios is not a bad fund. It is five funds holding the same thirty stocks,
bought over five years, each one "top rated" at the time of purchase.

Rebalancing prefers cashflow over sales. Selling to rebalance is the expensive
way to do something new contributions usually achieve for free.
"""

from dataclasses import dataclass, field
from decimal import Decimal

from .errors import ConfigError, SuitabilityBlock
from .money import money, rupees
from .risk import BAND_MAX_EQUITY_PCT

SLEEVES = ("core", "growth", "stability", "diversifier")

SLEEVE_PURPOSE = {
    "core": "The engine of the portfolio. Broad, boring, long-held.",
    "growth": "A measured tilt towards higher-return, higher-volatility segments.",
    "stability": "Ballast. Its job is to be there when equity is not.",
    "diversifier": "Something that does not move with the rest.",
}


# ── target allocation ────────────────────────────────────────────────────────

def select_policy(registry, goal, risk_band):
    """Pick the published allocation policy for this goal and risk band."""
    for policy in registry.allocation["policies"]:
        cond = policy["applies_when"]
        if "goal_type" in cond and cond["goal_type"] != goal.goal_type:
            continue
        if "goal_type" in cond:
            return policy
        if "min_years" in cond and goal.years_to_goal < cond["min_years"]:
            continue
        if "max_years" in cond and goal.years_to_goal > cond["max_years"]:
            continue
        if "risk_band" in cond and risk_band not in cond["risk_band"]:
            continue
        return policy
    raise ConfigError(
        f"No allocation policy covers a {goal.years_to_goal:.1f}-year "
        f"{goal.goal_type} goal at {risk_band} risk."
    )


def target_allocation(registry, goal, risk_assessment):
    """Target asset-class weights, capped by the investor's binding risk band.

    The cap is a hard constraint. If the policy allocation would breach it, the
    excess equity is moved to stability — the plan bends, the risk ceiling does
    not.
    """
    band = risk_assessment.binding_band
    policy = select_policy(registry, goal, band)

    weights, sleeve_map = {}, {}
    for sleeve, assets in policy["sleeves"].items():
        for asset, weight in (assets or {}).items():
            weights[asset] = weights.get(asset, 0.0) + float(weight)
            sleeve_map[asset] = sleeve

    equity_pct = sum(w for a, w in weights.items() if a.startswith("EQUITY"))
    ceiling = BAND_MAX_EQUITY_PCT[band]
    capped = False
    if equity_pct > ceiling:
        capped = True
        excess = equity_pct - ceiling
        for asset in list(weights):
            if asset.startswith("EQUITY"):
                weights[asset] -= excess * (weights[asset] / equity_pct)
        sink = "DEBT_MEDIUM" if "DEBT_MEDIUM" in weights else "DEBT_SHORT"
        weights[sink] = weights.get(sink, 0.0) + excess
        sleeve_map.setdefault(sink, "stability")

    # small-cap hygiene limit
    limits = registry.allocation["limits"]
    equity_now = sum(w for a, w in weights.items() if a.startswith("EQUITY"))
    small = weights.get("EQUITY_SMALL", 0.0)
    max_small = equity_now * limits["max_smallcap_pct_of_equity"] / 100.0
    if small > max_small:
        moved = small - max_small
        weights["EQUITY_SMALL"] = max_small
        weights["EQUITY_FLEXI"] = weights.get("EQUITY_FLEXI", 0.0) + moved

    total = sum(weights.values())
    weights = {a: round(w / total * 100, 2) for a, w in weights.items() if w > 0.01}

    return {
        "policy_id": policy["id"],
        "risk_band": band,
        "weights": weights,
        "sleeves": {
            sleeve: {a: weights[a] for a in weights if sleeve_map.get(a) == sleeve}
            for sleeve in SLEEVES
        },
        "sleeve_purpose": {s: SLEEVE_PURPOSE[s] for s in SLEEVES
                           if any(sleeve_map.get(a) == s for a in weights)},
        "equity_pct": round(sum(w for a, w in weights.items() if a.startswith("EQUITY")), 2),
        "equity_ceiling_pct": ceiling,
        "capped_by_risk_band": capped,
        "cap_note": (
            f"The published policy for this goal would hold more equity, but your "
            f"binding risk band ({band}) caps it at {ceiling}%. The plan adjusts; "
            "the ceiling does not."
        ) if capped else None,
        "policy_note": policy.get("note"),
        "ruleset_version": registry.allocation.version,
    }


# ── holdings ─────────────────────────────────────────────────────────────────

@dataclass
class Holding:
    scheme_code: str
    units: Decimal
    lots: list = field(default_factory=list)      # tax.Lot
    goal_id: str = None

    def value(self, nav):
        return money(Decimal(str(self.units)) * money(nav))


@dataclass
class Portfolio:
    investor_id: str
    holdings: list = field(default_factory=list)

    def value(self, universe):
        total = Decimal("0")
        for h in self.holdings:
            scheme = universe.get(h.scheme_code)
            if scheme and scheme.nav:
                total += h.value(scheme.nav)
        return money(total)

    def weights(self, universe):
        total = float(self.value(universe))
        if not total:
            return {}
        out = {}
        for h in self.holdings:
            scheme = universe.get(h.scheme_code)
            if scheme and scheme.nav:
                out[h.scheme_code] = float(h.value(scheme.nav)) / total * 100
        return out

    def asset_class_weights(self, universe):
        out = {}
        for code, weight in self.weights(universe).items():
            scheme = universe.get(code)
            if scheme and scheme.asset_class:
                out[scheme.asset_class] = out.get(scheme.asset_class, 0.0) + weight
        return out

    def for_goal(self, goal_id):
        return Portfolio(self.investor_id, [h for h in self.holdings if h.goal_id == goal_id])


# ── overlap and concentration ────────────────────────────────────────────────

def pairwise_overlap(scheme_a, scheme_b):
    """Percentage of portfolio common to both schemes.

    Standard method: sum of the minimum weight of each shared security. Two
    funds each holding a stock at 5% overlap by 5 points on that stock, not 10.
    """
    a, b = scheme_a.holdings, scheme_b.holdings
    if not a or not b:
        return None
    shared = set(a) & set(b)
    return round(sum(min(a[s], b[s]) for s in shared), 2)


def overlap_matrix(portfolio, universe):
    codes = [h.scheme_code for h in portfolio.holdings]
    pairs = []
    for i, ca in enumerate(codes):
        for cb in codes[i + 1:]:
            sa, sb = universe.get(ca), universe.get(cb)
            if not sa or not sb:
                continue
            value = pairwise_overlap(sa, sb)
            if value is None:
                continue
            pairs.append({
                "a": ca, "a_name": sa.scheme_name, "b": cb, "b_name": sb.scheme_name,
                "overlap_pct": value,
                "same_category": sa.sebi_category == sb.sebi_category,
            })
    return sorted(pairs, key=lambda p: -p["overlap_pct"])


def concentration(portfolio, universe):
    """Where the real, look-through risk actually sits."""
    weights = portfolio.weights(universe)
    total = sum(weights.values()) or 1.0

    by_amc, by_sector, by_cap, by_security = {}, {}, {}, {}
    for code, weight in weights.items():
        scheme = universe.get(code)
        if not scheme:
            continue
        share = weight / total
        by_amc[scheme.amc] = by_amc.get(scheme.amc, 0.0) + weight
        for sector, sw in (scheme.sector_allocation or {}).items():
            by_sector[sector] = by_sector.get(sector, 0.0) + sw * share
        for cap, cw in (scheme.market_cap_allocation or {}).items():
            by_cap[cap] = by_cap.get(cap, 0.0) + cw * share
        for sec, hw in (scheme.holdings or {}).items():
            by_security[sec] = by_security.get(sec, 0.0) + hw * share

    return {
        "by_amc": {k: round(v, 2) for k, v in sorted(by_amc.items(), key=lambda x: -x[1])},
        "by_sector": {k: round(v, 2) for k, v in sorted(by_sector.items(), key=lambda x: -x[1])},
        "by_market_cap": {k: round(v, 2) for k, v in sorted(by_cap.items(), key=lambda x: -x[1])},
        "top_securities": dict(sorted(
            ((k, round(v, 2)) for k, v in by_security.items()), key=lambda x: -x[1])[:10]),
        "fund_count": len(weights),
    }


def analyse_overlap(portfolio, universe, registry):
    """Overlap plus the judgement call: is this actually a problem?

    Overlap between a large cap and a flexi cap is expected and largely
    harmless. Overlap between two funds in the same category is duplication
    the investor is paying twice for.
    """
    limits = registry.allocation["limits"]
    matrix = overlap_matrix(portfolio, universe)
    conc = concentration(portfolio, universe)

    findings = []
    for pair in matrix:
        if pair["overlap_pct"] <= limits["max_portfolio_overlap_pct"]:
            continue
        if pair["same_category"]:
            verdict = "PROBLEMATIC"
            because = ("Same category and heavily overlapping — you are paying two expense "
                       "ratios for one portfolio, and getting one portfolio's diversification.")
        else:
            verdict = "EXPECTED"
            because = ("Different categories with a shared large-cap core. This is normal and "
                       "not worth acting on by itself.")
        findings.append({**pair, "verdict": verdict, "why": because})

    max_amc = max(conc["by_amc"].values(), default=0.0)
    max_sector = max(conc["by_sector"].values(), default=0.0)
    if max_amc > limits["max_single_amc_pct"]:
        amc = max(conc["by_amc"], key=conc["by_amc"].get)
        findings.append({
            "type": "AMC_CONCENTRATION", "verdict": "REVIEW",
            "why": f"{max_amc:.0f}% of the portfolio sits with {amc}. That is a single "
                   "operational and process risk, separate from market risk.",
        })
    if max_sector > limits["max_sector_pct"]:
        sector = max(conc["by_sector"], key=conc["by_sector"].get)
        findings.append({
            "type": "SECTOR_CONCENTRATION", "verdict": "REVIEW",
            "why": f"Looking through the funds, {max_sector:.0f}% of your equity is in "
                   f"{sector}. None of your funds is a sector fund; this is an accident "
                   "of holding several funds with the same tilt.",
        })
    if conc["fund_count"] > limits["max_funds_total"]:
        findings.append({
            "type": "TOO_MANY_FUNDS", "verdict": "REVIEW",
            "why": f"{conc['fund_count']} schemes. Past roughly eight you own the market "
                   "at an active fee, and you cannot monitor them all properly.",
        })

    problematic = [f for f in findings if f.get("verdict") == "PROBLEMATIC"]
    return {
        "pairs": matrix[:10],
        "max_pairwise_overlap_pct": matrix[0]["overlap_pct"] if matrix else None,
        "max_amc_pct": round(max_amc, 2),
        "fund_count": conc["fund_count"],
        "concentration": conc,
        "findings": findings,
        "headline": (
            f"You own {conc['fund_count']} schemes and the two most similar overlap "
            f"{matrix[0]['overlap_pct']:.0f}%." if matrix else
            f"You own {conc['fund_count']} scheme(s)."
        ),
        "action_needed": bool(problematic),
    }


# ── rebalancing ──────────────────────────────────────────────────────────────

def drift(current_weights, target_weights):
    keys = set(current_weights) | set(target_weights)
    out = {}
    for key in keys:
        cur = current_weights.get(key, 0.0)
        tgt = target_weights.get(key, 0.0)
        out[key] = {"current": round(cur, 2), "target": round(tgt, 2),
                    "drift_pp": round(cur - tgt, 2)}
    return dict(sorted(out.items(), key=lambda kv: -abs(kv[1]["drift_pp"])))


def rebalance_plan(portfolio, universe, target, registry, *, monthly_inflow=Decimal("0"),
                   months_since_last_rebalance=999, years_to_goal=None,
                   tax_estimator=None):
    """Whether to rebalance, and if so, how — cheapest route first.

    Order of preference:
      1. do nothing (drift is inside the band)
      2. redirect new contributions (free, no tax, no exit load)
      3. gradual sales inside the tax exemption
      4. a taxable sale, only when drift is large and the goal is close
    """
    policy = registry.allocation["rebalancing"]
    if not portfolio.holdings or not float(portfolio.value(universe)):
        # Nothing held yet. An empty portfolio is 100% "off target" arithmetically,
        # but the answer is to build it, not to rebalance it.
        return {
            "action": "BUILD",
            "signal": "HOLD",
            "drift": {},
            "why": "Nothing is held for this goal yet, so there is nothing to rebalance.",
        }
    current = portfolio.asset_class_weights(universe)
    target_weights = target["weights"]
    drifts = drift(current, target_weights)

    breaches = []
    for asset, row in drifts.items():
        tgt = row["target"]
        absolute = abs(row["drift_pp"]) > policy["absolute_band_pct"]
        relative = tgt > 0 and abs(row["drift_pp"]) > tgt * policy["relative_band_pct"] / 100.0
        if absolute and relative:
            breaches.append({**row, "asset_class": asset})

    if not breaches:
        return {
            "action": "NONE",
            "signal": "HOLD",
            "drift": drifts,
            "why": "Every asset class is inside its tolerance band. Rebalancing now would "
                   "cost money and change nothing.",
        }

    if months_since_last_rebalance < policy["min_months_between_rebalances"]:
        return {
            "action": "DEFER",
            "signal": "WATCH",
            "drift": drifts,
            "breaches": breaches,
            "why": f"Allocation has drifted, but the last rebalance was "
                   f"{months_since_last_rebalance} months ago. Rebalancing more often than "
                   f"every {policy['min_months_between_rebalances']} months adds cost without "
                   "improving outcomes.",
        }

    portfolio_value = float(portfolio.value(universe))
    worst = max(breaches, key=lambda b: abs(b["drift_pp"]))
    gap_value = abs(worst["drift_pp"]) / 100.0 * portfolio_value
    inflow = float(monthly_inflow) * policy["cashflow_rebalance_max_months"]

    if policy["prefer_cashflow_rebalancing"] and inflow >= gap_value * 0.7:
        months_needed = int(gap_value / float(monthly_inflow)) + 1 if monthly_inflow else None
        return {
            "action": "CASHFLOW_REBALANCE",
            "signal": "REBALANCE",
            "drift": drifts,
            "breaches": breaches,
            "why": (
                f"Your allocation is {worst['drift_pp']:+.1f} points off target on "
                f"{worst['asset_class']}. Redirecting your existing contributions closes "
                f"that in about {months_needed} months with no tax and no exit load — "
                "selling to fix it would cost money to achieve the same thing."
            ),
            "redirect_to": [b["asset_class"] for b in breaches if b["drift_pp"] < 0],
            "reduce_new_flows_to": [b["asset_class"] for b in breaches if b["drift_pp"] > 0],
            "estimated_months": months_needed,
            "tax_cost": "0",
        }

    close_to_goal = years_to_goal is not None and years_to_goal <= (
        registry.allocation["glide_path"]["start_years_before_goal"]
    )
    large_drift = abs(worst["drift_pp"]) > policy["absolute_band_pct"] * 1.5

    if not (close_to_goal and large_drift):
        return {
            "action": "GRADUAL",
            "signal": "REBALANCE",
            "drift": drifts,
            "breaches": breaches,
            "why": (
                f"Drift of {worst['drift_pp']:+.1f} points on {worst['asset_class']} is worth "
                "correcting, but not worth a taxable sale yet. Redirect contributions now and "
                "review in six months."
            ),
            "tax_cost": "0",
        }

    return {
        "action": "SELL_TO_REBALANCE",
        "signal": "REBALANCE",
        "drift": drifts,
        "breaches": breaches,
        "why": (
            f"{worst['asset_class']} is {worst['drift_pp']:+.1f} points off target and the goal "
            f"is {years_to_goal:.0f} years away. At this distance the risk of leaving it is "
            "larger than the tax cost of fixing it."
        ),
        "requires": [
            "tax estimate on every lot to be sold",
            "exit load check",
            "explicit investor consent",
        ],
        "note": "The decision simulator must show before/after allocation, tax and goal "
                "probability before consent is requested.",
    }
