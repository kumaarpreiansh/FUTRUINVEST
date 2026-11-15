"""Results history (SQLite). Powers trends, anomaly baselines and schema drift."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY, project TEXT, started_at TEXT, finished_at TEXT,
    status TEXT, score REAL, checks INTEGER, failed INTEGER, warned INTEGER, errored INTEGER
);
CREATE TABLE IF NOT EXISTS results (
    run_id TEXT, check_id TEXT, asset TEXT, type TEXT, dimension TEXT, severity TEXT,
    status TEXT, score REAL, observed TEXT, expected TEXT, failed_rows INTEGER,
    total_rows INTEGER, message TEXT, samples TEXT, sql TEXT, duration_ms INTEGER,
    PRIMARY KEY (run_id, check_id)
);
CREATE TABLE IF NOT EXISTS metrics (
    run_id TEXT, asset TEXT, metric TEXT, value REAL, recorded_at TEXT,
    PRIMARY KEY (run_id, asset, metric)
);
CREATE TABLE IF NOT EXISTS schema_snapshots (
    run_id TEXT, asset TEXT, columns TEXT, recorded_at TEXT, PRIMARY KEY (run_id, asset)
);
CREATE TABLE IF NOT EXISTS profiles (
    asset TEXT, profiled_at TEXT, profile TEXT
);
"""


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json(v):
    return json.dumps(v, default=str)


class Store:
    def __init__(self, path):
        path = Path(path)
        if str(path) != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def start_run(self, project: str) -> str:
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
        self.db.execute("INSERT INTO runs (run_id, project, started_at, status) VALUES (?,?,?,?)",
                        (run_id, project, _now(), "running"))
        self.db.commit()
        return run_id

    def finish_run(self, run_id, status, score, counts: dict):
        self.db.execute(
            "UPDATE runs SET finished_at=?, status=?, score=?, checks=?, failed=?, warned=?, errored=? "
            "WHERE run_id=?",
            (_now(), status, score, counts.get("total", 0), counts.get("fail", 0),
             counts.get("warn", 0), counts.get("error", 0), run_id))
        self.db.commit()

    def save_result(self, run_id, r):
        self.db.execute(
            "INSERT OR REPLACE INTO results VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, r.check_id, r.asset, r.type, r.dimension, r.severity, r.status, r.score,
             _json(r.observed), _json(r.expected), r.failed_rows, r.total_rows, r.message,
             _json(r.samples), r.sql, r.duration_ms))
        self.db.commit()

    def record_metric(self, run_id, asset, metric, value):
        self.db.execute("INSERT OR REPLACE INTO metrics VALUES (?,?,?,?,?)",
                        (run_id, asset, metric, float(value), _now()))
        self.db.commit()

    def metric_history(self, asset, metric, limit=30, exclude_run=None) -> list[float]:
        rows = self.db.execute(
            "SELECT value FROM metrics WHERE asset=? AND metric=? AND run_id IS NOT ? "
            "ORDER BY recorded_at DESC, run_id DESC LIMIT ?", (asset, metric, exclude_run, limit)).fetchall()
        return [r["value"] for r in rows]

    def save_schema(self, run_id, asset, columns):
        self.db.execute("INSERT OR REPLACE INTO schema_snapshots VALUES (?,?,?,?)",
                        (run_id, asset, _json(columns), _now()))
        self.db.commit()

    def last_schema(self, asset):
        row = self.db.execute("SELECT columns FROM schema_snapshots WHERE asset=? "
                              "ORDER BY recorded_at DESC, run_id DESC LIMIT 1", (asset,)).fetchone()
        return [tuple(c) for c in json.loads(row["columns"])] if row else None

    def save_profile(self, asset, profile):
        self.db.execute("INSERT INTO profiles VALUES (?,?,?)", (asset, _now(), _json(profile)))
        self.db.commit()

    def runs(self, limit=20):
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM runs ORDER BY started_at DESC, run_id DESC LIMIT ?", (limit,))]

    def score_trend(self, limit=20):
        return list(reversed(self.runs(limit)))

    def close(self):
        self.db.close()
