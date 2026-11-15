"""Lineage graph: declared edges + edges parsed from SQL (database views and
transformation scripts), resolved to assets, with impact / root-cause queries."""

from __future__ import annotations

import json
import re
from collections import defaultdict, deque
from pathlib import Path

_COMMENTS = re.compile(r"--[^\n]*|/\*.*?\*/", re.S)
_IDENT = r"(?:`[^`]+`|\"[^\"]+\"|\[[^\]]+\]|[A-Za-z_][\w$]*)(?:\s*\.\s*(?:`[^`]+`|\"[^\"]+\"|\[[^\]]+\]|[A-Za-z_][\w$]*)){0,2}"
_TARGET = re.compile(
    rf"\b(?:CREATE\s+(?:OR\s+REPLACE\s+)?(?:TEMP(?:ORARY)?\s+)?(?:TABLE|VIEW|MATERIALIZED\s+VIEW)"
    rf"(?:\s+IF\s+NOT\s+EXISTS)?|INSERT\s+(?:INTO|OVERWRITE)(?:\s+TABLE)?|MERGE\s+INTO)\s+({_IDENT})", re.I)
_SOURCE = re.compile(rf"\b(?:FROM|JOIN|USING)\s+({_IDENT})", re.I)
_CTE = re.compile(r"(?:\bWITH|,)\s*(?:RECURSIVE\s+)?([A-Za-z_]\w*)\s+AS\s*\(", re.I)
_KEYWORDS = {"select", "lateral", "unnest", "table", "values", "only"}


def _clean(ident: str) -> str:
    parts = [p.strip().strip('`"[]') for p in re.split(r"\s*\.\s*", ident)]
    return ".".join(parts)


def parse_sql_lineage(sql: str) -> list[tuple[list[str], str | None]]:
    """[(sources, target)] per statement. target is None for a bare SELECT
    (e.g. a view body, where the caller knows the target)."""
    out = []
    for stmt in _COMMENTS.sub(" ", sql).split(";"):
        if not stmt.strip():
            continue
        tm = _TARGET.search(stmt)
        target = _clean(tm.group(1)) if tm else None
        ctes = {c.lower() for c in _CTE.findall(stmt)}
        sources = []
        for m in _SOURCE.finditer(stmt):
            name = _clean(m.group(1))
            if name.lower() in ctes or name.lower() in _KEYWORDS:
                continue
            if target and name.lower() == target.lower():
                continue
            if name not in sources:
                sources.append(name)
        if sources or target:
            out.append((sources, target))
    return out


class LineageGraph:
    def __init__(self):
        self.down = defaultdict(set)
        self.up = defaultdict(set)
        self.nodes = {}           # node -> {"kind": asset|external, ...}
        self.edges = {}           # (a, b) -> via

    def add_node(self, node, **attrs):
        self.nodes.setdefault(node, {}).update(attrs)

    def add_edge(self, src, dst, via=""):
        if src == dst:
            return
        self.add_node(src)
        self.add_node(dst)
        self.down[src].add(dst)
        self.up[dst].add(src)
        self.edges.setdefault((src, dst), via)

    def _walk(self, start, graph):
        seen, q = [], deque(graph.get(start, ()))
        while q:
            n = q.popleft()
            if n in seen or n == start:
                continue
            seen.append(n)
            q.extend(graph.get(n, ()))
        return seen

    def downstream(self, node):
        return self._walk(node, self.down)

    def upstream(self, node):
        return self._walk(node, self.up)

    def to_dict(self):
        return {"nodes": [{"id": n, **a} for n, a in sorted(self.nodes.items())],
                "edges": [{"from": a, "to": b, "via": v} for (a, b), v in sorted(self.edges.items())]}

    def to_json(self):
        return json.dumps(self.to_dict(), indent=2)

    def to_mermaid(self, status: dict | None = None):
        status = status or {}
        ids = {n: f"n{i}" for i, n in enumerate(sorted(self.nodes))}
        lines = ["flowchart LR"]
        for n, i in ids.items():
            label = n.replace('"', "'")
            shape = f'(["{label}"])' if self.nodes[n].get("kind") == "external" else f'["{label}"]'
            lines.append(f"  {i}{shape}")
        for (a, b), via in sorted(self.edges.items()):
            lines.append(f"  {ids[a]} -->{'|' + via.replace('|', '/') + '|' if via else ''} {ids[b]}")
        for cls, color in (("fail", "#e5484d"), ("warn", "#f5a524"), ("pass", "#30a46c"), ("impacted", "#8e4ec6")):
            lines.append(f"  classDef {cls} stroke:{color},stroke-width:3px")
        for n, s in status.items():
            if n in ids:
                lines.append(f"  class {ids[n]} {s}")
        return "\n".join(lines)

    def to_dot(self):
        lines = ["digraph lineage {", "  rankdir=LR;"]
        for (a, b), via in sorted(self.edges.items()):
            lines.append(f'  "{a}" -> "{b}"' + (f' [label="{via}"]' if via else "") + ";")
        return "\n".join(lines + ["}"])


