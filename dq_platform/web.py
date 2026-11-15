"""Web app for the platform: a JSON API plus a single-page UI, standard library only.

    python -m dq_platform serve --demo            # sample ERP + warehouse
    python -m dq_platform serve -p dq_project.yml # your own project

Binds to 127.0.0.1 by default. There is no authentication: it is a local
prototype. Put it behind your SSO proxy (e.g. IAP on Cloud Run) before sharing.
"""

from __future__ import annotations

import json
import re
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yaml

from .checks import CHECKS, DIMENSIONS, FORMATS
from .config import load_project
from .connectors import create_connector
from .lineage import build_lineage, to_svg
from .runner import expand_checks, run_project
from .store import Store

UI_FILE = Path(__file__).parent / "ui" / "index.html"


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def _loads(v):
    if v is None:
        return None
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return v


class App:
    def __init__(self, project_path, demo_dir=None):
        self.path = Path(project_path).resolve()
        self.demo_dir = Path(demo_dir).resolve() if demo_dir else None
        self.write_lock = threading.Lock()
        self.run_lock = threading.Lock()
        self._lineage = (None, None)

    # ------------------------------------------------------------ helpers
    def project(self):
        try:
            return load_project(self.path)
        except Exception as e:  # noqa: BLE001
            raise ApiError(500, f"project config is invalid: {e}") from e

    def store(self):
        return Store(self.project().store_path)

    def raw(self) -> dict:
        return yaml.safe_load(self.path.read_text()) or {}

    def save_raw(self, raw: dict):
        self.save_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True, width=110))

    def save_text(self, text: str):
        with self.write_lock:
            tmp = self.path.with_name(f".{self.path.name}.check.yml")
            tmp.write_text(text)
            try:
                load_project(tmp)
            except Exception as e:  # noqa: BLE001
                raise ApiError(400, f"not saved: {e}") from e
            finally:
                tmp.unlink(missing_ok=True)
            self.path.write_text(text)
        self._lineage = (None, None)

    def lineage(self, project):
        key = (self.path.stat().st_mtime, project.transforms_dir and sum(
            f.stat().st_mtime for f in project.transforms_dir.rglob("*.sql")))
        if self._lineage[0] == key:
            return self._lineage[1]
        conns = {}
        for name, cfg in project.connections.items():
            try:
                conns[name] = create_connector(name, cfg)
            except Exception:  # noqa: BLE001 - lineage is best-effort
                pass
        g = build_lineage(project, conns)
        for c in conns.values():
            c.close()
        self._lineage = (key, g)
        return g

    def _latest_run_id(self, st):
        row = st.db.execute("SELECT run_id FROM runs WHERE status != 'running' "
                            "ORDER BY started_at DESC, run_id DESC LIMIT 1").fetchone()
        return row["run_id"] if row else None

    def _current(self, st, project):
        ids = {c["id"] for a in project.assets.values() for c in expand_checks(a)}
        return self._decode(st.current_results(ids))

    def _results(self, st, run_id):
        return self._decode(st.db.execute("SELECT * FROM results WHERE run_id=?", (run_id,)).fetchall())

    def _decode(self, rows):
        out = []
        for r in rows:
            d = dict(r)
            for k in ("observed", "expected", "samples"):
                d[k] = _loads(d[k])
            out.append(d)
        return out

    @staticmethod
    def _mean(xs):
        xs = [x for x in xs if x is not None]
        return round(sum(xs) / len(xs) * 100, 2) if xs else None

    # ------------------------------------------------------------ endpoints
    def overview(self, q, body):
        p = self.project()
        st = self.store()
        try:
            runs = st.runs(1)
            trend = st.score_trend(30)
            results = self._current(st, p)
        finally:
            st.close()
        return {
            "project": p.name, "config_path": str(self.path), "demo": bool(self.demo_dir),
            "sources": len(p.connections), "assets": len(p.assets),
            "checks": sum(len(expand_checks(a)) for a in p.assets.values()),
            "latest": runs[0] if runs else None,
            "score": self._mean(r["score"] for r in results),
            "evaluated": len(results),
            "trend": trend,
            "dimensions": {d: self._mean(r["score"] for r in results if r["dimension"] == d) for d in DIMENSIONS},
            "issues": sorted([r for r in results if r["status"] in ("fail", "warn", "error")],
                             key=lambda r: ({"fail": 0, "error": 1, "warn": 2}[r["status"]], r["check_id"])),
        }

    def run(self, q, body):
        if not self.run_lock.acquire(blocking=False):
            raise ApiError(409, "a run is already in progress")
        try:
            t0 = time.time()
            rep = run_project(self.path, assets=body.get("assets") or None, tags=body.get("tags") or None)
            return {"run_id": rep.run_id, "status": rep.status, "score": rep.score, "counts": rep.counts,
                    "seconds": round(time.time() - t0, 2), "connection_errors": rep.connection_errors}
        finally:
            self.run_lock.release()

    def runs(self, q, body):
        st = self.store()
        try:
            return st.runs(int(q.get("limit", ["50"])[0]))
        finally:
            st.close()

    def run_detail(self, q, body, run_id):
        p = self.project()
        st = self.store()
        try:
            if run_id == "latest":
                latest = self._latest_run_id(st)
                if not latest:
                    return {"run": None, "results": []}
                run = {"run_id": "current", "current": True,
                       **dict(st.db.execute("SELECT started_at FROM runs WHERE run_id=?", (latest,)).fetchone())}
                results = self._current(st, p)
            else:
                row = st.db.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
                if not row:
                    raise ApiError(404, f"run {run_id} not found")
                run, results = dict(row), self._results(st, run_id)
        finally:
            st.close()
        g = self.lineage(p)
        status = {}
        for r in results:
            cur = status.get(r["asset"])
            if r["status"] in ("fail", "warn") and cur != "fail":
                status[r["asset"]] = r["status"]
            elif r["status"] == "pass" and cur is None:
                status[r["asset"]] = "pass"
        impact = {a: g.downstream(a) for a, s in status.items() if s in ("fail", "warn") and a in g.nodes}
        for downs in impact.values():
            for d in downs:
                if status.get(d) in (None, "pass"):
                    status[d] = "impacted"
        assets = sorted({r["asset"] for r in results})
        return {
            "run": run, "results": results, "impact": impact,
            "asset_scores": {a: self._mean(r["score"] for r in results if r["asset"] == a) for a in assets},
            "dimensions": {d: self._mean(r["score"] for r in results if r["dimension"] == d) for d in DIMENSIONS},
            "lineage_svg": to_svg(g, status) if g.edges else "",
        }

    def assets(self, q, body):
        p = self.project()
        st = self.store()
        try:
            results = {r["check_id"]: r for r in self._current(st, p)}
        finally:
            st.close()
        g = self.lineage(p)
        out = []
        for a in p.assets.values():
            checks = []
            for c in expand_checks(a):
                r = results.get(c["id"])
                checks.append({"id": c["id"], "type": c.get("type"), "column": c.get("column") or c.get("columns"),
                               "severity": c.get("severity", "error"), "config": c,
                               "status": r["status"] if r else None, "message": r["message"] if r else None})
            scores = [results[c["id"]]["score"] for c in checks if c["id"] in results]
            out.append({"name": a.name, "connection": a.connection, "table": a.table, "owner": a.owner,
                        "tags": a.tags, "description": a.description, "checks": checks,
                        "score": self._mean(scores),
                        "upstream": g.upstream(a.name), "downstream": g.downstream(a.name)})
        return out

    def add_asset(self, q, body):
        name = (body.get("name") or "").strip()
        if not re.fullmatch(r"[A-Za-z_][\w-]*", name):
            raise ApiError(400, "asset name must be letters, digits, _ or -")
        raw = self.raw()
        raw.setdefault("assets", {})
        if name in raw["assets"]:
            raise ApiError(400, f"asset {name} already exists")
        entry = {"connection": body.get("connection"), "table": body.get("table")}
        for k in ("owner", "description", "pack", "mapping"):
            if body.get(k):
                entry[k] = body[k]
        if body.get("tags"):
            entry["tags"] = [t.strip() for t in str(body["tags"]).split(",") if t.strip()]
        entry["checks"] = body.get("checks") or [{"type": "row_count", "min": 1}]
        if not entry.get("table") and not entry.get("pack"):
            raise ApiError(400, "table is required")
        raw["assets"][name] = {k: v for k, v in entry.items() if v is not None}
        self.save_raw(raw)
        return {"ok": True, "asset": name}

    def delete_asset(self, q, body, name):
        raw = self.raw()
        if name not in (raw.get("assets") or {}):
            raise ApiError(404, f"asset {name} not found")
        del raw["assets"][name]
        raw["checks"] = [c for c in raw.get("checks") or [] if name not in (c.get("source"), c.get("asset"), c.get("target"))]
        raw["lineage"] = [e for e in raw.get("lineage") or []
                          if name not in ([e["from"]] if isinstance(e["from"], str) else e["from"])
                          and name not in ([e["to"]] if isinstance(e["to"], str) else e["to"])]
        self.save_raw(raw)
        return {"ok": True}

    def add_checks(self, q, body, name):
        raw = self.raw()
        if name not in (raw.get("assets") or {}):
            raise ApiError(404, f"asset {name} not found")
        new = body.get("checks") or []
        for c in new:
            if c.get("type") not in CHECKS:
                raise ApiError(400, f"unknown check type {c.get('type')!r}")
        raw["assets"][name].setdefault("checks", []).extend(new)
        self.save_raw(raw)
        return {"ok": True, "added": len(new)}

    def remove_check(self, q, body, name):
        """Remove a check that was declared directly on the asset (not one from a pack)."""
        check_id = body.get("id")
        p = self.project()
        asset = p.assets.get(name)
        if not asset:
            raise ApiError(404, f"asset {name} not found")
        raw = self.raw()
        declared = raw["assets"][name].get("checks", [])
        from .config import Asset
        for i, c in enumerate(declared):
            ids = [x["id"] for x in expand_checks(Asset(name=name, connection="", table="", checks=[c]))]
            if check_id in ids:
                declared.pop(i)
                self.save_raw(raw)
                return {"ok": True}
        raise ApiError(400, "that check comes from a pack or a cross-asset rule; edit it in Config")

    def profile(self, q, body):
        from .config import Asset
        from .profiler import profile_asset, suggest_checks
        p = self.project()
        if body.get("asset"):
            a = p.assets.get(body["asset"])
            if not a:
                raise ApiError(404, "unknown asset")
        else:
            a = Asset(name=body.get("table", "table").split(".")[-1], connection=body["connection"], table=body["table"])
        conn = create_connector(a.connection, p.connections[a.connection])
        try:
            prof = profile_asset(conn, a)
        finally:
            conn.close()
        existing = set()
        if a.name in p.assets:
            for c in expand_checks(p.assets[a.name]):
                existing.add((c.get("type"), str(c.get("column") or c.get("columns"))))
        sugg = []
        for s in suggest_checks(prof):
            key = (s["type"], str(s.get("column") or (s.get("columns") if s["type"] != "schema" else None)))
            sugg.append({"check": s, "exists": key in existing or (s["type"] in ("schema", "row_count", "volume_anomaly")
                                                                  and any(t == s["type"] for t, _ in existing))})
        st = self.store()
        st.save_profile(a.name, prof)
        st.close()
        return {"profile": prof, "suggestions": sugg}

    def connections(self, q, body):
        raw = self.raw()
        return [{"name": n, "type": c.get("type"), "settings": c} for n, c in (raw.get("connections") or {}).items()]

    def add_connection(self, q, body):
        name = (body.get("name") or "").strip()
        if not re.fullmatch(r"[A-Za-z_][\w-]*", name):
            raise ApiError(400, "source name must be letters, digits, _ or -")
        settings = {k: v for k, v in (body.get("settings") or {}).items() if v not in (None, "")}
        if not settings.get("type"):
            raise ApiError(400, "type is required")
        raw = self.raw()
        raw.setdefault("connections", {})
        if name in raw["connections"] and not body.get("replace"):
            raise ApiError(400, f"source {name} already exists")
        raw["connections"][name] = settings
        self.save_raw(raw)
        return {"ok": True}

    def test_connection(self, q, body, name):
        p = self.project()
        if name not in p.connections:
            raise ApiError(404, f"unknown source {name}")
        t0 = time.time()
        try:
            conn = create_connector(name, p.connections[name])
            tables = conn.list_tables()
            conn.close()
            return {"ok": True, "tables": len(tables), "ms": int((time.time() - t0) * 1000),
                    "dialect": conn.dialect.name}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    def tables(self, q, body, name):
        p = self.project()
        if name not in p.connections:
            raise ApiError(404, f"unknown source {name}")
        conn = create_connector(name, p.connections[name])
        try:
            table = q.get("table", [None])[0]
            if table:
                return {"table": table, "columns": [{"name": c, "type": t} for c, t in conn.get_columns(table)]}
            registered = {(a.connection, a.table.lower()) for a in p.assets.values()}
            return [{"table": t, "registered": (name, t.lower()) in registered} for t in conn.list_tables()]
        finally:
            conn.close()

    def config_get(self, q, body):
        return {"text": self.path.read_text(), "path": str(self.path),
                "check_types": sorted(CHECKS), "formats": sorted(FORMATS)}

    def config_put(self, q, body):
        self.save_text(body.get("text", ""))
        return {"ok": True}

    def incidents(self, q, body):
        from .demo import INCIDENTS
        return INCIDENTS if self.demo_dir else {}

    def incident(self, q, body):
        from .demo import apply_incident
        if not self.demo_dir:
            raise ApiError(400, "incidents are only available in demo mode")
        return {"ok": True, "message": apply_incident(self.demo_dir, body.get("kind"))}

    def reset_demo(self, q, body):
        if not self.demo_dir:
            raise ApiError(400, "only available in demo mode")
        seed_demo(self.demo_dir, rebuild=True)
        self._lineage = (None, None)
        return {"ok": True}


