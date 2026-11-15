"""Runs a project: connect, execute checks, score, store, attach lineage impact."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .checks import CHECKS, DEFAULT_DIMENSION, DIMENSIONS, PER_COLUMN, CheckError, CheckResult, Context
from .config import Project, load_project
from .connectors import create_connector
from .lineage import build_lineage
from .store import Store


@dataclass
class RunReport:
    project: str
    run_id: str
    started_at: datetime
    results: list[CheckResult]
    status: str
    score: float | None
    asset_scores: dict
    dimension_scores: dict
    impact: dict                       # failing asset -> downstream assets
    lineage: object = None
    trend: list = field(default_factory=list)
    connection_errors: dict = field(default_factory=dict)

    @property
    def counts(self):
        c = {"total": len(self.results)}
        for r in self.results:
            c[r.status] = c.get(r.status, 0) + 1
        return c


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs) * 100, 2) if xs else None


def expand_checks(asset) -> list[dict]:
    """Per-column shorthand -> one check per column; give every check a stable id."""
    out, seen = [], {}
    for cfg in asset.checks:
        cfg = dict(cfg)
        ctype = cfg.get("type")
        cols = cfg.get("columns")
        if ctype in PER_COLUMN and isinstance(cols, list) and len(cols) > 1:
            base = cfg.pop("columns")
            group_id = cfg.pop("id", None)
            for c in base:
                out.append({**cfg, "column": c, **({"id": f"{group_id}.{c}"} if group_id else {})})
        else:
            out.append(cfg)
    for cfg in out:
        if "id" in cfg and not str(cfg["id"]).startswith(asset.name + "."):
            cfg["id"] = f"{asset.name}.{cfg['id']}"  # ids are unique per project
        if "id" not in cfg:
            col = cfg.get("column") or (cfg.get("columns") if isinstance(cfg.get("columns"), str) else None)
            if isinstance(cfg.get("columns"), list) and cfg.get("type") == "unique":
                col = "+".join(cfg["columns"])
            parts = [asset.name, cfg.get("type", "?")] + ([col] if col else []) + \
                    ([cfg["target"]] if cfg.get("target") else [])
            cfg["id"] = ".".join(parts)
        n = seen.get(cfg["id"], 0)
        seen[cfg["id"]] = n + 1
        if n:
            cfg["id"] = f"{cfg['id']}#{n + 1}"
    return out


def run_check(ctx: Context, asset, cfg: dict) -> CheckResult:
    ctype = cfg.get("type", "")
    severity = cfg.get("severity", "error").lower()
    res = CheckResult(check_id=cfg["id"], asset=asset.name, type=ctype,
                      dimension=cfg.get("dimension", DEFAULT_DIMENSION.get(ctype, "validity")),
                      severity=severity, status="error", description=cfg.get("description", ""))
    t0 = time.perf_counter()
    try:
        fn = CHECKS.get(ctype)
        if fn is None:
            raise CheckError(f"unknown check type {ctype!r}; available: {', '.join(sorted(CHECKS))}")
        out = fn(ctx, asset, cfg)
        for k in ("observed", "expected", "failed_rows", "total_rows", "samples", "sql", "message"):
            if k in out:
                setattr(res, k, out[k])
        if out.get("skipped"):
            res.status = "skipped"
        else:
            res.status = "pass" if out["ok"] else ("warn" if severity == "warn" else "fail")
    except Exception as e:  # noqa: BLE001 - one broken check must not stop the run
        res.status, res.message = "error", f"{type(e).__name__}: {e}"
    res.duration_ms = int((time.perf_counter() - t0) * 1000)
    return res


def run_project(project: Project | str, assets=None, tags=None, store: Store | None = None,
                sample_limit=None, now=None) -> RunReport:
    if not isinstance(project, Project):
        project = load_project(project)
    owns_store = store is None
    store = store or Store(project.store_path)
    started = datetime.now(timezone.utc)
    run_id = store.start_run(project.name)

    selected = [a for a in project.assets.values()
                if (not assets or a.name in assets) and (not tags or set(tags) & set(a.tags))]
    needed = {a.connection for a in selected}
    # cross-system checks need the other side's connection too
    for a in selected:
        for c in a.checks:
            for other in (c.get("target"), (c.get("references") or {}).get("asset")):
                if other in project.assets:
                    needed.add(project.assets[other].connection)

    connectors, conn_errors = {}, {}
    for name in sorted(needed):
        try:
            connectors[name] = create_connector(name, project.connections[name])
        except Exception as e:  # noqa: BLE001
            conn_errors[name] = f"{type(e).__name__}: {e}"

    ctx = Context(connectors, project.assets, store=store, run_id=run_id, now=now,
                  sample_limit=sample_limit if sample_limit is not None else project.settings.get("sample_rows", 5))
    results = []
    for asset in selected:
        for cfg in expand_checks(asset):
            if asset.connection in conn_errors:
                r = CheckResult(cfg["id"], asset.name, cfg.get("type", ""),
                                cfg.get("dimension", DEFAULT_DIMENSION.get(cfg.get("type"), "validity")),
                                cfg.get("severity", "error"), "error",
                                f"connection {asset.connection} unavailable: {conn_errors[asset.connection]}")
            else:
                r = run_check(ctx, asset, cfg)
            store.save_result(run_id, r)
            results.append(r)

    asset_scores = {a.name: _mean(r.score for r in results if r.asset == a.name) for a in selected}
    dimension_scores = {d: _mean(r.score for r in results if r.dimension == d) for d in DIMENSIONS}
    score = _mean(r.score for r in results)

    statuses = {r.status for r in results}
    status = next((s for s in ("fail", "error", "warn") if s in statuses), "pass")

    lineage = build_lineage(project, connectors)
    impact = {}
    for a in selected:
        if any(r.asset == a.name and r.status in ("fail", "warn") for r in results):
            impact[a.name] = lineage.downstream(a.name)

    counts = {"total": len(results)}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    store.finish_run(run_id, status, score, counts)
    trend = store.score_trend()
    if owns_store:
        store.close()
    for c in connectors.values():
        try:
            c.close()
        except Exception:  # noqa: BLE001
            pass

    return RunReport(project=project.name, run_id=run_id, started_at=started, results=results,
                     status=status, score=score, asset_scores=asset_scores,
                     dimension_scores=dimension_scores, impact=impact, lineage=lineage,
                     trend=trend, connection_errors=conn_errors)
