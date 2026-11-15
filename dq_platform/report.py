"""Self-contained HTML report + JSON export for a run."""

from __future__ import annotations

import html
import json

from .checks import DIMENSIONS

STATUS_ORDER = {"fail": 0, "error": 1, "warn": 2, "skipped": 3, "pass": 4}


def _e(v) -> str:
    if v is None:
        return "—"
    return html.escape(v if isinstance(v, str) else json.dumps(v, default=str) if isinstance(v, (list, dict)) else str(v))


def to_json(report) -> str:
    return json.dumps({
        "project": report.project, "run_id": report.run_id, "status": report.status,
        "score": report.score, "counts": report.counts, "asset_scores": report.asset_scores,
        "dimension_scores": report.dimension_scores, "impact": report.impact,
        "connection_errors": report.connection_errors,
        "results": [{**{k: getattr(r, k) for k in (
            "check_id", "asset", "type", "dimension", "severity", "status", "message", "observed",
            "expected", "failed_rows", "total_rows", "samples", "duration_ms")}, "score": r.score}
            for r in report.results],
        "lineage": report.lineage.to_dict() if report.lineage else None,
    }, indent=2, default=str)


def _samples_table(samples) -> str:
    if not samples:
        return ""
    cols = []
    for s in samples:
        for k in s:
            if k not in cols:
                cols.append(k)
    head = "".join(f"<th>{_e(c)}</th>" for c in cols)
    body = "".join("<tr>" + "".join(f"<td>{_e(s.get(c))}</td>" for c in cols) + "</tr>" for s in samples)
    return f'<div class="scroll"><table class="samples"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def _score_cls(s):
    if s is None:
        return "muted"
    return "good" if s >= 95 else "ok" if s >= 80 else "bad"


def _sparkline(trend) -> str:
    pts = [t["score"] for t in trend if t.get("score") is not None]
    if len(pts) < 2:
        return '<p class="muted">Trend appears after two or more runs.</p>'
    w, h, pad = 320, 64, 6
    lo, hi = min(pts + [80]), 100
    xs = [pad + i * (w - 2 * pad) / (len(pts) - 1) for i in range(len(pts))]
    ys = [h - pad - (p - lo) / max(hi - lo, 1e-9) * (h - 2 * pad) for p in pts]
    path = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}" for i, (x, y) in enumerate(zip(xs, ys)))
    return (f'<svg viewBox="0 0 {w} {h}" class="spark" role="img" aria-label="Score trend over last '
            f'{len(pts)} runs"><path d="{path}" fill="none" stroke="currentColor" stroke-width="2"/>'
            f'<circle cx="{xs[-1]:.1f}" cy="{ys[-1]:.1f}" r="3.5" fill="currentColor"/></svg>'
            f'<p class="muted small">Last {len(pts)} runs: {pts[0]:.1f} → {pts[-1]:.1f}</p>')


