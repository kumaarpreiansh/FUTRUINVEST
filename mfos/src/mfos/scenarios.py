"""Scenario Engine.

Rule: no projection is ever shown as a single number. Every projection is a
range, and the range is generated from published, versioned assumptions the
investor can open and read.

Two families:
  * SCENARIOS — conservative / base / optimistic. Different assumptions about
    a normal future.
  * STRESS    — a specific bad thing happening. Not a different assumption; a
    modelled event applied on top of the base assumptions.
"""

import math
from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal

from .errors import ConfigError
from .goals import (Goal, contribution_schedule, inflation_adjusted_target,
                    probability_of_reaching, project_goal)
from .money import money, rupees

SCENARIOS = ("conservative", "base", "optimistic")


def _corr(matrix, a, b):
    if a == b:
        return 1.0
    for x, y in ((a, b), (b, a)):
        row = matrix.get(x)
        if row and y in row:
            return float(row[y])
    return 0.0


def portfolio_assumptions(allocation, registry, scenario="base"):
    """Expected return and volatility for an asset-class allocation.

    Volatility is computed through the correlation matrix. Summing asset
    volatilities would overstate portfolio risk by roughly a third for a
    typical Indian balanced portfolio, which would in turn understate every
    goal probability on the platform.

    allocation: {asset_class: weight_pct}, weights summing to 100.
    """
    if scenario not in SCENARIOS:
        raise ConfigError(f"unknown scenario {scenario!r}")
    cma = registry.allocation["capital_market_assumptions"]["classes"]
    corr = registry.allocation["correlations"]

    total = sum(allocation.values())
    if total <= 0:
        raise ConfigError("allocation is empty")
    weights = {k: v / total for k, v in allocation.items() if v}

    unknown = [k for k in weights if k not in cma]
    if unknown:
        raise ConfigError(f"no capital market assumption for {unknown}")

    expected = sum(w * float(cma[a][scenario]) for a, w in weights.items())

    variance = 0.0
    for a, wa in weights.items():
        for b, wb in weights.items():
            variance += wa * wb * float(cma[a]["volatility"]) * float(cma[b]["volatility"]) * _corr(corr, a, b)
    vol = math.sqrt(max(variance, 0.0))

    inflation = float(registry.allocation["capital_market_assumptions"]["inflation"][scenario])
    return {
        "scenario": scenario,
        "expected_return_pct": expected,
        "volatility_pct": vol,
        "inflation_pct": inflation,
        "weights": {k: round(v * 100, 2) for k, v in weights.items()},
        "source": "config/allocation/allocation_policy.v1.yaml",
        "ruleset_version": registry.allocation.version,
        "basis": registry.allocation["capital_market_assumptions"]["basis"],
    }


def glide_path_allocation(allocation, registry, years_to_goal):
    """De-risk on a published schedule as the goal approaches.

    An investor who can see the glide path in year one does not experience
    year-nine de-risking as the platform panicking.
    """
    gp = registry.allocation["glide_path"]
    if not gp.get("enabled") or years_to_goal > gp["start_years_before_goal"]:
        return dict(allocation)

    equity = {k: v for k, v in allocation.items() if k.startswith("EQUITY")}
    equity_pct = sum(equity.values())
    if not equity_pct:
        return dict(allocation)

    start = float(gp["start_years_before_goal"])
    target_at_goal = float(gp["target_equity_pct_at_goal"])
    progress = max(0.0, min(1.0, (start - years_to_goal) / start))
    new_equity_pct = equity_pct + (target_at_goal - equity_pct) * progress
    moved = equity_pct - new_equity_pct

    out = dict(allocation)
    for k, v in equity.items():
        out[k] = v * (new_equity_pct / equity_pct) if equity_pct else 0.0
    # Everything taken out of equity goes to the shortest-duration stability
    # sleeve available, because this money is now close to being needed.
    sink = "DEBT_SHORT" if "DEBT_SHORT" in out else ("DEBT_MEDIUM" if "DEBT_MEDIUM" in out else "CASH")
    out[sink] = out.get(sink, 0.0) + moved
    return {k: v for k, v in out.items() if v > 1e-9}


