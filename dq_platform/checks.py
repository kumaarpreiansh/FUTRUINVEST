"""Check library. Each check compiles to SQL for the asset's dialect and is
pushed down to the source, so nothing but counts and small samples leave it.

Row-level checks (not_null, accepted_values, range, regex, expression,
referential_integrity) share one evaluator: count rows matching a "failing"
predicate, compare against the threshold, pull a few example rows.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from .config import Asset, parse_duration

DIMENSIONS = ("completeness", "uniqueness", "validity", "timeliness", "consistency", "integrity")

DEFAULT_DIMENSION = {
    "not_null": "completeness", "row_count": "completeness", "volume_anomaly": "completeness",
    "unique": "uniqueness",
    "accepted_values": "validity", "range": "validity", "regex": "validity",
    "expression": "validity", "custom_sql": "validity",
    "freshness": "timeliness",
    "reconciliation": "consistency",
    "referential_integrity": "integrity", "schema": "integrity",
}

# Checks that take `columns: [a, b]` as shorthand for one check per column.
PER_COLUMN = {"not_null", "accepted_values", "range", "regex"}

FORMATS = {
    "email": r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$",
    "uuid": r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$",
    "iso_country": r"^[A-Z]{2}$",
    "iso_currency": r"^[A-Z]{3}$",
    "digits": r"^[0-9]+$",
    "phone": r"^\+?[0-9 ()-]{7,20}$",
}


@dataclass
class CheckResult:
    check_id: str
    asset: str
    type: str
    dimension: str
    severity: str
    status: str                  # pass | warn | fail | error | skipped
    message: str = ""
    observed: object = None
    expected: object = None
    failed_rows: int | None = None
    total_rows: int | None = None
    samples: list = field(default_factory=list)
    sql: str | None = None
    duration_ms: int = 0
    description: str = ""

    @property
    def score(self) -> float | None:
        """0..1 quality score; row-level checks score by share of good rows."""
        if self.status in ("error", "skipped"):
            return None
        if self.total_rows and self.failed_rows is not None:
            return max(0.0, 1 - self.failed_rows / self.total_rows)
        return 1.0 if self.status == "pass" else 0.0


class CheckError(Exception):
    pass


class Context:
    def __init__(self, connectors, assets, store=None, run_id=None, now=None, sample_limit=5):
        self.connectors = connectors
        self.assets = assets
        self.store = store
        self.run_id = run_id
        self.now = now or datetime.now(timezone.utc)
        self.sample_limit = sample_limit

    def conn(self, asset: Asset):
        return self.connectors[asset.connection]


# --------------------------------------------------------------------------- helpers

def _cols(cfg) -> list[str]:
    cols = cfg.get("columns") or cfg.get("column")
    if not cols:
        raise CheckError("`column` is required")
    return [cols] if isinstance(cols, str) else list(cols)


def _where(*clauses) -> str:
    parts = [f"({c})" for c in clauses if c]
    return (" WHERE " + " AND ".join(parts)) if parts else ""


def _first(row: dict):
    return list(row.values())


def _int(v) -> int:
    return int(v or 0)


def _within(cfg, failed: int, total: int) -> bool:
    max_f, max_pct = cfg.get("max_failures"), cfg.get("max_failure_pct")
    if max_f is None and max_pct is None:
        return failed == 0
    pct = (failed / total * 100) if total else 0.0
    if max_f is not None and failed > max_f:
        return False
    if max_pct is not None and pct > max_pct:
        return False
    return True


def _threshold_text(cfg) -> str:
    bits = []
    if cfg.get("max_failures") is not None:
        bits.append(f"<= {cfg['max_failures']} failing rows")
    if cfg.get("max_failure_pct") is not None:
        bits.append(f"<= {cfg['max_failure_pct']}% failing rows")
    return " and ".join(bits) or "0 failing rows"


def row_level(ctx: Context, asset: Asset, cfg: dict, predicate: str) -> dict:
    conn = ctx.conn(asset)
    tbl = conn.dialect.table(asset.table)
    base_filter = (asset.filter, cfg.get("filter"))
    sql = (f"SELECT COUNT(*) AS total_rows, "
           f"SUM(CASE WHEN {predicate} THEN 1 ELSE 0 END) AS failed_rows "
           f"FROM {tbl} src{_where(*base_filter)}")
    total, failed = (_int(v) for v in _first(conn.query(sql)[0]))
    samples = []
    if failed and ctx.sample_limit:
        samples = conn.query(conn.dialect.limit(
            f"SELECT * FROM {tbl} src{_where(*base_filter, predicate)}", ctx.sample_limit))
    ok = _within(cfg, failed, total)
    pct = (failed / total * 100) if total else 0.0
    return dict(ok=ok, failed_rows=failed, total_rows=total, observed=f"{failed} rows ({pct:.2f}%)",
                expected=_threshold_text(cfg), samples=samples, sql=sql,
                message=(f"{failed} of {total} rows fail" + (" (within threshold)" if ok else "")
                         if failed else f"all {total} rows pass"))


# --------------------------------------------------------------------------- checks

CHECKS = {}


def check(name):
    def deco(fn):
        CHECKS[name] = fn
        return fn
    return deco


@check("not_null")
def not_null(ctx, asset, cfg):
    d = ctx.conn(asset).dialect
    col = d.quote(_cols(cfg)[0])
    pred = f"{col} IS NULL"
    if cfg.get("blank_is_null", True):
        pred += f" OR {d.trim(d.as_text(col))} = ''"
    return row_level(ctx, asset, cfg, pred)


@check("accepted_values")
def accepted_values(ctx, asset, cfg):
    d = ctx.conn(asset).dialect
    col = d.quote(_cols(cfg)[0])
    values = cfg.get("values")
    if not values:
        raise CheckError("`values` is required")
    in_list = ", ".join(d.literal(v) for v in values)
    return row_level(ctx, asset, cfg, f"{col} IS NOT NULL AND {col} NOT IN ({in_list})")


@check("range")
def range_check(ctx, asset, cfg):
    d = ctx.conn(asset).dialect
    col = d.quote(_cols(cfg)[0])
    conds = []
    if cfg.get("min") is not None:
        conds.append(f"{col} {'<=' if cfg.get('exclusive_min') else '<'} {d.literal(cfg['min'])}")
    if cfg.get("max") is not None:
        conds.append(f"{col} {'>=' if cfg.get('exclusive_max') else '>'} {d.literal(cfg['max'])}")
    if not conds:
        raise CheckError("`min` and/or `max` is required")
    return row_level(ctx, asset, cfg, " OR ".join(conds))


@check("regex")
def regex(ctx, asset, cfg):
    d = ctx.conn(asset).dialect
    col = d.quote(_cols(cfg)[0])
    pattern = cfg.get("pattern") or FORMATS.get(cfg.get("format", ""))
    if not pattern:
        raise CheckError(f"`pattern` or a known `format` ({', '.join(FORMATS)}) is required")
    return row_level(ctx, asset, cfg, f"{col} IS NOT NULL AND NOT ({d.regex_match(col, pattern)})")


@check("expression")
def expression(ctx, asset, cfg):
    """Business rule that every row must satisfy, e.g. `net + tax = gross`."""
    cond = cfg.get("condition")
    if not cond:
        raise CheckError("`condition` is required")
    return row_level(ctx, asset, cfg, f"NOT ({cond})")


@check("unique")
def unique(ctx, asset, cfg):
    conn = ctx.conn(asset)
    d = conn.dialect
    cols = [d.quote(c) for c in _cols(cfg)]
    col_list = ", ".join(cols)
    tbl = d.table(asset.table)
    not_null = " AND ".join(f"{c} IS NOT NULL" for c in cols)
    where = _where(asset.filter, cfg.get("filter"), not_null)
    total = _int(conn.scalar(f"SELECT COUNT(*) FROM {tbl} src{_where(asset.filter, cfg.get('filter'))}"))
    groups = f"SELECT {col_list}, COUNT(*) AS dq_n FROM {tbl} src{where} GROUP BY {col_list} HAVING COUNT(*) > 1"
    sql = f"SELECT COUNT(*) AS dup_keys, SUM(dq_n) AS dup_rows FROM ({groups}) dq_dups"
    dup_keys, dup_rows = (_int(v) for v in _first(conn.query(sql)[0]))
    samples = []
    if dup_keys and ctx.sample_limit:
        samples = conn.query(d.limit(f"{groups} ORDER BY COUNT(*) DESC", ctx.sample_limit))
    ok = _within(cfg, dup_rows, total)
    return dict(ok=ok, failed_rows=dup_rows, total_rows=total, sql=sql, samples=samples,
                observed=f"{dup_keys} duplicated keys / {dup_rows} rows", expected=_threshold_text(cfg),
                message=(f"{dup_keys} key values appear more than once ({dup_rows} rows)"
                         if dup_keys else f"({', '.join(_cols(cfg))}) is unique across {total} rows"))


@check("referential_integrity")
def referential_integrity(ctx, asset, cfg):
    """Every child key must exist in the parent. Works across systems too
    (e.g. ERP order -> warehouse customer) by comparing key sets."""
    ref = cfg.get("references") or {}
    parent = ctx.assets.get(ref.get("asset"))
    if parent is None:
        raise CheckError("`references.asset` must name a known asset")
    col = _cols(cfg)[0]
    pcol = ref.get("column", col)
    child_conn, parent_conn = ctx.conn(asset), ctx.conn(parent)

    if asset.connection == parent.connection:
        d = child_conn.dialect
        pred = (f"src.{d.quote(col)} IS NOT NULL AND NOT EXISTS (SELECT 1 FROM {d.table(parent.table)} dq_p "
                f"WHERE dq_p.{d.quote(pcol)} = src.{d.quote(col)}{' AND (' + parent.filter + ')' if parent.filter else ''})")
        res = row_level(ctx, asset, cfg, pred)
        res["message"] = (f"{res['failed_rows']} rows reference a missing {parent.name}.{pcol}"
                          if res["failed_rows"] else f"all {col} values exist in {parent.name}.{pcol}")
        return res

    max_keys = int(cfg.get("max_keys", 1_000_000))
    child_keys = _distinct_keys(child_conn, asset, col, max_keys, cfg.get("filter"))
    parent_keys = _distinct_keys(parent_conn, parent, pcol, max_keys)
    orphans = sorted((k for k in child_keys - parent_keys), key=str)
    ok = _within(cfg, len(orphans), len(child_keys))
    return dict(ok=ok, failed_rows=len(orphans), total_rows=len(child_keys),
                observed=f"{len(orphans)} orphan keys", expected=_threshold_text(cfg),
                samples=[{col: k} for k in orphans[: ctx.sample_limit]],
                message=(f"{len(orphans)} distinct {col} values missing from {parent.name}.{pcol} "
                         f"(cross-system: {asset.connection} -> {parent.connection})"
                         if orphans else f"all {col} values exist in {parent.name}.{pcol}"))


def _norm_key(v):
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def _distinct_keys(conn, asset, col, max_keys, extra_filter=None) -> set:
    d = conn.dialect
    sql = (f"SELECT DISTINCT {d.quote(col)} AS k FROM {d.table(asset.table)} src"
           f"{_where(asset.filter, extra_filter, d.quote(col) + ' IS NOT NULL')}")
    rows = conn.query(d.limit(sql, max_keys + 1))
    if len(rows) > max_keys:
        raise CheckError(f"more than {max_keys} distinct keys in {asset.name}; raise max_keys or stage both "
                         "tables in one warehouse for a pushed-down join")
    return {_norm_key(_first(r)[0]) for r in rows}


@check("row_count")
def row_count(ctx, asset, cfg):
    conn = ctx.conn(asset)
    sql = f"SELECT COUNT(*) FROM {conn.dialect.table(asset.table)} src{_where(asset.filter, cfg.get('filter'))}"
    n = _int(conn.scalar(sql))
    if ctx.store:
        ctx.store.record_metric(ctx.run_id, asset.name, "row_count", n)
    lo, hi = cfg.get("min"), cfg.get("max")
    ok = (lo is None or n >= lo) and (hi is None or n <= hi)
    exp = " and ".join(x for x in (f">= {lo}" if lo is not None else "", f"<= {hi}" if hi is not None else "") if x)
    return dict(ok=ok, observed=n, expected=exp or "any", sql=sql, message=f"{n} rows")


def _to_datetime(v) -> datetime | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day, tzinfo=timezone.utc)
    if isinstance(v, (int, float)):  # epoch seconds (or ms)
        return datetime.fromtimestamp(v / 1000 if v > 1e11 else v, tz=timezone.utc)
    s = str(v).strip().replace("Z", "+00:00")
    if re.fullmatch(r"\d{8}", s):  # SAP-style YYYYMMDD
        s = f"{s[:4]}-{s[4:6]}-{s[6:]}"
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@check("freshness")
def freshness(ctx, asset, cfg):
    conn = ctx.conn(asset)
    col = conn.dialect.quote(_cols(cfg)[0])
    max_age = parse_duration(cfg.get("max_age", "24h"))
    sql = f"SELECT MAX({col}) FROM {conn.dialect.table(asset.table)} src{_where(asset.filter, cfg.get('filter'))}"
    latest = _to_datetime(conn.scalar(sql))
    if latest is None:
        return dict(ok=False, observed="no rows", expected=f"newer than {cfg.get('max_age', '24h')}",
                    sql=sql, message="table is empty, no timestamp to check")
    age = ctx.now - latest
    hours = age.total_seconds() / 3600
    return dict(ok=age <= max_age, observed=f"{hours:.1f}h old (latest {latest.isoformat()})",
                expected=f"<= {cfg.get('max_age', '24h')}", sql=sql,
                message=f"latest {cfg.get('column')} is {hours:.1f}h old")


def _type_matches(actual: str, expected: str) -> bool:
    return expected.upper() in actual.upper()


@check("schema")
def schema(ctx, asset, cfg):
    """Expected columns/types, plus drift detection against the last run."""
    actual = ctx.conn(asset).get_columns(asset.table)
    if not actual:
        raise CheckError(f"table {asset.table} not found or has no columns")
    actual_map = {c.lower(): t for c, t in actual}
    problems, drift = [], []

    expected_cols = cfg.get("columns") or []
    for c in expected_cols:
        if c.lower() not in actual_map:
            problems.append(f"missing column {c}")
    for c, t in (cfg.get("types") or {}).items():
        if c.lower() in actual_map and not _type_matches(actual_map[c.lower()], t):
            problems.append(f"{c} is {actual_map[c.lower()]}, expected {t}")
    if expected_cols and not cfg.get("allow_extra", True):
        extra = set(actual_map) - {c.lower() for c in expected_cols}
        problems += [f"unexpected column {c}" for c in sorted(extra)]

    if ctx.store and cfg.get("detect_drift", True):
        previous = ctx.store.last_schema(asset.name)
        if previous is not None:
            prev = {c.lower(): t for c, t in previous}
            drift += [f"column added: {c}" for c in actual_map if c not in prev]
            drift += [f"column removed: {c}" for c in prev if c not in actual_map]
            drift += [f"type changed: {c} {prev[c]} -> {actual_map[c]}"
                      for c in actual_map if c in prev and prev[c] != actual_map[c]]
        ctx.store.save_schema(ctx.run_id, asset.name, actual)

    issues = problems + drift
    return dict(ok=not issues, observed=f"{len(actual)} columns", expected="schema contract + no drift",
                samples=[{"issue": i} for i in issues],
                message="; ".join(issues) if issues else f"{len(actual)} columns match contract, no drift")


@check("volume_anomaly")
def volume_anomaly(ctx, asset, cfg):
    """Flags unusual row counts versus this asset's own history (z-score)."""
    conn = ctx.conn(asset)
    sql = f"SELECT COUNT(*) FROM {conn.dialect.table(asset.table)} src{_where(asset.filter, cfg.get('filter'))}"
    n = _int(conn.scalar(sql))
    if not ctx.store:
        raise CheckError("volume_anomaly needs a results store")
    history = ctx.store.metric_history(asset.name, "row_count", limit=int(cfg.get("lookback", 30)),
                                       exclude_run=ctx.run_id)
    ctx.store.record_metric(ctx.run_id, asset.name, "row_count", n)
    min_hist = int(cfg.get("min_history", 5))
    if len(history) < min_hist:
        return dict(skipped=True, observed=n, sql=sql,
                    message=f"collecting baseline: {len(history)}/{min_hist} previous runs")
    mean = sum(history) / len(history)
    sd = math.sqrt(sum((x - mean) ** 2 for x in history) / len(history))
    z = 0.0 if sd == 0 else (n - mean) / sd
    limit = float(cfg.get("z_threshold", 3.0))
    ok = abs(z) <= limit if sd else n == mean or abs(n - mean) / max(mean, 1) <= cfg.get("flat_tolerance_pct", 5) / 100
    return dict(ok=ok, observed=f"{n} rows (z={z:+.2f})", expected=f"|z| <= {limit} vs mean {mean:.0f}",
                sql=sql, message=f"{n} rows vs baseline {mean:.0f} ± {sd:.0f}")


