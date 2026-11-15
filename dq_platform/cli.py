"""Command line: python -m dq_platform <command> ...

  demo       build the sample ERP + warehouse and run every check against it
  run        run a project's checks, write HTML/JSON reports, exit non-zero on failure
  discover   list tables and columns on a connection
  profile    profile an asset (or any table) and suggest checks
  lineage    print the lineage graph, or the upstream/downstream of one asset
  history    show recent runs and scores
  serve      start the web app (add --demo for the sample project)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import Asset, load_project


def _write(path, text):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def _summary(report) -> str:
    c = report.counts
    lines = [f"\n{report.project}  run {report.run_id}",
             f"status: {report.status.upper()}   score: {report.score if report.score is not None else '—'}   "
             f"checks: {c.get('total', 0)}  pass {c.get('pass', 0)}  fail {c.get('fail', 0)}  "
             f"warn {c.get('warn', 0)}  error {c.get('error', 0)}  skipped {c.get('skipped', 0)}", ""]
    icon = {"pass": "✓", "fail": "✗", "warn": "!", "error": "E", "skipped": "-"}
    for r in sorted(report.results, key=lambda r: ({"fail": 0, "error": 1, "warn": 2}.get(r.status, 3), r.check_id)):
        lines.append(f"  {icon.get(r.status, '?')} {r.status:<7} {r.check_id:<52} {r.message}")
    if report.impact:
        lines.append("\ndownstream impact:")
        for a, downs in report.impact.items():
            lines.append(f"  {a} -> {', '.join(downs) if downs else '(no downstream assets)'}")
    for name, err in report.connection_errors.items():
        lines.append(f"\nconnection {name} failed: {err}")
    return "\n".join(lines)


def _exit_code(report, fail_on) -> int:
    levels = {"never": set(), "error": {"fail", "error"}, "warn": {"fail", "error", "warn"}}[fail_on]
    return 1 if any(r.status in levels for r in report.results) else 0


def cmd_run(args):
    from .report import to_html, to_json
    from .runner import run_project
    report = run_project(args.project, assets=args.asset, tags=args.tag)
    print(_summary(report))
    project = load_project(args.project)
    html_path = args.report or (project.root / "reports" / f"dq_report_{report.run_id}.html")
    print(f"\nHTML report: {_write(html_path, to_html(report)).resolve()}")
    if args.json:
        print(f"JSON results: {_write(args.json, to_json(report)).resolve()}")
    return _exit_code(report, args.fail_on)


def cmd_demo(args):
    from .demo import build_demo
    path = build_demo(args.dir)
    print(f"demo project written to {path}")
    args.project, args.asset, args.tag, args.json = str(path), None, None, args.json
    args.report = args.report or str(Path(args.dir) / "reports" / "latest.html")
    return cmd_run(args) if not args.no_run else 0


def cmd_discover(args):
    from .connectors import create_connector
    project = load_project(args.project)
    conn = create_connector(args.connection, project.connections[args.connection])
    tables = [args.table] if args.table else conn.list_tables()
    for t in tables:
        cols = conn.get_columns(t)
        print(f"{t}  ({len(cols)} columns)")
        for c, typ in cols:
            print(f"    {c:<32} {typ}")
    conn.close()
    return 0


def cmd_profile(args):
    from .connectors import create_connector
    from .profiler import profile_asset, suggestions_yaml
    from .store import Store
    project = load_project(args.project)
    if args.asset:
        asset = project.assets[args.asset]
    else:
        if not (args.connection and args.table):
            raise SystemExit("give --asset, or --connection and --table")
        asset = Asset(name=args.table.split(".")[-1], connection=args.connection, table=args.table)
    conn = create_connector(asset.connection, project.connections[asset.connection])
    prof = profile_asset(conn, asset)
    conn.close()
    st = Store(project.store_path)
    st.save_profile(asset.name, prof)
    st.close()
    print(f"{asset.name}: {prof['rows']} rows\n")
    print(f"  {'column':<24} {'type':<12} {'null%':>7} {'distinct':>9}  min .. max")
    for c in prof["columns"]:
        print(f"  {c['name']:<24} {c['type'][:12]:<12} {c['null_pct']:>7} {c['distinct']:>9}  "
              f"{str(c['min'])[:22]} .. {str(c['max'])[:22]}")
    if args.json:
        _write(args.json, json.dumps(prof, indent=2, default=str))
    print("\n" + suggestions_yaml(prof))
    return 0


def cmd_lineage(args):
    from .connectors import create_connector
    from .lineage import build_lineage
    project = load_project(args.project)
    conns = {}
    for name, cfg in project.connections.items():
        try:
            conns[name] = create_connector(name, cfg)
        except Exception as e:  # noqa: BLE001
            print(f"(skipping view discovery on {name}: {e})", file=sys.stderr)
    g = build_lineage(project, conns)
    if args.asset:
        print(f"upstream of {args.asset}:   {', '.join(g.upstream(args.asset)) or '(none)'}")
        print(f"downstream of {args.asset}: {', '.join(g.downstream(args.asset)) or '(none)'}")
    else:
        print({"mermaid": g.to_mermaid, "json": g.to_json, "dot": g.to_dot}[args.format]())
    return 0


def cmd_history(args):
    from .store import Store
    project = load_project(args.project)
    rows = Store(project.store_path).runs(args.limit)
    print(f"{'run':<24} {'status':<8} {'score':>6} {'checks':>7} {'fail':>5} {'warn':>5} {'err':>4}")
    for r in rows:
        print(f"{r['run_id']:<24} {r['status'] or '':<8} {r['score'] if r['score'] is not None else '—':>6} "
              f"{r['checks'] or 0:>7} {r['failed'] or 0:>5} {r['warned'] or 0:>5} {r['errored'] or 0:>4}")
    return 0


def cmd_serve(args):
    from .web import seed_demo, serve
    demo_dir = None
    project = args.project
    if args.demo:
        demo_dir = args.dir
        print("preparing demo data and baseline runs…")
        project = str(seed_demo(demo_dir))
    elif not Path(project).exists():
        raise SystemExit(f"{project} not found. Use --demo, or -p path/to/dq_project.yml")
    serve(project, host=args.host, port=args.port, demo_dir=demo_dir)
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="dq", description="Platform-agnostic data quality, integrity and lineage.")
    sub = p.add_subparsers(dest="command", required=True)

    def run_opts(sp):
        sp.add_argument("--report", help="HTML report path")
        sp.add_argument("--json", help="also write machine-readable results here")
        sp.add_argument("--fail-on", choices=["error", "warn", "never"], default="error",
                        help="exit code 1 when checks at this level fail (for CI / Airflow / Cloud Composer)")

    sp = sub.add_parser("demo", help="build and run the sample project")
    sp.add_argument("--dir", default="dq_demo")
    sp.add_argument("--no-run", action="store_true")
    run_opts(sp)
    sp.set_defaults(fn=cmd_demo, fail_on="never")

    sp = sub.add_parser("run", help="run checks")
    sp.add_argument("-p", "--project", default="dq_project.yml")
    sp.add_argument("--asset", action="append", help="only this asset (repeatable)")
    sp.add_argument("--tag", action="append", help="only assets with this tag (repeatable)")
    run_opts(sp)
    sp.set_defaults(fn=cmd_run)

    sp = sub.add_parser("discover", help="list tables/columns on a connection")
    sp.add_argument("-p", "--project", default="dq_project.yml")
    sp.add_argument("connection")
    sp.add_argument("--table")
    sp.set_defaults(fn=cmd_discover)

    sp = sub.add_parser("profile", help="profile a table and suggest checks")
    sp.add_argument("-p", "--project", default="dq_project.yml")
    sp.add_argument("--asset")
    sp.add_argument("--connection")
    sp.add_argument("--table")
    sp.add_argument("--json")
    sp.set_defaults(fn=cmd_profile)

    sp = sub.add_parser("lineage", help="show lineage")
    sp.add_argument("-p", "--project", default="dq_project.yml")
    sp.add_argument("--asset", help="show upstream/downstream of this asset")
    sp.add_argument("--format", choices=["mermaid", "json", "dot"], default="mermaid")
    sp.set_defaults(fn=cmd_lineage)

    sp = sub.add_parser("serve", help="start the web app")
    sp.add_argument("-p", "--project", default="dq_project.yml")
    sp.add_argument("--demo", action="store_true", help="serve the sample ERP + warehouse project")
    sp.add_argument("--dir", default="dq_demo", help="where the demo project lives")
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=8765)
    sp.set_defaults(fn=cmd_serve)

    sp = sub.add_parser("history", help="recent runs")
    sp.add_argument("-p", "--project", default="dq_project.yml")
    sp.add_argument("--limit", type=int, default=20)
    sp.set_defaults(fn=cmd_history)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)