def to_html(report) -> str:
    c = report.counts
    results = sorted(report.results, key=lambda r: (STATUS_ORDER.get(r.status, 9), r.asset, r.check_id))
    problems = [r for r in results if r.status in ("fail", "warn", "error")]

    tiles = "".join(
        f'<div class="tile"><div class="label">{label}</div><div class="value {cls}">{val}</div></div>'
        for label, val, cls in (
            ("Quality score", f"{report.score:.1f}" if report.score is not None else "—", _score_cls(report.score)),
            ("Checks run", c.get("total", 0), ""),
            ("Failed", c.get("fail", 0), "bad" if c.get("fail") else ""),
            ("Warnings", c.get("warn", 0), "ok" if c.get("warn") else ""),
            ("Errors", c.get("error", 0), "bad" if c.get("error") else ""),
        ))

    dims = "".join(
        f'<div class="dim"><span>{d.title()}</span><div class="bar"><div class="fill {_score_cls(s)}" '
        f'style="width:{s or 0}%"></div></div><b>{"—" if s is None else f"{s:.1f}"}</b></div>'
        for d in DIMENSIONS for s in [report.dimension_scores.get(d)])

    asset_rows = ""
    for a, s in sorted(report.asset_scores.items(), key=lambda kv: (kv[1] is None, kv[1] if kv[1] is not None else 0)):
        rs = [r for r in report.results if r.asset == a]
        bad = sum(r.status == "fail" for r in rs)
        warn = sum(r.status == "warn" for r in rs)
        downstream = report.impact.get(a, [])
        asset_rows += (f"<tr><td><b>{_e(a)}</b></td><td class='num {_score_cls(s)}'>{'—' if s is None else f'{s:.1f}'}</td>"
                       f"<td class='num'>{len(rs)}</td><td class='num'>{bad}</td><td class='num'>{warn}</td>"
                       f"<td>{', '.join(_e(d) for d in downstream) or '<span class=muted>—</span>'}</td></tr>")

    issue_cards = ""
    for r in problems:
        issue_cards += f"""
<details class="issue {r.status}" {'open' if r.status == 'fail' else ''}>
  <summary><span class="pill {r.status}">{r.status}</span> <b>{_e(r.check_id)}</b>
    <span class="muted">· {_e(r.dimension)} · {_e(r.type)}</span><br><span class="msg">{_e(r.message)}</span></summary>
  <dl><dt>Observed</dt><dd>{_e(r.observed)}</dd><dt>Expected</dt><dd>{_e(r.expected)}</dd>
  {f'<dt>Downstream impact</dt><dd>{", ".join(_e(x) for x in report.impact.get(r.asset, [])) or "none"}</dd>' if r.status != 'error' else ''}
  {f'<dt>Note</dt><dd>{_e(r.description)}</dd>' if r.description else ''}</dl>
  {_samples_table(r.samples)}
  {f'<details class="sql"><summary>SQL</summary><pre>{_e(r.sql)}</pre></details>' if r.sql else ''}
</details>"""

    all_rows = "".join(
        f"<tr><td><span class='pill {r.status}'>{r.status}</span></td><td>{_e(r.check_id)}</td>"
        f"<td>{_e(r.dimension)}</td><td>{_e(r.severity)}</td><td>{_e(r.message)}</td>"
        f"<td class='num'>{'—' if r.score is None else f'{r.score * 100:.1f}'}</td><td class='num'>{r.duration_ms}</td></tr>"
        for r in results)

    node_status = {}
    for r in report.results:
        cur = node_status.get(r.asset)
        if r.status in ("fail", "warn") and cur != "fail":
            node_status[r.asset] = r.status
        elif r.status == "pass" and cur is None:
            node_status[r.asset] = "pass"
    for downs in report.impact.values():
        for d in downs:
            if node_status.get(d) in (None, "pass"):
                node_status[d] = "impacted"
    from .lineage import to_svg
    lineage_svg = to_svg(report.lineage, node_status) if report.lineage and report.lineage.edges else ""

    conn_err = "".join(f"<li><b>{_e(k)}</b>: {_e(v)}</li>" for k, v in report.connection_errors.items())

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Data Quality Report</title>
<style>
:root {{ --bg:#f7f7f5; --panel:#fff; --ink:#1b1b18; --muted:#6b6b66; --line:#e4e4df;
  --good:#1f8a4c; --ok:#b7791f; --bad:#d13438; --info:#6e56cf; --code:#f1f1ee; }}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{ --bg:#141413; --panel:#1d1d1b;
  --ink:#ececea; --muted:#a3a39d; --line:#33332f; --good:#4cc38a; --ok:#f0b44a; --bad:#ff6b6b; --info:#a78bfa; --code:#262624; }} }}
:root[data-theme="dark"] {{ --bg:#141413; --panel:#1d1d1b; --ink:#ececea; --muted:#a3a39d; --line:#33332f;
  --good:#4cc38a; --ok:#f0b44a; --bad:#ff6b6b; --info:#a78bfa; --code:#262624; }}
* {{ box-sizing:border-box }}
body {{ margin:0; background:var(--bg); color:var(--ink); font:15px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif; }}
main {{ max-width:1180px; margin:0 auto; padding:28px 16px 64px }}
h1 {{ font-size:26px; margin:0 0 4px }} h2 {{ font-size:18px; margin:36px 0 12px }}
.muted {{ color:var(--muted) }} .small {{ font-size:13px }}
.status {{ display:inline-block; padding:2px 10px; border-radius:999px; font-weight:600; font-size:13px; text-transform:uppercase; letter-spacing:.04em }}
.tiles {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px; margin-top:20px }}
.tile, .panel {{ background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:14px 16px }}
.tile .label {{ color:var(--muted); font-size:13px }} .tile .value {{ font-size:28px; font-weight:650; font-variant-numeric:tabular-nums }}
.good {{ color:var(--good) }} .ok {{ color:var(--ok) }} .bad {{ color:var(--bad) }}
.grid2 {{ display:grid; grid-template-columns:2fr 1fr; gap:12px }} @media (max-width:760px) {{ .grid2 {{ grid-template-columns:1fr }} }}
.dim {{ display:grid; grid-template-columns:120px 1fr 52px; gap:10px; align-items:center; padding:5px 0 }}
.dim b {{ text-align:right; font-variant-numeric:tabular-nums }}
.bar {{ height:8px; background:var(--line); border-radius:4px; overflow:hidden }}
.fill {{ height:100% }} .fill.good {{ background:var(--good) }} .fill.ok {{ background:var(--ok) }} .fill.bad {{ background:var(--bad) }} .fill.muted {{ background:transparent }}
.spark {{ width:100%; height:64px; color:var(--info) }}
table {{ width:100%; border-collapse:collapse; font-size:14px }}
th, td {{ text-align:left; padding:7px 10px; border-bottom:1px solid var(--line); vertical-align:top }}
th {{ color:var(--muted); font-weight:600; font-size:12px; text-transform:uppercase; letter-spacing:.04em }}
.num {{ text-align:right; font-variant-numeric:tabular-nums }}
.scroll {{ overflow-x:auto }}
.pill {{ display:inline-block; min-width:56px; text-align:center; padding:1px 8px; border-radius:999px; font-size:12px; font-weight:600; border:1px solid currentColor }}
.pill.fail, .pill.error {{ color:var(--bad) }} .pill.warn {{ color:var(--ok) }} .pill.pass {{ color:var(--good) }} .pill.skipped {{ color:var(--muted) }}
.issue {{ background:var(--panel); border:1px solid var(--line); border-left:4px solid var(--muted); border-radius:10px; padding:12px 16px; margin-bottom:10px }}
.issue.fail, .issue.error {{ border-left-color:var(--bad) }} .issue.warn {{ border-left-color:var(--ok) }}
.issue summary {{ cursor:pointer; list-style:none }} .issue .msg {{ display:inline-block; margin-top:4px }}
dl {{ display:grid; grid-template-columns:150px 1fr; gap:4px 12px; margin:12px 0 }} dt {{ color:var(--muted) }} dd {{ margin:0 }}
table.samples {{ font-size:13px; background:var(--code); border-radius:8px }}
pre {{ background:var(--code); padding:10px; border-radius:8px; overflow-x:auto; font-size:12.5px; white-space:pre-wrap }}
details.sql summary {{ color:var(--muted); font-size:13px; cursor:pointer }}
svg.lineage {{ display:block; max-width:none; font-size:12.5px }}
svg.lineage .edge {{ fill:none; stroke:var(--muted); stroke-width:1.4; opacity:.7 }}
svg.lineage .arrow {{ fill:var(--muted) }}
svg.lineage .node rect {{ fill:var(--panel); stroke:var(--line); stroke-width:1.5 }}
svg.lineage .node text {{ fill:var(--ink) }}
svg.lineage .node.ext rect {{ stroke-dasharray:4 3 }}
svg.lineage .node.fail rect {{ stroke:var(--bad); stroke-width:2.5 }}
svg.lineage .node.warn rect {{ stroke:var(--ok); stroke-width:2.5 }}
svg.lineage .node.pass rect {{ stroke:var(--good); stroke-width:2 }}
svg.lineage .node.impacted rect {{ stroke:var(--info); stroke-width:2.5 }}
.key {{ display:inline-block; width:10px; height:10px; border-radius:3px; margin:0 4px 0 10px; border:2px solid }}
.key.fail {{ border-color:var(--bad) }} .key.warn {{ border-color:var(--ok) }} .key.impacted {{ border-color:var(--info) }} .key.pass {{ border-color:var(--good) }}
</style></head>
<body><main>
<h1>Data Quality Report — {_e(report.project)}</h1>
<div class="muted">Run {_e(report.run_id)} · {report.started_at:%Y-%m-%d %H:%M UTC} ·
 <span class="status pill {report.status}">{report.status}</span></div>
{f'<div class="panel" style="margin-top:16px;border-color:var(--bad)"><b>Connection errors</b><ul>{conn_err}</ul></div>' if conn_err else ''}
<div class="tiles">{tiles}</div>

<div class="grid2" style="margin-top:12px">
  <div class="panel"><b>Score by dimension</b>{dims}</div>
  <div class="panel"><b>Score trend</b>{_sparkline(report.trend)}</div>
</div>

<h2>Assets</h2>
<div class="panel scroll"><table><thead><tr><th>Asset</th><th class="num">Score</th><th class="num">Checks</th>
<th class="num">Failed</th><th class="num">Warn</th><th>Downstream impact of issues</th></tr></thead><tbody>{asset_rows}</tbody></table></div>

<h2>Issues ({len(problems)})</h2>
{issue_cards or '<div class="panel good">No issues — every check passed.</div>'}

{f'<h2>Lineage &amp; impact</h2><p class="muted small"><span class="key fail"></span>failing <span class="key warn"></span>warning <span class="key impacted"></span>downstream of an issue <span class="key pass"></span>healthy · rounded nodes are systems outside the checked assets · hover an edge for the job that moves the data</p><div class="panel scroll">{lineage_svg}</div>' if lineage_svg else ''}

<h2>All checks</h2>
<div class="panel scroll"><table><thead><tr><th>Status</th><th>Check</th><th>Dimension</th><th>Severity</th>
<th>Result</th><th class="num">Score</th><th class="num">ms</th></tr></thead><tbody>{all_rows}</tbody></table></div>
</main>
</body></html>"""