_METRIC = re.compile(r"^(count|sum|avg|min|max|count_distinct)(?::(.+))?$")


def _metric_sql(d, metric: str, column_map: dict) -> str:
    m = _METRIC.match(metric)
    if not m:
        raise CheckError(f"bad metric {metric!r}; use count, sum:col, avg:col, min:col, max:col, count_distinct:col")
    fn, col = m.group(1), m.group(2)
    if fn == "count":
        return "COUNT(*)"
    if not col:
        raise CheckError(f"metric {fn} needs a column, e.g. {fn}:amount")
    qc = d.quote(column_map.get(col, col))
    return f"COUNT(DISTINCT {qc})" if fn == "count_distinct" else f"{fn.upper()}({qc})"


def _num(v):
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return v


@check("reconciliation")
def reconciliation(ctx, asset, cfg):
    """System-integrity check between two systems (ERP vs warehouse, source vs
    replica): compares aggregates and, optionally, the key sets themselves."""
    target = ctx.assets.get(cfg.get("target"))
    if target is None:
        raise CheckError("`target` must name a known asset")
    metrics = cfg.get("metrics", ["count"])
    col_map = cfg.get("column_map", {})
    tol = float(cfg.get("tolerance_pct", 0))

    def measure(a, mapping, extra_filter):
        conn = ctx.conn(a)
        d = conn.dialect
        exprs = ", ".join(f"{_metric_sql(d, m, mapping)} AS m{i}" for i, m in enumerate(metrics))
        sql = f"SELECT {exprs} FROM {d.table(a.table)} src{_where(a.filter, extra_filter)}"
        return [_num(v) for v in _first(conn.query(sql)[0])], sql

    src_vals, sql = measure(asset, {}, cfg.get("filter"))
    tgt_vals, _ = measure(target, col_map, cfg.get("target_filter"))
    rows, mismatches = [], 0
    for m, s, t in zip(metrics, src_vals, tgt_vals):
        if isinstance(s, float) and isinstance(t, float):
            diff_pct = 0.0 if s == t else abs(s - t) / max(abs(s), 1e-12) * 100
            ok = diff_pct <= tol
        else:
            diff_pct, ok = None, s == t
        mismatches += not ok
        rows.append({"metric": m, asset.name: s, target.name: t,
                     "diff_pct": None if diff_pct is None else round(diff_pct, 4), "ok": ok})

    key = cfg.get("key")
    key_note = ""
    if key:
        max_keys = int(cfg.get("max_keys", 1_000_000))
        sk = _distinct_keys(ctx.conn(asset), asset, key, max_keys, cfg.get("filter"))
        tk = _distinct_keys(ctx.conn(target), target, col_map.get(key, key), max_keys, cfg.get("target_filter"))
        missing, extra = sorted(sk - tk, key=str), sorted(tk - sk, key=str)
        if missing or extra:
            mismatches += 1
            rows.append({"metric": f"keys missing in {target.name}", "count": len(missing),
                         "examples": missing[:ctx.sample_limit]})
            rows.append({"metric": f"keys only in {target.name}", "count": len(extra),
                         "examples": extra[:ctx.sample_limit]})
        key_note = f"; {len(missing)} keys missing in target, {len(extra)} unexpected"

    return dict(ok=mismatches == 0, observed=f"{mismatches} mismatched measures",
                expected=f"all measures within {tol}%", samples=rows, sql=sql,
                message=(f"{asset.name} vs {target.name}: {mismatches} mismatches{key_note}"
                         if mismatches else f"{asset.name} and {target.name} reconcile on {', '.join(metrics)}"))


