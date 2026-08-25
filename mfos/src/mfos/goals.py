"""Goal Engine.

A goal is the unit of meaning in this platform. Funds are instruments; goals
are the thing the investor actually has. Everything downstream — allocation,
selection, signals, exits — is answerable to a goal.

The probability model is a closed-form moment-matched lognormal, not a random
simulation, for one reason: an investor's stated probability must be exactly
reproducible years later during an audit. Same inputs, same number, forever.

Method, stated plainly so it can be argued with:
  * annual simple return R has mean mu and standard deviation sigma
  * log-return parameters follow: s^2 = ln(1 + sigma^2/(1+mu)^2), m = ln(1+mu) - s^2/2
  * terminal wealth W = sum_i c_i * exp(X_i), X_i ~ N(m*t_i, s^2*t_i),
    with Cov(X_i, X_j) = s^2 * min(t_i, t_j) because contributions share the
    market path from the later contribution date onwards
  * W's first two moments are computed exactly, then W is matched to a
    lognormal and P(W >= target) read off that
The approximation is in the final matching step, and it is documented in
docs/E-INVESTMENT-DECISION-ENGINE.md alongside its known limitations.
"""

import math
from dataclasses import dataclass, field
from decimal import Decimal

from .errors import InsufficientData
from .money import money, rupees

GOAL_TYPES = (
    "RETIREMENT", "HOME", "CHILD_EDUCATION", "MARRIAGE", "CAR", "TRAVEL",
    "EMERGENCY_FUND", "WEALTH_CREATION", "FINANCIAL_INDEPENDENCE", "OTHER",
)

# Goals whose shortfall is not survivable get a higher required confidence.
NON_NEGOTIABLE = ("RETIREMENT", "CHILD_EDUCATION", "EMERGENCY_FUND")


def norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


@dataclass
class Goal:
    goal_id: str
    name: str
    goal_type: str
    target_amount_today: Decimal          # in today's rupees
    years_to_goal: float
    current_corpus: Decimal = Decimal("0")
    monthly_contribution: Decimal = Decimal("0")
    annual_step_up_pct: float = 0.0
    inflation_pct: float = None           # None => use the scenario's inflation
    priority: str = "MEDIUM"              # HIGH | MEDIUM | LOW
    flexible_amount: bool = False
    flexible_date: bool = False
    notes: str = ""

    def __post_init__(self):
        if self.goal_type not in GOAL_TYPES:
            raise ValueError(f"unknown goal_type {self.goal_type!r}")
        self.target_amount_today = money(self.target_amount_today)
        self.current_corpus = money(self.current_corpus)
        self.monthly_contribution = money(self.monthly_contribution)

    @property
    def is_non_negotiable(self):
        return self.goal_type in NON_NEGOTIABLE


def inflation_adjusted_target(goal, inflation_pct):
    """What the goal will cost in the rupees of the year it falls due."""
    infl = goal.inflation_pct if goal.inflation_pct is not None else inflation_pct
    return rupees(
        Decimal(str(float(goal.target_amount_today) * (1 + infl / 100.0) ** goal.years_to_goal))
    )


def contribution_schedule(goal, months=None):
    """Monthly contributions as (years_remaining_at_contribution, amount).

    Step-ups are applied on each completed year, which is how an income-linked
    step-up actually behaves.
    """
    total_months = months if months is not None else int(round(goal.years_to_goal * 12))
    if total_months <= 0:
        return []
    out = []
    amount = float(goal.monthly_contribution)
    for m in range(total_months):
        if m and m % 12 == 0:
            amount *= (1 + goal.annual_step_up_pct / 100.0)
        years_invested = (total_months - m) / 12.0
        out.append((years_invested, amount))
    return out


def _log_params(mu, sigma):
    """Simple-return moments -> log-return moments."""
    if mu <= -1:
        raise ValueError("mean return below -100% is not a valid assumption")
    var_log = math.log(1.0 + (sigma ** 2) / ((1.0 + mu) ** 2))
    m = math.log(1.0 + mu) - 0.5 * var_log
    return m, var_log


