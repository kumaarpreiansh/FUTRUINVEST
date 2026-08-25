"""Alert Engine and frequency control.

The default answer to "should we notify?" is no.

Every alert has to earn its place three times: it must be material, it must
survive de-duplication against what was already said, and it must fit inside
the investor's notification budget. A platform that notifies constantly trains
its users to ignore it, which means the one alert that mattered gets ignored
too.

Every alert must also be complete. The required fields in the rule-set are not
a template — an alert missing any of them is not sent, because "your fund needs
review" without what changed, what it means, and what happens if you do nothing
is just anxiety with a logo on it.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from .errors import ComplianceBlock

SEVERITIES = ("RED", "ORANGE", "YELLOW", "BLUE", "GREEN", "EDUCATION")
SEVERITY_ORDER = {s: i for i, s in enumerate(SEVERITIES)}


@dataclass
class Alert:
    alert_id: str
    investor_id: str
    severity: str
    title: str
    what_happened: str
    why_it_matters: str
    confidence: float
    recommended_action: str
    urgency: str
    tax_implication: str
    if_you_do_nothing: str
    sources: list = field(default_factory=list)
    scheme_code: str = None
    goal_id: str = None
    signal_state: str = None
    created_at: datetime = None
    disclaimer_ids: list = field(default_factory=list)

    def __post_init__(self):
        self.created_at = self.created_at or datetime.utcnow()

    def as_dict(self):
        return {
            "alert_id": self.alert_id,
            "severity": self.severity,
            "title": self.title,
            "what_happened": self.what_happened,
            "why_it_matters": self.why_it_matters,
            "confidence_pct": round(self.confidence * 100),
            "recommended_action": self.recommended_action,
            "urgency": self.urgency,
            "tax_implication": self.tax_implication,
            "if_you_do_nothing": self.if_you_do_nothing,
            "sources": self.sources,
            "scheme_code": self.scheme_code,
            "goal_id": self.goal_id,
            "signal_state": self.signal_state,
            "created_at": self.created_at.isoformat(),
            "disclaimer_ids": self.disclaimer_ids,
        }

    def dedupe_key(self):
        return f"{self.investor_id}|{self.severity}|{self.scheme_code}|{self.signal_state}|{self.title}"


def validate(alert, registry):
    """Reject incomplete alerts before they reach a queue."""
    required = registry.alerts["required_fields"]
    missing = [f for f in required if not getattr(alert, f, None) and getattr(alert, f, None) != 0]
    if missing:
        raise ComplianceBlock(
            "Alert is incomplete and will not be sent.",
            detail={"missing_fields": missing, "alert_id": alert.alert_id},
        )
    if alert.severity not in SEVERITIES:
        raise ComplianceBlock(f"Unknown alert severity {alert.severity!r}")
    return True


class AlertDispatcher:
    """Applies materiality, de-duplication, budget and quiet hours.

    Deliberately stateful and inspectable: `self.sent` is the record that
    proves the budget was honoured, and it is what an audit reads.
    """

    def __init__(self, registry, sent_log=None):
        self.registry = registry
        self.policy = registry.alerts
        self.sent = list(sent_log or [])

    # ── checks ───────────────────────────────────────────────────────────────
    def _recent(self, days, investor_id):
        cutoff = datetime.utcnow() - timedelta(days=days)
        return [a for a in self.sent
                if a["investor_id"] == investor_id and a["created_at"] >= cutoff]

    def _duplicate(self, alert):
        window = self.policy["budget"]["suppress_duplicate_within_days"]
        key = alert.dedupe_key()
        return any(a["dedupe_key"] == key for a in self._recent(window, alert.investor_id))

    def _same_holding_recently(self, alert):
        if not alert.scheme_code:
            return False
        window = self.policy["budget"]["suppress_same_holding_within_days"]
        return any(a["scheme_code"] == alert.scheme_code
                   for a in self._recent(window, alert.investor_id))

    def _over_budget(self, alert):
        budget = self.policy["budget"]
        if alert.severity == "RED":
            return None                      # RED is exempt from the global budget
        weekly = len(self._recent(7, alert.investor_id))
        monthly = len(self._recent(30, alert.investor_id))
        if weekly >= budget["max_notifications_per_week"]:
            return f"weekly budget of {budget['max_notifications_per_week']} already used"
        if monthly >= budget["max_notifications_per_month"]:
            return f"monthly budget of {budget['max_notifications_per_month']} already used"
        per_sev = self.policy["severities"][alert.severity].get("max_per_investor_per_month")
        if per_sev is not None:
            same = [a for a in self._recent(30, alert.investor_id)
                    if a["severity"] == alert.severity]
            if len(same) >= per_sev:
                return f"monthly {alert.severity} limit of {per_sev} already used"
        return None

    def _in_quiet_hours(self, alert, local_time=None):
        quiet = self.policy["budget"]["quiet_hours_local"]
        if alert.severity in self.policy["budget"]["quiet_hours_exempt"]:
            return False
        now = local_time or datetime.now().time()
        start = datetime.strptime(quiet["start"], "%H:%M").time()
        end = datetime.strptime(quiet["end"], "%H:%M").time()
        return now >= start or now <= end

    # ── dispatch ─────────────────────────────────────────────────────────────
    def dispatch(self, alert, local_time=None):
        """Decide whether this alert is delivered, deferred, or dropped."""
        validate(alert, self.registry)

        if self._duplicate(alert):
            return self._outcome(alert, "SUPPRESSED", "We already told you this recently.")
        if alert.severity not in ("RED", "ORANGE") and self._same_holding_recently(alert):
            return self._outcome(alert, "SUPPRESSED",
                                 "Another alert about this holding was sent recently.")
        over = self._over_budget(alert)
        if over:
            return self._outcome(alert, "DEFERRED_TO_DIGEST",
                                 f"Not urgent enough to break the notification budget "
                                 f"({over}). It will appear in your next digest.")

        cadence = self.policy["severities"][alert.severity]["cadence"]
        channels = list(self.policy["severities"][alert.severity]["channel"])
        if cadence != "IMMEDIATE":
            return self._outcome(alert, "QUEUED_" + cadence,
                                 f"Queued for the {cadence.replace('_', ' ').lower()}.",
                                 channels=channels)
        if self._in_quiet_hours(alert, local_time):
            return self._outcome(alert, "DEFERRED_QUIET_HOURS",
                                 "Held until morning — nothing here needs waking you up.",
                                 channels=channels)

        return self._outcome(alert, "SENT", "Delivered.", channels=channels)

    def _outcome(self, alert, status, why, channels=None):
        # Anything that will reach the investor — now or in a digest — counts
        # against de-duplication and against the budget. Only suppressed and
        # dropped alerts leave no trace, because the investor never sees them.
        if status != "SUPPRESSED":
            self.sent.append({
                "investor_id": alert.investor_id, "severity": alert.severity,
                "scheme_code": alert.scheme_code, "created_at": alert.created_at,
                "dedupe_key": alert.dedupe_key(), "alert_id": alert.alert_id,
                "status": status,
            })
        return {
            "alert_id": alert.alert_id,
            "status": status,
            "why": why,
            "channels": channels or [],
            "alert": alert.as_dict(),
        }


# ── alert construction from a signal ─────────────────────────────────────────

SEVERITY_FOR_SIGNAL = {
    "HOLD": "GREEN",
    "WATCH": "BLUE",
    "REVIEW": "ORANGE",
    "REBALANCE": "YELLOW",
    "SWITCH_REVIEW": "ORANGE",
    "REDUCE": "ORANGE",
    "EXIT_REVIEW": "RED",
}


def from_signal(signal, *, investor_id, scheme_name, tax_implication="None — nothing is being sold.",
                confidence=0.7, sources=None, do_nothing=None):
    """Turn a signal into a complete, sendable alert — or nothing at all.

    HOLD produces no alert. "Nothing has changed" is not news, and sending it
    weekly is how an alert channel dies.
    """
    if signal.state == "HOLD":
        return None

    severity = SEVERITY_FOR_SIGNAL[signal.state]
    what = "; ".join(t["description"] for t in signal.fired[:3]) or "Something changed."
    urgency = {"RED": "Review within a week", "ORANGE": "Review this month",
               "YELLOW": "No rush", "BLUE": "For information", "GREEN": "None"}[severity]

    if do_nothing is None:
        do_nothing = {
            "EXIT_REVIEW": ("The holding stays as it is. If the reason was a liquidity need or "
                            "a met goal, the risk of doing nothing is that the money is exposed "
                            "when you need it."),
            "SWITCH_REVIEW": ("Nothing is sold. You continue to hold a scheme our evidence says "
                              "has weakened — which may still be the right call once costs are "
                              "counted."),
            "REDUCE": "Your exposure stays above the level your plan calls for.",
            "REBALANCE": ("Your allocation stays off target. Over time that changes the risk you "
                          "are taking without you having decided to."),
            "REVIEW": "Nothing happens. We will keep watching and tell you if it develops.",
            "WATCH": "Nothing happens. This is information, not a call to act.",
        }[signal.state]

    return Alert(
        alert_id=f"ALT-{signal.scheme_code}-{signal.state}-{date.today().isoformat()}",
        investor_id=investor_id,
        severity=severity,
        title=f"{scheme_name}: {signal.state.replace('_', ' ').title()}",
        what_happened=what,
        why_it_matters=(
            f"{len(signal.fired)} independent signal(s) fired on this holding"
            + (" including a material event" if signal.material_events else "")
            + ". This is about the scheme and your plan, not about market movement."
        ),
        confidence=confidence,
        recommended_action=(
            "Open the review — the numbers, the tax and the alternatives are laid out there."
            if signal.is_actionable else "No action needed."
        ),
        urgency=urgency,
        tax_implication=tax_implication,
        if_you_do_nothing=do_nothing,
        sources=sources or ["Scheme disclosures", "Benchmark data", "Your portfolio"],
        scheme_code=signal.scheme_code,
        goal_id=signal.goal_id,
        signal_state=signal.state,
        disclaimer_ids=["DISC-MF-RISK-001", "DISC-PAST-PERF-001"],
    )


def progress_alert(investor_id, goal_name, probability_before, probability_after, driver):
    """The engagement the platform is allowed to have: financial progress."""
    delta = (probability_after - probability_before) * 100
    return Alert(
        alert_id=f"ALT-PROGRESS-{date.today().isoformat()}",
        investor_id=investor_id,
        severity="BLUE" if delta > 0 else "YELLOW",
        title=f"{goal_name}: probability now {probability_after * 100:.0f}%",
        what_happened=f"{driver} moved your goal probability by {delta:+.1f} percentage points.",
        why_it_matters=("This is the number that matters — not what any single fund did "
                        "this month."),
        confidence=0.8,
        recommended_action="Nothing to do. Keep going.",
        urgency="None",
        tax_implication="None.",
        if_you_do_nothing="Your plan continues as it is.",
        sources=["Your contributions", "Published return assumptions"],
        goal_id=None,
        disclaimer_ids=["DISC-PROJECTION-001"],
    )