def seed_demo(target, rebuild=False, baseline_runs=5):
    """Create the demo project (if needed) and a few baseline runs so trends and
    anomaly detection have history."""
    from .demo import build_demo
    target = Path(target)
    project = target / "dq_project.yml"
    if rebuild or not project.exists():
        store = target / ".dq" / "results.db"
        store.unlink(missing_ok=True)
        project = build_demo(target)
        for _ in range(baseline_runs):
            run_project(project)
    return project


ROUTES = [
    ("GET", r"/api/overview", "overview"),
    ("POST", r"/api/run", "run"),
    ("GET", r"/api/runs", "runs"),
    ("GET", r"/api/runs/([\w-]+)", "run_detail"),
    ("GET", r"/api/assets", "assets"),
    ("POST", r"/api/assets", "add_asset"),
    ("DELETE", r"/api/assets/([\w-]+)", "delete_asset"),
    ("POST", r"/api/assets/([\w-]+)/checks", "add_checks"),
    ("DELETE", r"/api/assets/([\w-]+)/checks", "remove_check"),
    ("POST", r"/api/profile", "profile"),
    ("GET", r"/api/connections", "connections"),
    ("POST", r"/api/connections", "add_connection"),
    ("POST", r"/api/connections/([\w-]+)/test", "test_connection"),
    ("GET", r"/api/connections/([\w-]+)/tables", "tables"),
    ("GET", r"/api/config", "config_get"),
    ("PUT", r"/api/config", "config_put"),
    ("GET", r"/api/demo/incidents", "incidents"),
    ("POST", r"/api/demo/incident", "incident"),
    ("POST", r"/api/demo/reset", "reset_demo"),
]


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        server_version = "dq_platform"

        def log_message(self, fmt, *args):  # quieter console
            if not self.path.startswith("/api/"):
                return
            print(f"  {self.command} {self.path} -> {args[1] if len(args) > 1 else ''}")

        def _send(self, status, payload, ctype="application/json"):
            data = payload if isinstance(payload, bytes) else json.dumps(payload, default=str).encode()
            self.send_response(status)
            self.send_header("Content-Type", f"{ctype}; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _dispatch(self):
            url = urlparse(self.path)
            if self.command == "GET" and url.path in ("/", "/index.html"):
                return self._send(200, UI_FILE.read_bytes(), "text/html")
            for method, pattern, fn in ROUTES:
                m = re.fullmatch(pattern, url.path)
                if m and method == self.command:
                    body = {}
                    n = int(self.headers.get("Content-Length") or 0)
                    if n:
                        try:
                            body = json.loads(self.rfile.read(n))
                        except ValueError:
                            return self._send(400, {"error": "body must be JSON"})
                    try:
                        return self._send(200, getattr(app, fn)(parse_qs(url.query), body, *m.groups()))
                    except ApiError as e:
                        return self._send(e.status, {"error": str(e)})
                    except Exception as e:  # noqa: BLE001
                        traceback.print_exc()
                        return self._send(500, {"error": f"{type(e).__name__}: {e}"})
            self._send(404, {"error": "not found"})

        do_GET = do_POST = do_PUT = do_DELETE = _dispatch

    return Handler


def serve(project_path, host="127.0.0.1", port=8765, demo_dir=None):
    app = App(project_path, demo_dir)
    httpd = ThreadingHTTPServer((host, port), make_handler(app))
    print(f"dq_platform UI on http://{host}:{httpd.server_address[1]}  (project: {app.path})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