def terminal_wealth_moments(flows, mu, sigma):
    """Exact first two moments of terminal wealth.

    flows: list of (years_invested, amount). Amount may be a lump sum with
    years_invested == horizon.
    """
    if not flows:
        return (0.0, 0.0)
    m, s2 = _log_params(mu, sigma)
    m1 = sum(c * math.exp(m * t + 0.5 * s2 * t) for t, c in flows)
    m2 = 0.0
    for t_i, c_i in flows:
        for t_j, c_j in flows:
            cov = s2 * min(t_i, t_j)
            m2 += c_i * c_j * math.exp(
                m * (t_i + t_j) + 0.5 * (s2 * t_i + s2 * t_j) + cov
            )
    return (m1, m2)


def wealth_distribution(flows, mu, sigma):
    """Moment-matched lognormal for terminal wealth."""
    m1, m2 = terminal_wealth_moments(flows, mu, sigma)
    if m1 <= 0:
        return None
    ratio = m2 / (m1 ** 2)
    var_log = math.log(max(ratio, 1.0 + 1e-12))
    return {
        "expected": m1,
        "mu_log": math.log(m1) - 0.5 * var_log,
        "sigma_log": math.sqrt(var_log),
    }


def probability_of_reaching(flows, mu, sigma, target):
    dist = wealth_distribution(flows, mu, sigma)
    if dist is None or target <= 0:
        return None
    if dist["sigma_log"] < 1e-9:
        return 1.0 if dist["expected"] >= target else 0.0
    z = (math.log(target) - dist["mu_log"]) / dist["sigma_log"]
    return 1.0 - norm_cdf(z)


def wealth_percentile(flows, mu, sigma, p):
    """Terminal wealth at percentile p (0-1). Used for the outcome range."""
    dist = wealth_distribution(flows, mu, sigma)
    if dist is None:
        return None
    # inverse normal CDF via bisection: exact enough, and dependency-free
    lo, hi = -8.0, 8.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if norm_cdf(mid) < p:
            lo = mid
        else:
            hi = mid
    z = (lo + hi) / 2
    return math.exp(dist["mu_log"] + dist["sigma_log"] * z)


def required_return(goal, inflation_pct):
    """The annual return the plan needs, given what is actually being saved.

    This is the number that turns 'how much risk do you want?' into
    'how much risk does your plan require?' — the risk-requirement dimension.
    """
    target = float(inflation_adjusted_target(goal, inflation_pct))
    flows = contribution_schedule(goal)
    if float(goal.current_corpus):
        flows = flows + [(goal.years_to_goal, float(goal.current_corpus))]
    if not flows or target <= 0:
        return None

    def fv(rate):
        return sum(c * (1 + rate) ** t for t, c in flows)

    if fv(0.0) >= target:
        return 0.0
    lo, hi = 0.0, 1.0
    if fv(hi) < target:
        return None                      # not reachable at any sane return
    for _ in range(200):
        mid = (lo + hi) / 2
        if fv(mid) < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def required_monthly_contribution(goal, expected_return, inflation_pct):
    """Monthly SIP needed to reach the goal at a given expected return."""
    target = float(inflation_adjusted_target(goal, inflation_pct))
    months = int(round(goal.years_to_goal * 12))
    if months <= 0:
        return None
    lump_fv = float(goal.current_corpus) * (1 + expected_return) ** goal.years_to_goal
    remaining = target - lump_fv
    if remaining <= 0:
        return money(0)
    probe = Goal(
        goal_id=goal.goal_id, name=goal.name, goal_type=goal.goal_type,
        target_amount_today=goal.target_amount_today, years_to_goal=goal.years_to_goal,
        monthly_contribution=Decimal("1"), annual_step_up_pct=goal.annual_step_up_pct,
    )
    unit_fv = sum(c * (1 + expected_return) ** t for t, c in contribution_schedule(probe))
    if unit_fv <= 0:
        return None
    return rupees(Decimal(str(remaining / unit_fv)))