@check("custom_sql")
def custom_sql(ctx, asset, cfg):
    """Free-form SQL. `{table}` expands to the asset's table.
    With min/max/equals: the query returns one number to bound.
    Without: every row the query returns is a failure."""
    conn = ctx.conn(asset)
    d = conn.dialect
    sql = cfg.get("sql")
    if not sql:
        raise CheckError("`sql` is required")
    sql = sql.replace("{table}", d.table(asset.table)).strip().rstrip(";")
    if any(k in cfg for k in ("min", "max", "equals")):
        v = _num(conn.scalar(sql))
        ok = ((cfg.get("min") is None or v >= cfg["min"]) and (cfg.get("max") is None or v <= cfg["max"])
              and ("equals" not in cfg or v == cfg["equals"]))
        return dict(ok=ok, observed=v, sql=sql, message=f"query returned {v}",
                    expected=", ".join(f"{k} {cfg[k]}" for k in ("min", "max", "equals") if k in cfg))
    failed = _int(conn.scalar(f"SELECT COUNT(*) FROM ({sql}) dq_q"))
    samples = conn.query(d.limit(f"SELECT * FROM ({sql}) dq_q", ctx.sample_limit)) if failed else []
    return dict(ok=_within(cfg, failed, failed), failed_rows=failed, observed=f"{failed} rows",
                expected=_threshold_text(cfg), samples=samples, sql=sql,
                message=f"query returned {failed} failing rows" if failed else "query returned no failing rows")