def build_lineage(project, connectors: dict | None = None, discover_views=True) -> LineageGraph:
    g = LineageGraph()
    by_table = {}  # (connection, table-name variants) -> asset name
    for a in project.assets.values():
        g.add_node(a.name, kind="asset", connection=a.connection, table=a.table, owner=a.owner)
        full = a.table.lower()
        by_table[(a.connection, full)] = a.name
        by_table[(a.connection, full.split(".")[-1])] = a.name

    def resolve(name, connection=None):
        key = name.lower()
        if name in project.assets:
            return name
        for k in (key, key.split(".")[-1]):
            if connection and (connection, k) in by_table:
                return by_table[(connection, k)]
        hits = {v for (c, t), v in by_table.items() if t in (key, key.split(".")[-1])}
        if len(hits) == 1:
            return hits.pop()
        node = f"{connection}:{name}" if connection else name
        g.add_node(node, kind="external")
        return node

    for e in project.lineage:  # declared: ingestion jobs, ERP extracts, BI dashboards...
        srcs = e["from"] if isinstance(e["from"], list) else [e["from"]]
        dsts = e["to"] if isinstance(e["to"], list) else [e["to"]]
        for s in srcs:
            for d in dsts:
                g.add_edge(resolve(s), resolve(d), e.get("via", "declared"))

    if project.transforms_dir and project.transforms_dir.exists():
        conn_hint = project.settings.get("transforms_connection")
        for f in sorted(project.transforms_dir.rglob("*.sql")):
            for sources, target in parse_sql_lineage(f.read_text()):
                if not target:
                    continue
                t = resolve(target, conn_hint)
                for s in sources:
                    g.add_edge(resolve(s, conn_hint), t, f.name)

    if discover_views and connectors:
        for cname, conn in connectors.items():
            try:
                views = conn.view_definitions()
            except Exception:  # noqa: BLE001 - lineage discovery is best-effort
                continue
            for view, sql in views.items():
                body = re.sub(r"^\s*CREATE\s+.*?\bAS\b", "", sql or "", count=1, flags=re.I | re.S)
                v = resolve(view, cname)
                for sources, _ in parse_sql_lineage(body):
                    for s in sources:
                        g.add_edge(resolve(s, cname), v, "view")
    return g


def to_svg(g: LineageGraph, status: dict | None = None) -> str:
    """Layered left-to-right DAG drawing with no external dependencies."""
    import html as _html
    status = status or {}
    nodes = sorted(g.nodes)
    if not nodes:
        return ""
    layer = {n: 0 for n in nodes}
    for _ in range(len(nodes)):  # longest-path layering (bounded for cycles)
        changed = False
        for (a, b) in g.edges:
            if layer[b] < layer[a] + 1 <= len(nodes):
                layer[b] = layer[a] + 1
                changed = True
        if not changed:
            break
    cols = defaultdict(list)
    for n in nodes:
        cols[layer[n]].append(n)
    pos_index = {}
    for L in sorted(cols):
        def bary(n):
            ps = [pos_index[p] for p in g.up.get(n, ()) if p in pos_index]
            return sum(ps) / len(ps) if ps else 0
        cols[L].sort(key=lambda n: (bary(n), n))
        for i, n in enumerate(cols[L]):
            pos_index[n] = i
    W, H, GX, GY, PAD = 200, 42, 64, 16, 12
    rows = max(len(v) for v in cols.values())
    width = PAD * 2 + len(cols) * W + (len(cols) - 1) * GX
    height = PAD * 2 + rows * H + (rows - 1) * GY
    xy = {}
    for L, ns in cols.items():
        offset = (rows - len(ns)) * (H + GY) / 2
        for i, n in enumerate(ns):
            xy[n] = (PAD + L * (W + GX), PAD + offset + i * (H + GY))
    parts = [f'<svg class="lineage" viewBox="0 0 {width} {height:.0f}" width="{width}" '
             f'role="img" aria-label="Lineage graph">',
             '<defs><marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
             'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" class="arrow"/></marker></defs>']
    for (a, b), via in sorted(g.edges.items()):
        (x1, y1), (x2, y2) = xy[a], xy[b]
        x1, y1, x2, y2 = x1 + W, y1 + H / 2, x2, y2 + H / 2
        mx = (x1 + x2) / 2
        parts.append(f'<path class="edge" d="M{x1:.0f},{y1:.0f} C{mx:.0f},{y1:.0f} {mx:.0f},{y2:.0f} {x2 - 2:.0f},{y2:.0f}" '
                     f'marker-end="url(#arr)"><title>{_html.escape(a)} → {_html.escape(b)}'
                     f'{" (" + _html.escape(via) + ")" if via else ""}</title></path>')
    for n in nodes:
        x, y = xy[n]
        st = status.get(n, "none")
        ext = g.nodes[n].get("kind") == "external"
        label = n if len(n) <= 26 else n[:25] + "…"
        parts.append(f'<g class="node {st}{" ext" if ext else ""}"><title>{_html.escape(n)}</title>'
                     f'<rect x="{x}" y="{y:.0f}" width="{W}" height="{H}" rx="{H / 2 if ext else 8}"/>'
                     f'<text x="{x + W / 2}" y="{y + H / 2 + 4.5:.0f}" text-anchor="middle">{_html.escape(label)}</text></g>')
    parts.append("</svg>")
    return "".join(parts)