def project_all_scenarios(goal, allocation, registry):
    """The three-scenario view that every goal screen must show."""
    out = {}
    for scenario in SCENARIOS:
        assumptions = portfolio_assumptions(allocation, registry, scenario)
        out[scenario] = project_goal(
            goal,
            expected_return_pct=assumptions["expected_return_pct"],
            volatility_pct=assumptions["volatility_pct"],
            inflation_pct=assumptions["inflation_pct"],
            scenario=scenario,
            assumptions=assumptions,
        )
    return out


# ── stress tests ─────────────────────────────────────────────────────────────

@dataclass
class StressResult:
    stress_id: str
    label: str
    description: str
    baseline_probability: float
    stressed_probability: float
    baseline_corpus: Decimal
    stressed_corpus: Decimal
    recovery_note: str

    @property
    def probability_delta_pp(self):
        return round((self.stressed_probability - self.baseline_probability) * 100, 1)

    def as_dict(self):
        return {
            "stress_id": self.stress_id,
            "label": self.label,
            "description": self.description,
            "baseline_probability_pct": round(self.baseline_probability * 100, 1),
            "stressed_probability_pct": round(self.stressed_probability * 100, 1),
            "delta_pp": self.probability_delta_pp,
            "baseline_median_corpus": str(self.baseline_corpus),
            "stressed_median_corpus": str(self.stressed_corpus),
            "note": self.recovery_note,
        }


STRESS_LIBRARY = {
    "EQUITY_CRASH_30": {
        "label": "Equity falls 30% now",
        "description": "An immediate 30% fall in the equity portion of the existing corpus, with contributions continuing.",
        "corpus_shock_pct": -30.0,
        "shock_applies_to": "EQUITY",
    },
    "PROLONGED_BEAR": {
        "label": "Five flat years",
        "description": "Equity returns nothing for five years, then reverts to the base assumption.",
        "return_haircut_pct": None,
        "flat_years": 5,
    },
    "INFLATION_SPIKE": {
        "label": "Inflation runs 3pp higher",
        "description": "Inflation is 3 percentage points above the base assumption for the whole horizon, so the goal costs more.",
        "inflation_add_pp": 3.0,
    },
    "INCOME_DROP": {
        "label": "Contributions cut 40%",
        "description": "A fall in income reduces every future contribution by 40%.",
        "contribution_multiplier": 0.60,
    },
    "MISSED_SIPS_12": {
        "label": "Twelve months of missed SIPs",
        "description": "No contributions for the next twelve months, then normal service resumes.",
        "skip_months": 12,
    },
    "EARLY_WITHDRAWAL": {
        "label": "25% withdrawn now",
        "description": "A quarter of the existing corpus is withdrawn for something else and never replaced.",
        "corpus_shock_pct": -25.0,
        "shock_applies_to": "ALL",
    },
    "GOAL_PULLED_FORWARD": {
        "label": "Goal arrives three years early",
        "description": "The goal date moves forward by three years, cutting both compounding and contributions.",
        "years_delta": -3.0,
    },
}


def _stressed_goal(goal, spec, equity_share):
    g = deepcopy(goal)
    if "corpus_shock_pct" in spec and spec["corpus_shock_pct"] is not None:
        share = equity_share if spec.get("shock_applies_to") == "EQUITY" else 1.0
        factor = 1.0 + (spec["corpus_shock_pct"] / 100.0) * share
        g.current_corpus = money(float(g.current_corpus) * max(factor, 0.0))
    if spec.get("contribution_multiplier"):
        g.monthly_contribution = money(float(g.monthly_contribution) * spec["contribution_multiplier"])
    if spec.get("years_delta"):
        g.years_to_goal = max(0.5, g.years_to_goal + spec["years_delta"])
    return g


