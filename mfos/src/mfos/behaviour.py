"""Behavioural Guardrails.

The largest, most reliable destroyer of Indian retail returns is not fund
selection. It is the investor selling a good plan during a bad quarter, and
buying whatever did well last year. So these guardrails are not a soft feature
bolted on the side — they sit between every market event and every message the
platform is allowed to send.

The pattern is always the same: when the investor is under emotional pressure,
the platform slows down, asks what has actually changed, and refuses to supply
the certainty they are looking for.

Each guardrail returns an intervention: what to show, what NOT to show, and
what the investor must be asked before any action is processed.
"""

from dataclasses import dataclass, field

GUARDRAILS = (
    "PANIC", "FOMO", "OVERTRADING", "RECENCY", "LOSS_AVERSION", "PERFORMANCE_CHASING",
)


@dataclass
class Intervention:
    guardrail: str
    triggered: bool
    severity: str = "LOW"          # LOW | MEDIUM | HIGH
    message: str = ""
    questions: list = field(default_factory=list)
    show: list = field(default_factory=list)
    suppress: list = field(default_factory=list)
    requires_acknowledgement: bool = False
    cooling_off_hours: int = 0

    def as_dict(self):
        return {
            "guardrail": self.guardrail,
            "triggered": self.triggered,
            "severity": self.severity,
            "message": self.message,
            "ask_before_proceeding": self.questions,
            "show": self.show,
            "suppress": self.suppress,
            "requires_acknowledgement": self.requires_acknowledgement,
            "cooling_off_hours": self.cooling_off_hours,
        }


# The five questions that separate a reason from a reaction. Asked before any
# redemption or stop-SIP request during a drawdown.
REALITY_CHECK = [
    "Has your goal changed?",
    "Has your income or job situation changed?",
    "Has your capacity to take risk changed — a new dependant, a new loan, a lost buffer?",
    "Has anything changed about the scheme itself, rather than about the market?",
    "Has your target allocation moved outside its band?",
]


def panic_protection(ctx):
    """Investor is trying to sell into a fall."""
    drawdown = ctx.get("portfolio_drawdown_pct", 0) or 0
    intent = ctx.get("intended_action")
    if not (drawdown >= 10 and intent in ("REDEEM", "STOP_SIP", "SWITCH_TO_DEBT")):
        return Intervention("PANIC", False)

    changed = ctx.get("fundamentals_changed", False)
    return Intervention(
        guardrail="PANIC",
        triggered=True,
        severity="HIGH" if drawdown >= 20 else "MEDIUM",
        message=(
            f"Your portfolio is down {drawdown:.0f}% and you are about to "
            f"{intent.replace('_', ' ').lower()}. Before that goes through: a fall in prices is "
            "not, by itself, new information about your plan. Selling converts a paper fall "
            "into a permanent one, and locks in the loss at the worst available price."
            if not changed else
            "Something has genuinely changed here, so this may well be the right decision. "
            "Let's confirm what changed before acting."
        ),
        questions=REALITY_CHECK,
        show=[
            "goal probability before and after this action",
            "what the same portfolio did after previous falls of this size",
            "the tax and exit load this action would crystallise",
        ],
        suppress=["any performance leaderboard", "any 'top performing funds' list"],
        requires_acknowledgement=True,
        cooling_off_hours=24 if drawdown >= 20 else 0,
    )


def fomo_protection(ctx):
    """Investor is chasing something that has just run hard."""
    recent = ctx.get("candidate_trailing_1y_return_pct")
    category_median = ctx.get("category_median_1y_return_pct")
    intent = ctx.get("intended_action")
    if recent is None or category_median is None or intent not in ("BUY", "SWITCH_IN", "LUMPSUM"):
        return Intervention("FOMO", False)
    if recent < category_median * 1.5 or recent < 30:
        return Intervention("FOMO", False)

    return Intervention(
        guardrail="FOMO",
        triggered=True,
        severity="MEDIUM",
        message=(
            f"This scheme is up {recent:.0f}% over the last year against a category median of "
            f"{category_median:.0f}%. Buying after a run like that is the most common way "
            "investors turn a good fund into a bad experience — you are buying the segment at "
            "its most expensive, and the next few years usually look nothing like the last one."
        ),
        questions=[
            "Does this scheme fit the target allocation for a goal you already have?",
            "Would you still want it if last year's return were removed from the page?",
        ],
        show=["rolling returns over every 3-year window, not the last 12 months",
              "what this segment did in the 3 years after its last comparable run"],
        suppress=["1-year return as the headline figure"],
        requires_acknowledgement=True,
    )


