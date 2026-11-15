"""Column profiling (pushed down as one aggregate query per table) and
rule suggestions derived from the profile — a starting point, reviewed by a human."""

from __future__ import annotations

import re

import yaml

_NUMERIC = re.compile(r"INT|NUM|DEC|FLOAT|DOUBLE|REAL|BIGNUMERIC|MONEY", re.I)
_TEMPORAL = re.compile(r"DATE|TIME", re.I)


def profile_asset(conn, asset, top_n=5, distinct_values_limit=20) -> dict:
    d = conn.dialect
    cols = conn.get_columns(asset.table)
    if not cols:
        raise ValueError(f"{asset.table}: no columns found")
    tbl = d.table(asset.table)
    where = f" WHERE {asset.filter}" if asset.filter else ""
    exprs = ["COUNT(*) AS dq_rows"]
    for i, (c, t) in enumerate(cols):
        q = d.quote(c)
        exprs += [f"COUNT({q}) AS c{i}_nn", f"COUNT(DISTINCT {q}) AS c{i}_nd",
                  f"MIN({q}) AS c{i}_min", f"MAX({q}) AS c{i}_max"]
        if _NUMERIC.search(t):
            exprs.append(f"AVG({q}) AS c{i}_avg")
    row = {k.lower(): v for k, v in conn.query(f"SELECT {', '.join(exprs)} FROM {tbl}{where}")[0].items()}
    total = int(row["dq_rows"] or 0)
    out = {"asset": asset.name, "table": asset.table, "rows": total, "columns": []}
    for i, (c, t) in enumerate(cols):
        nn, nd = int(row[f"c{i}_nn"] or 0), int(row[f"c{i}_nd"] or 0)
        col = {"name": c, "type": t, "non_null": nn, "nulls": total - nn,
               "null_pct": round((total - nn) / total * 100, 2) if total else 0.0,
               "distinct": nd, "distinct_pct": round(nd / nn * 100, 2) if nn else 0.0,
               "min": row[f"c{i}_min"], "max": row[f"c{i}_max"]}
        if f"c{i}_avg" in row:
            col["avg"] = None if row[f"c{i}_avg"] is None else round(float(row[f"c{i}_avg"]), 4)
        if 0 < nd <= distinct_values_limit:
            q = d.quote(c)
            top = conn.query(d.limit(
                f"SELECT {q} AS v, COUNT(*) AS n FROM {tbl}{where} GROUP BY {q} ORDER BY COUNT(*) DESC", top_n))
            col["top_values"] = [list(r.values()) for r in top]
            if nd <= distinct_values_limit:
                vals = conn.query(f"SELECT DISTINCT {q} AS v FROM {tbl}{where}")
                col["values"] = sorted((list(r.values())[0] for r in vals if list(r.values())[0] is not None), key=str)
        out["columns"].append(col)
    return out


def suggest_checks(profile: dict) -> list[dict]:
    rows = profile["rows"]
    out = [{"type": "row_count", "min": 1}, {"type": "schema", "columns": [c["name"] for c in profile["columns"]]}]
    if rows >= 20:
        out.append({"type": "volume_anomaly", "z_threshold": 3})
    for c in profile["columns"]:
        name, low, t = c["name"], c["name"].lower(), c["type"]
        if c["nulls"] == 0 and rows:
            out.append({"type": "not_null", "column": name})
        elif c["null_pct"] < 5 and rows:
            out.append({"type": "not_null", "column": name, "max_failure_pct": max(1.0, round(c["null_pct"] * 2, 1)),
                        "severity": "warn"})
        if rows and c["non_null"] and c["distinct"] == c["non_null"] and (low.endswith("id") or low in ("key", "code")
                                                                           or c["distinct"] == rows):
            out.append({"type": "unique", "column": name})
        if "values" in c and 1 < len(c["values"]) <= 15 and rows >= 50 and not _NUMERIC.search(t):
            out.append({"type": "accepted_values", "column": name, "values": c["values"]})
        if _NUMERIC.search(t) and c["min"] is not None:
            if float(c["min"]) >= 0:
                out.append({"type": "range", "column": name, "min": 0})
        if "email" in low:
            out.append({"type": "regex", "column": name, "format": "email", "max_failure_pct": 1})
        if low in ("country", "country_code", "land1") or low.endswith("_country"):
            out.append({"type": "regex", "column": name, "format": "iso_country"})
        if low in ("currency", "currency_code", "waerk", "curr"):
            out.append({"type": "regex", "column": name, "format": "iso_currency"})
        if _TEMPORAL.search(t) or re.search(r"(updated|modified|created|loaded)(_at|_on|_date|_ts)?$", low):
            out.append({"type": "freshness", "column": name, "max_age": "24h",
                        "description": "review the SLA — suggested default"})
    return out


def suggestions_yaml(profile: dict) -> str:
    body = {"assets": {profile["asset"]: {"checks": suggest_checks(profile)}}}
    return ("# Suggested from profile — review before adopting. Thresholds are starting points.\n"
            + yaml.safe_dump(body, sort_keys=False))