@dataclass
class GoalProjection:
    """Everything the goal screen shows, computed once, auditable."""

    goal_id: str
    scenario: str
    inflation_pct: float
    expected_return_pct: float
    volatility_pct: float
    inflation_adjusted_target: Decimal
    expected_corpus: Decimal
    corpus_p10: Decimal
    corpus_p50: Decimal
    corpus_p90: Decimal
    probability: float
    shortfall: Decimal
    surplus: Decimal
    required_return_pct: float
    required_monthly_contribution: Decimal
    assumptions: dict = field(default_factory=dict)

    def as_dict(self):
        return {
            "goal_id": self.goal_id,
            "scenario": self.scenario,
            "inflation_pct": round(self.inflation_pct, 2),
            "expected_return_pct": round(self.expected_return_pct, 2),
            "volatility_pct": round(self.volatility_pct, 2),
            "inflation_adjusted_target": str(self.inflation_adjusted_target),
            "expected_corpus": str(self.expected_corpus),
            "range_p10_p90": [str(self.corpus_p10), str(self.corpus_p90)],
            "median_corpus": str(self.corpus_p50),
            "probability_pct": round(self.probability * 100, 1),
            "shortfall": str(self.shortfall),
            "surplus": str(self.surplus),
            "required_return_pct": (
                round(self.required_return_pct, 2) if self.required_return_pct is not None else None
            ),
            "required_monthly_contribution": str(self.required_monthly_contribution),
            "assumptions": self.assumptions,
            "disclaimer_ids": ["DISC-PROJECTION-001", "DISC-PAST-PERF-001"],
        }


def project_goal(goal, expected_return_pct, volatility_pct, inflation_pct, scenario="base",
                 assumptions=None):
    """Project one goal under one set of assumptions."""
    if goal.years_to_goal <= 0:
        raise InsufficientData(
            "Goal horizon must be positive to project it.",
            detail={"goal_id": goal.goal_id},
        )
    mu = expected_return_pct / 100.0
    sigma = volatility_pct / 100.0
    target = float(inflation_adjusted_target(goal, inflation_pct))

    flows = contribution_schedule(goal)
    if float(goal.current_corpus):
        flows = flows + [(goal.years_to_goal, float(goal.current_corpus))]

    dist = wealth_distribution(flows, mu, sigma)
    prob = probability_of_reaching(flows, mu, sigma, target)
    expected = dist["expected"] if dist else 0.0
    p10 = wealth_percentile(flows, mu, sigma, 0.10) or 0.0
    p50 = wealth_percentile(flows, mu, sigma, 0.50) or 0.0
    p90 = wealth_percentile(flows, mu, sigma, 0.90) or 0.0

    gap = target - p50
    return GoalProjection(
        goal_id=goal.goal_id,
        scenario=scenario,
        inflation_pct=inflation_pct,
        expected_return_pct=expected_return_pct,
        volatility_pct=volatility_pct,
        inflation_adjusted_target=rupees(target),
        expected_corpus=rupees(expected),
        corpus_p10=rupees(p10),
        corpus_p50=rupees(p50),
        corpus_p90=rupees(p90),
        probability=prob if prob is not None else 0.0,
        shortfall=rupees(max(0.0, gap)),
        surplus=rupees(max(0.0, -gap)),
        required_return_pct=(
            (required_return(goal, inflation_pct) or 0.0) * 100
            if required_return(goal, inflation_pct) is not None else None
        ),
        required_monthly_contribution=(
            required_monthly_contribution(goal, mu, inflation_pct) or money(0)
        ),
        assumptions=assumptions or {},
    )


def probability_band(probability):
    """Words for a number, because 84% means nothing on its own."""
    if probability is None:
        return "UNKNOWN"
    p = probability * 100
    if p >= 85:
        return "ON_TRACK"
    if p >= 70:
        return "LIKELY"
    if p >= 50:
        return "AT_RISK"
    if p >= 30:
        return "OFF_TRACK"
    return "UNLIKELY"