def overtrading_protection(ctx):
    """Too many changes, too close together."""
    actions_12m = ctx.get("portfolio_actions_last_12m", 0)
    on_holding = ctx.get("months_since_last_action_on_holding")
    if actions_12m < 4 and (on_holding is None or on_holding >= 12):
        return Intervention("OVERTRADING", False)

    return Intervention(
        guardrail="OVERTRADING",
        triggered=True,
        severity="MEDIUM",
        message=(
            f"You have made {actions_12m} changes in the last twelve months"
            + (f", and this holding was last changed {on_holding} months ago"
               if on_holding is not None else "")
            + ". Each change costs tax, exit load and time out of the market, and the evidence "
              "that frequent switching improves outcomes is weak to non-existent."
        ),
        questions=["What has changed since the last time this was reviewed?"],
        show=["the cumulative cost of the last twelve months of changes"],
        suppress=[],
        requires_acknowledgement=True,
        cooling_off_hours=48,
    )


def recency_protection(ctx):
    """Judging a decade-long decision on a quarter of data."""
    horizon = ctx.get("evaluation_window_months")
    goal_years = ctx.get("years_to_goal")
    if horizon is None or horizon > 12 or (goal_years or 0) < 5:
        return Intervention("RECENCY", False)
    return Intervention(
        guardrail="RECENCY",
        triggered=True,
        severity="LOW",
        message=(
            f"You are judging a {goal_years:.0f}-year decision on {horizon} months of data. "
            "Over that window, luck dominates skill for every equity scheme in existence."
        ),
        questions=[],
        show=["rolling 3-year and 5-year windows instead of the trailing period"],
        suppress=["1-month, 3-month and 6-month return figures for long-horizon goals"],
    )


def loss_aversion_protection(ctx):
    """Holding a loser to avoid booking the loss, or selling a winner to book a gain."""
    holding_loss_pct = ctx.get("holding_unrealised_return_pct")
    intent = ctx.get("intended_action")
    if holding_loss_pct is None:
        return Intervention("LOSS_AVERSION", False)

    if holding_loss_pct < -15 and intent == "HOLD_DESPITE_SIGNAL":
        return Intervention(
            "LOSS_AVERSION", True, "MEDIUM",
            message=(
                "You are holding this because it is down, not because it is good. The purchase "
                "price is information about the past, not about the scheme. The right question "
                "is whether you would buy it today."
            ),
            questions=["Knowing what you know now, would you buy this scheme today?"],
            show=["the scheme's current evidence, with your purchase price hidden"],
        )
    if holding_loss_pct > 40 and intent in ("REDEEM", "SWITCH_OUT"):
        return Intervention(
            "LOSS_AVERSION", True, "LOW",
            message=(
                "Booking a gain feels like winning, but it is not a reason to sell. Nothing "
                "about the scheme changed because it went up."
            ),
            questions=["Has the reason you bought this changed?"],
            show=["tax that would be crystallised", "goal probability before and after"],
        )
    return Intervention("LOSS_AVERSION", False)


def performance_chasing_protection(ctx):
    """Wanting to switch into whatever topped the table."""
    if ctx.get("intended_action") != "SWITCH" :
        return Intervention("PERFORMANCE_CHASING", False)
    reason = (ctx.get("stated_reason") or "").upper()
    triggers = ("BETTER RETURN", "TOP PERFORMER", "HIGHER RETURN", "BEST FUND", "RANKING")
    if not any(t in reason for t in triggers):
        return Intervention("PERFORMANCE_CHASING", False)
    return Intervention(
        "PERFORMANCE_CHASING", True, "MEDIUM",
        message=(
            "Last year's ranking is the weakest available predictor of next year's ranking. "
            "Funds at the top of a one-year table are usually there because their style was in "
            "favour — and styles rotate. Let's look at whether this switch survives its own costs."
        ),
        questions=["Is the current scheme failing on evidence, or is the other one simply up more?"],
        show=["net-of-tax switching benefit", "rolling win rates for both schemes"],
        suppress=["1-year return leaderboards"],
    )


ALL = (panic_protection, fomo_protection, overtrading_protection,
       recency_protection, loss_aversion_protection, performance_chasing_protection)


def evaluate(ctx):
    """Run every guardrail. Returns the interventions that fired."""
    return [i for i in (fn(ctx) for fn in ALL) if i.triggered]


def gate_action(ctx):
    """Should this action proceed straight through, or slow down first?

    Never blocks. The investor's money is their own and a platform that refuses
    to let someone sell is worse than one that lets them sell badly. It inserts
    friction, information and a question — and records that it did.
    """
    interventions = evaluate(ctx)
    if not interventions:
        return {"proceed": True, "interventions": []}
    worst = max(interventions, key=lambda i: ["LOW", "MEDIUM", "HIGH"].index(i.severity))
    return {
        "proceed": True,
        "friction": True,
        "highest_severity": worst.severity,
        "cooling_off_hours": max(i.cooling_off_hours for i in interventions),
        "must_acknowledge": any(i.requires_acknowledgement for i in interventions),
        "interventions": [i.as_dict() for i in interventions],
        "principle": (
            "The investor always decides. The platform's job is to make sure the decision "
            "is made with the facts in view and the reflex given time to pass."
        ),
    }
