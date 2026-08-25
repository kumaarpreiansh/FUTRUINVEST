"""Versioned rule-set loading.

Every regulatory fact, tax rate, threshold, weight and disclosure the platform
uses comes from YAML under mfos/config. Code holds method; configuration holds
policy and law. That split is what makes a rule change a config change with a
diff, a reviewer and a version — instead of a code release nobody can audit.

Every object loaded here knows its own version, and every recommendation stores
the versions it was computed under, so any past decision can be replayed
against the rules that were live at the time.
"""

import os
from datetime import date, datetime

import yaml

from .errors import ConfigError, RuleNotVerified

CONFIG_ROOT = os.environ.get(
    "MFOS_CONFIG_ROOT",
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "config"),
)

USABLE_VERIFICATION = ("VERIFIED_PRIMARY", "VERIFIED_SECONDARY", "COMMITTEE_ASSUMPTION")


def _parse_date(value):
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value), "%Y-%m-%d").date()


class RuleSet:
    """One YAML rule-set, with provenance and effective-date filtering."""

    def __init__(self, path, raw):
        self.path = path
        self.raw = raw
        meta = raw.get("ruleset") or {}
        self.id = meta.get("id") or os.path.basename(path)
        self.version = meta.get("version", "0.0.0")
        self.owner = meta.get("owner")
        self.published_on = _parse_date(meta.get("published_on"))
        self._by_id = {}
        for rule in raw.get("rules") or []:
            rid = rule.get("rule_id") or rule.get("id")
            if rid:
                self._by_id[rid] = rule

    # ── identity ─────────────────────────────────────────────────────────────
    @property
    def stamp(self):
        """What gets written into the audit trail."""
        return {"ruleset_id": self.id, "version": self.version, "path": os.path.basename(self.path)}

    # ── access ───────────────────────────────────────────────────────────────
    def __getitem__(self, key):
        if key in self.raw:
            return self.raw[key]
        raise ConfigError(f"{self.id}: no section '{key}'")

    def get(self, key, default=None):
        return self.raw.get(key, default)

    def rule(self, rule_id):
        try:
            return self._by_id[rule_id]
        except KeyError:
            raise ConfigError(f"{self.id}: unknown rule '{rule_id}'")

    def rules(self, **filters):
        """Rules matching every filter, as a list (order preserved)."""
        out = []
        for rule in self._by_id.values():
            if all(rule.get(k) == v for k, v in filters.items()):
                out.append(rule)
        return out

    def effective_rules(self, on=None):
        """Rules in force on a date. An expired rule is not law."""
        on = on or date.today()
        live = []
        for rule in self._by_id.values():
            start = _parse_date(rule.get("effective_from"))
            end = _parse_date(rule.get("effective_to"))
            if start and start > on:
                continue
            if end and end < on:
                continue
            live.append(rule)
        return live

    # ── verification ─────────────────────────────────────────────────────────
    def require_verified(self, rule_id):
        """Return a rule only if it may be relied upon; else refuse.

        This is the single choke point that stops an unverified placeholder
        from silently becoming a number on an investor's screen.
        """
        rule = self.rule(rule_id)
        status = rule.get("verification_status", "UNVERIFIED")
        if status not in USABLE_VERIFICATION:
            raise RuleNotVerified(
                f"Rule {rule_id} is {status}; it cannot be used to compute a figure.",
                detail={"rule_id": rule_id, "status": status, "ruleset": self.id},
                rule_ids=[rule_id],
            )
        return rule

    def unverified(self):
        return [
            r for r in self._by_id.values()
            if r.get("verification_status", "UNVERIFIED") not in USABLE_VERIFICATION
        ]

    def legal_review_queue(self):
        return [r for r in self._by_id.values() if r.get("legal_review_required")]


def load_ruleset(path_or_relative, config_root=None):
    """Resolve a rule-set path.

    Tried in order: as given, relative to the config root's parent (which is
    how platform.yaml expresses them), and finally by basename inside the
    config root. Being forgiving here means a rule-set is never silently
    missing — it either loads or raises with the path it looked for.
    """
    root = config_root or CONFIG_ROOT
    candidates = [path_or_relative]
    if not os.path.isabs(path_or_relative):
        candidates.append(os.path.join(os.path.dirname(root), path_or_relative))
        candidates.append(os.path.join(root, os.path.basename(path_or_relative)))
    path = next((c for c in candidates if os.path.exists(c)), None)
    if path is None:
        raise ConfigError(
            f"rule-set not found: {path_or_relative} (looked in {candidates})"
        )
    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    return RuleSet(path, raw)


