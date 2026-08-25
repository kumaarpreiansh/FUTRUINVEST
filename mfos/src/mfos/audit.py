"""Recommendation Audit Trail.

The requirement is replayability: for any recommendation, at any point in the
future, it must be possible to reconstruct exactly what was known, which rules
were in force, which model produced what, and what the investor then did.

So an audit record pins versions rather than referring to "current" anything:
profile version and fingerprint, rule-set versions, model IDs and versions, the
market and fund data snapshot, the triggers that fired, the confidence and its
inputs, and finally the investor's response and the eventual action.

Written append-only as JSONL. Nothing in the platform edits an audit row; a
correction is a new row that references the old one.
"""

import hashlib
import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime


def _default(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "as_dict"):
        return value.as_dict()
    return str(value)


@dataclass
class AuditRecord:
    recommendation_id: str
    investor_id: str
    created_at: str

    # what was known
    profile_version: int = None
    profile_fingerprint: str = None
    portfolio_snapshot: dict = field(default_factory=dict)
    market_data_snapshot: dict = field(default_factory=dict)
    fund_data_snapshot: dict = field(default_factory=dict)

    # under which rules
    ruleset_versions: dict = field(default_factory=dict)
    regulatory_rules_used: list = field(default_factory=list)
    tax_rules_used: list = field(default_factory=list)

    # by which models
    models_used: list = field(default_factory=list)

    # what the engine concluded
    stage_results: dict = field(default_factory=dict)
    triggers_fired: list = field(default_factory=list)
    signals: list = field(default_factory=list)
    recommendation: dict = field(default_factory=dict)
    confidence: dict = field(default_factory=dict)
    explanation: str = ""
    disclosures_shown: list = field(default_factory=list)
    conflicts_disclosed: list = field(default_factory=list)

    # what happened next
    human_review: dict = field(default_factory=dict)
    investor_response: str = None       # PRESENTED | ACCEPTED | DECLINED | DEFERRED
    investor_responded_at: str = None
    final_action: dict = field(default_factory=dict)
    outcome_review: dict = field(default_factory=dict)
    supersedes: str = None

    def content_hash(self):
        payload = json.dumps(asdict(self), sort_keys=True, default=_default)
        return hashlib.sha256(payload.encode()).hexdigest()

    def as_dict(self):
        d = asdict(self)
        d["content_hash"] = self.content_hash()
        return d


class AuditLog:
    """Append-only JSONL log."""

    def __init__(self, path):
        self.path = path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    def write(self, record):
        line = json.dumps(record.as_dict(), default=_default, sort_keys=True)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        return record.recommendation_id

    def read_all(self):
        if not os.path.exists(self.path):
            return []
        with open(self.path, encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]

    def find(self, recommendation_id):
        """Latest row for an id — including any row that superseded it."""
        rows = [r for r in self.read_all() if r["recommendation_id"] == recommendation_id]
        return rows[-1] if rows else None

    def history(self, investor_id):
        return [r for r in self.read_all() if r["investor_id"] == investor_id]

    def record_response(self, record, response, final_action=None):
        """A response is a NEW row that supersedes, never an edit."""
        import copy
        updated = copy.deepcopy(record)
        updated.investor_response = response
        updated.investor_responded_at = datetime.utcnow().isoformat()
        updated.final_action = final_action or {}
        updated.supersedes = record.content_hash()
        self.write(updated)
        return updated

    def verify_chain(self):
        """Every superseding row must point at a hash that exists in the log."""
        rows = self.read_all()
        hashes = {r["content_hash"] for r in rows}
        broken = [r["recommendation_id"] for r in rows
                  if r.get("supersedes") and r["supersedes"] not in hashes]
        return {"rows": len(rows), "broken_links": broken, "intact": not broken}


def new_record(investor_id, registry, profile=None):
    """Start a record with everything that must be pinned at the outset."""
    return AuditRecord(
        recommendation_id=f"REC-{uuid.uuid4().hex[:12]}",
        investor_id=investor_id,
        created_at=datetime.utcnow().isoformat(),
        profile_version=getattr(profile, "version", None),
        profile_fingerprint=profile.fingerprint() if profile else None,
        ruleset_versions=registry.version_stamp(),
    )


def snapshot_schemes(schemes):
    """The fund data as it was at decision time — not a reference to live data.

    A recommendation defended against today's NAV is not defended at all.
    """
    out = {}
    for scheme in schemes:
        out[scheme.scheme_code] = {
            "scheme_name": scheme.scheme_name,
            "amc": scheme.amc,
            "plan": scheme.plan,
            "sebi_category": scheme.sebi_category,
            "nav": scheme.nav,
            "nav_date": str(scheme.nav_date),
            "expense_ratio_pct": scheme.expense_ratio_pct,
            "aum_cr": scheme.aum_cr,
            "riskometer": scheme.riskometer,
            "fund_manager": scheme.fund_manager,
            "top10_weight_pct": scheme.top10_weight_pct,
            "provenance": {name: scheme.provenance(name) for name in scheme.fields},
            "disagreements": scheme.disagreements(),
        }
    return out