def run_stress(goal, allocation, registry, stress_id):
    """One stress test, expressed as the change in goal probability.

    Deliberately reported as a probability delta rather than a rupee loss:
    'you would lose ₹8 lakh' provokes selling; 'your goal probability falls
    from 84% to 71% and recovers if you keep contributing' provokes thinking.
    """
    if stress_id not in STRESS_LIBRARY:
        raise ConfigError(f"unknown stress {stress_id!r}")
    spec = STRESS_LIBRARY[stress_id]

    base = portfolio_assumptions(allocation, registry, "base")
    equity_share = sum(v for k, v in base["weights"].items() if k.startswith("EQUITY")) / 100.0

    baseline = project_goal(
        goal, base["expected_return_pct"], base["volatility_pct"], base["inflation_pct"],
        scenario="base", assumptions=base,
    )

    g = _stressed_goal(goal, spec, equity_share)
    mu = base["expected_return_pct"]
    infl = base["inflation_pct"] + spec.get("inflation_add_pp", 0.0)

    flows = contribution_schedule(g)
    if spec.get("skip_months"):
        skip = spec["skip_months"]
        flows = [f for f in flows if f[0] <= g.years_to_goal - skip / 12.0]
    if float(g.current_corpus):
        flows.append((g.years_to_goal, float(g.current_corpus)))

    if spec.get("flat_years"):
        # Compounding is simply switched off for the first N years: an amount
        # invested with F years left compounds for (F - flat) years only.
        flat = float(spec["flat_years"])
        flows = [(max(0.0, t - min(flat, t)), c) for t, c in flows]

    target = float(inflation_adjusted_target(g, infl))
    prob = probability_of_reaching(flows, mu / 100.0, base["volatility_pct"] / 100.0, target)

    from .goals import wealth_percentile
    stressed_median = wealth_percentile(flows, mu / 100.0, base["volatility_pct"] / 100.0, 0.5) or 0.0

    if baseline.probability < 0.10:
        note = ("This goal is already unlikely on the base plan, so the stress barely moves it. "
                "The contribution, the target or the date is the problem, not this scenario.")
    elif prob is not None and prob >= baseline.probability - 0.05:
        note = "The plan absorbs this. No action indicated."
    elif spec.get("skip_months") or spec.get("contribution_multiplier"):
        note = "Contribution-driven, and therefore recoverable: restoring or stepping up contributions restores most of the gap."
    else:
        note = "Materially reduces the probability. Review the contribution level or the goal date, not the fund choice."

    return StressResult(
        stress_id=stress_id,
        label=spec["label"],
        description=spec["description"],
        baseline_probability=baseline.probability,
        stressed_probability=prob if prob is not None else 0.0,
        baseline_corpus=baseline.corpus_p50,
        stressed_corpus=rupees(stressed_median),
        recovery_note=note,
    )


def run_all_stresses(goal, allocation, registry):
    return [run_stress(goal, allocation, registry, sid) for sid in STRESS_LIBRARY]


# ── digital twin / decision simulator ────────────────────────────────────────

def what_if(goal, allocation, registry, *, monthly_contribution=None, step_up_pct=None,
            years_to_goal=None, lump_sum=None, allocation_override=None):
    """Re-run a goal under a hypothetical change.

    This is the engine behind both the Portfolio Digital Twin ('what if I
    increase my SIP by ₹5,000?') and the Decision Simulator that must run
    before any material recommendation is presented.
    """
    g = deepcopy(goal)
    if monthly_contribution is not None:
        g.monthly_contribution = money(monthly_contribution)
    if step_up_pct is not None:
        g.annual_step_up_pct = float(step_up_pct)
    if years_to_goal is not None:
        g.years_to_goal = float(years_to_goal)
    if lump_sum is not None:
        g.current_corpus = money(float(g.current_corpus) + float(lump_sum))

    alloc = allocation_override or allocation
    before = project_all_scenarios(goal, allocation, registry)["base"]
    after = project_all_scenarios(g, alloc, registry)["base"]

    return {
        "before": before.as_dict(),
        "after": after.as_dict(),
        "probability_delta_pp": round((after.probability - before.probability) * 100, 1),
        "corpus_delta": str(rupees(float(after.corpus_p50) - float(before.corpus_p50))),
        "change": {
            "monthly_contribution": str(g.monthly_contribution) if monthly_contribution is not None else None,
            "step_up_pct": step_up_pct,
            "years_to_goal": years_to_goal,
            "lump_sum": str(money(lump_sum)) if lump_sum is not None else None,
            "allocation_changed": allocation_override is not None,
        },
        "disclaimer_ids": ["DISC-PROJECTION-001"],
    }