class Registry:
    """All live rule-sets plus the platform switches, loaded once.

    Held read-only by every engine. Engines never reach past this to a literal.
    """

    def __init__(self, config_root=None):
        self.config_root = os.path.abspath(config_root or CONFIG_ROOT)
        platform_path = os.path.join(self.config_root, "platform.yaml")
        if not os.path.exists(platform_path):
            raise ConfigError(f"platform.yaml not found under {self.config_root}")
        with open(platform_path, encoding="utf-8") as fh:
            self.platform = yaml.safe_load(fh)

        paths = self.platform["rulesets"]
        self.tax = load_ruleset(paths["tax"], self.config_root)
        self.compliance = load_ruleset(paths["compliance"], self.config_root)
        self.scoring = load_ruleset(paths["fund_scoring"], self.config_root)
        self.allocation = load_ruleset(paths["allocation"], self.config_root)
        self.signals = load_ruleset(paths["signals"], self.config_root)
        self.alerts = load_ruleset(paths["alerts"], self.config_root)
        self.ai_models = load_ruleset(paths["ai_models"], self.config_root)
        self._validate()

    # ── switches ─────────────────────────────────────────────────────────────
    def gate(self, name):
        gates = self.platform.get("gates", {})
        if name not in gates:
            raise ConfigError(f"unknown gate '{name}'")
        return bool(gates[name])

    def threshold(self, *path):
        node = self.platform.get("thresholds", {})
        for key in path:
            if not isinstance(node, dict) or key not in node:
                raise ConfigError(f"unknown threshold {'.'.join(map(str, path))}")
            node = node[key]
        return node

    @property
    def is_registered_adviser(self):
        reg = self.platform.get("regulatory", {})
        return bool(reg.get("sebi_ia_registration_verified")) and bool(
            reg.get("sebi_ia_registration_number")
        )

    def version_stamp(self):
        """Goes into every audit record."""
        return {
            "platform_build_stage": self.platform["platform"]["build_stage"],
            "tax": self.tax.stamp,
            "compliance": self.compliance.stamp,
            "scoring": self.scoring.stamp,
            "allocation": self.allocation.stamp,
            "signals": self.signals.stamp,
            "alerts": self.alerts.stamp,
            "ai_models": self.ai_models.stamp,
        }

    # ── self-checks that must hold at start-up ───────────────────────────────
    def _validate(self):
        pillars = self.scoring["pillars"]
        total = sum(p["weight"] for p in pillars.values())
        if total != 100:
            raise ConfigError(f"fund scoring pillar weights sum to {total}, expected 100")
        for name, override in (self.scoring.get("overrides") or {}).items():
            ptotal = sum(override["pillars"].values())
            if ptotal != 100:
                raise ConfigError(f"scoring override '{name}' weights sum to {ptotal}, expected 100")
        for pol in self.allocation["policies"]:
            weight = sum(sum(s.values()) for s in pol["sleeves"].values())
            if weight and round(weight, 6) != 100:
                raise ConfigError(f"allocation policy {pol['id']} sums to {weight}, expected 100")

        # Safety switches that this repository does not permit flipping.
        for locked in ("auto_execution_enabled", "withdrawals_enabled",
                       "commission_influences_ranking", "allow_llm_generated_numbers"):
            if self.platform["gates"].get(locked):
                raise ConfigError(
                    f"gate '{locked}' must be false. Refusing to start."
                )

    # ── sandbox ──────────────────────────────────────────────────────────────
    def enable_sandbox_advisory(self, reason):
        """Pretend, IN MEMORY ONLY, that registration is in place.

        This exists so the full sixteen-stage pipeline can be demonstrated and
        tested. It writes nothing to disk, it refuses outright once build_stage
        is "production", and it does not touch the execution, auto-execution,
        withdrawal or commission gates — those stay closed in every mode.
        """
        if self.platform["platform"]["build_stage"] == "production":
            raise ConfigError(
                "Refusing to fake adviser registration on a production build."
            )
        self.platform["regulatory"] = dict(self.platform["regulatory"])
        self.platform["regulatory"].update({
            "sebi_ia_registration_number": "SANDBOX-NOT-A-REAL-REGISTRATION",
            "sebi_ia_registration_verified": True,
        })
        self.platform["gates"] = dict(self.platform["gates"])
        self.platform["gates"]["personalised_advice_enabled"] = True
        self.sandbox_reason = reason
        return self

    @property
    def is_sandbox(self):
        return bool(getattr(self, "sandbox_reason", None))

    # ── review surface ───────────────────────────────────────────────────────
    def open_legal_items(self):
        """Everything a lawyer still has to sign off, across all rule-sets."""
        items = []
        for rs in (self.tax, self.compliance):
            for rule in rs.legal_review_queue():
                items.append({
                    "ruleset": rs.id,
                    "rule_id": rule.get("rule_id"),
                    "title": rule.get("title"),
                    "verification_status": rule.get("verification_status", "UNVERIFIED"),
                    "compliance_status": rule.get("compliance_status"),
                })
        return items
