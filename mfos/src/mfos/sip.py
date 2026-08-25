"""SIP Engine.

The SIP is the only lever most investors can actually pull. Fund selection is
a second-order decision; contribution level is first-order. So this engine
exists to make the contribution decision vivid — and to make pausing feel
survivable rather than shameful, because an investor who feels judged for
pausing stops instead.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from .errors import InsufficientData
from .money import money, rupees
from .scenarios import portfolio_assumptions, what_if

SIP_STATES = ("ACTIVE", "PAUSED", "STOPPED", "COMPLETED")
STEP_UP_OPTIONS = (0, 5, 10, 15)


@dataclass
class SIP:
    sip_id: str
    goal_id: str
    scheme_code: str
    amount: Decimal
    day_of_month: int = 5
    annual_step_up_pct: float = 0.0
    state: str = "ACTIVE"
    started_on: date = None
    paused_until: date = None
    consecutive_instalments: int = 0
    missed_instalments: int = 0
    history: list = field(default_factory=list)

    def __post_init__(self):
        self.amount = money(self.amount)
        if self.state not in SIP_STATES:
            raise ValueError(f"unknown SIP state {self.state!r}")

    def _log(self, action, detail):
        self.history.append({
            "on": date.today().isoformat(), "action": action, "detail": detail,
        })

    # ── lifecycle ────────────────────────────────────────────────────────────
    def modify(self, new_amount):
        old = self.amount
        self.amount = money(new_amount)
        self._log("MODIFY", {"from": str(old), "to": str(self.amount)})
        return self

    def pause(self, until=None):
        """Pausing is a feature, not a failure. It is strictly better than
        stopping, and the copy that surfaces it must say so."""
        self.state = "PAUSED"
        self.paused_until = until
        self._log("PAUSE", {"until": until.isoformat() if until else "indefinite"})
        return self

    def restart(self):
        self.state = "ACTIVE"
        self.paused_until = None
        self._log("RESTART", {})
        return self

    def stop(self, reason):
        self.state = "STOPPED"
        self._log("STOP", {"reason": reason})
        return self

    def step_up(self, pct):
        self.annual_step_up_pct = float(pct)
        self._log("STEP_UP", {"pct": pct})
        return self

    @property
    def annual_amount(self):
        return money(self.amount * 12)


def link_to_income(sip, monthly_income, target_savings_rate_pct):
    """Income-linked SIP: express the SIP as a share of income, so it grows
    with the investor rather than being re-decided every year."""
    if not monthly_income:
        raise InsufficientData("Monthly income is needed to size an income-linked SIP.")
    return money(Decimal(str(float(monthly_income) * target_savings_rate_pct / 100.0)))


def step_up_comparison(goal, allocation, registry, options=STEP_UP_OPTIONS):
    """The 5% / 10% / 15% step-up table, priced in goal probability.

    This is the single highest-leverage screen in the product: a 10% annual
    step-up usually moves goal probability more than any fund switch ever will.
    """
    rows = []
    base = None
    for pct in options:
        result = what_if(goal, allocation, registry, step_up_pct=pct)
        after = result["after"]
        if base is None:
            base = after["probability_pct"]
        rows.append({
            "step_up_pct": pct,
            "probability_pct": after["probability_pct"],
            "probability_gain_pp": round(after["probability_pct"] - base, 1),
            "median_corpus": after["median_corpus"],
            "first_year_monthly": str(goal.monthly_contribution),
            "final_year_monthly": str(rupees(
                float(goal.monthly_contribution) * (1 + pct / 100.0) ** max(0, goal.years_to_goal - 1)
            )),
            "disclaimer_ids": ["DISC-PROJECTION-001"],
        })
    return rows


def contribution_impact(goal, allocation, registry, increments=(2500, 5000, 10000)):
    """'What does another ₹5,000 a month buy me?' — answered in probability."""
    rows = []
    for inc in increments:
        result = what_if(
            goal, allocation, registry,
            monthly_contribution=float(goal.monthly_contribution) + inc,
        )
        rows.append({
            "extra_monthly": str(money(inc)),
            "probability_delta_pp": result["probability_delta_pp"],
            "corpus_delta": result["corpus_delta"],
        })
    return rows


def required_sip_for_target_probability(goal, allocation, registry, target_probability=0.80,
                                        max_monthly=None):
    """Invert the question: what contribution reaches the target probability?

    Returns None when no contribution inside the investor's means reaches it —
    which is itself the answer, and must be surfaced as 'the goal or the date
    needs to change', not buried.
    """
    ceiling = float(max_monthly) if max_monthly else float(goal.monthly_contribution) * 10 + 100000
    lo, hi = 0.0, ceiling
    best = None
    for _ in range(60):
        mid = (lo + hi) / 2
        prob = what_if(goal, allocation, registry, monthly_contribution=mid)["after"]["probability_pct"] / 100.0
        if prob >= target_probability:
            best = mid
            hi = mid
        else:
            lo = mid
    if best is None:
        return {
            "reachable": False,
            "message": (
                "No monthly contribution within the assumed ceiling reaches "
                f"{int(target_probability * 100)}% for this goal. The goal amount, "
                "the date or the required return needs to change — adding risk is "
                "not a substitute for the gap."
            ),
            "ceiling_tested": str(rupees(ceiling)),
        }
    return {
        "reachable": True,
        "required_monthly": str(rupees(best)),
        "current_monthly": str(goal.monthly_contribution),
        "increase_needed": str(rupees(max(0.0, best - float(goal.monthly_contribution)))),
        "target_probability_pct": round(target_probability * 100, 1),
    }


def discipline_metrics(sips, months_observed):
    """Inputs to the Investor Discipline Score.

    Note what is measured: consistency and step-ups. Not transactions, not
    switches, not returns. Rewarding activity would make the platform worse.
    """
    if not sips or months_observed <= 0:
        return None
    expected = len(sips) * months_observed
    missed = sum(s.missed_instalments for s in sips)
    stepped = sum(1 for s in sips if s.annual_step_up_pct > 0)
    paused = sum(1 for s in sips if s.state == "PAUSED")
    stopped = sum(1 for s in sips if s.state == "STOPPED")
    return {
        "sip_consistency_pct": round(max(0.0, (expected - missed) / expected) * 100, 1),
        "step_up_adoption_pct": round(stepped / len(sips) * 100, 1),
        "active_sips": sum(1 for s in sips if s.state == "ACTIVE"),
        "paused_sips": paused,
        "stopped_sips": stopped,
        "monthly_commitment": str(money(sum(float(s.amount) for s in sips if s.state == "ACTIVE"))),
    }
