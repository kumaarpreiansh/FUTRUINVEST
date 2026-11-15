/* dq-engine.js: in-browser edition of dq_platform.
 * The same YAML project format, check library, SQL pushdown, scoring, lineage
 * and profiling as the Python engine, running on SQLite compiled to WebAssembly.
 * Databases live in this browser tab; nothing is sent anywhere. */
(function (root) {
"use strict";

const DIMENSIONS = ["completeness", "uniqueness", "validity", "timeliness", "consistency", "integrity"];
const DEFAULT_DIMENSION = {
  not_null: "completeness", row_count: "completeness", volume_anomaly: "completeness",
  unique: "uniqueness", accepted_values: "validity", range: "validity", regex: "validity",
  expression: "validity", custom_sql: "validity", freshness: "timeliness",
  reconciliation: "consistency", referential_integrity: "integrity", schema: "integrity",
};
const PER_COLUMN = new Set(["not_null", "accepted_values", "range", "regex"]);
const FORMATS = {
  email: "^[^@\\s]+@[^@\\s]+\\.[A-Za-z]{2,}$",
  uuid: "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$",
  iso_country: "^[A-Z]{2}$", iso_currency: "^[A-Z]{3}$", digits: "^[0-9]+$", phone: "^\\+?[0-9 ()-]{7,20}$",
};

const PACKS = {
  customer_master: {
    fields: ["customer_id", "name", "country", "email", "currency", "created_on"],
    tables: { sap: "KNA1", oracle_ebs: "HZ_CUST_ACCOUNTS", dynamics365: "CUSTTABLE", generic: "customers" },
    mappings: {
      sap: { customer_id: "KUNNR", name: "NAME1", country: "LAND1", created_on: "ERDAT" },
      oracle_ebs: { customer_id: "ACCOUNT_NUMBER", created_on: "CREATION_DATE" },
      dynamics365: { customer_id: "ACCOUNTNUM", currency: "CURRENCY" },
      generic: { customer_id: "customer_id", name: "name", country: "country", email: "email", currency: "currency", created_on: "created_at" },
    },
    checks: [
      { id: "id_present", type: "not_null", column: "{customer_id}" },
      { id: "id_unique", type: "unique", column: "{customer_id}" },
      { id: "name_present", type: "not_null", column: "{name}", severity: "warn" },
      { id: "country_iso", type: "regex", column: "{country}", format: "iso_country", description: "ISO 3166-1 alpha-2 country code" },
      { id: "email_valid", type: "regex", column: "{email}", format: "email", max_failure_pct: 2, severity: "warn" },
      { id: "currency_iso", type: "regex", column: "{currency}", format: "iso_currency" },
    ],
  },
  sales_order: {
    fields: ["order_id", "customer_id", "order_date", "net_value", "currency"],
    tables: { sap: "VBAK", oracle_ebs: "OE_ORDER_HEADERS_ALL", dynamics365: "SALESTABLE", generic: "sales_orders" },
    mappings: {
      sap: { order_id: "VBELN", customer_id: "KUNNR", order_date: "ERDAT", net_value: "NETWR", currency: "WAERK" },
      oracle_ebs: { order_id: "HEADER_ID", customer_id: "SOLD_TO_ORG_ID", order_date: "ORDERED_DATE", currency: "TRANSACTIONAL_CURR_CODE" },
      dynamics365: { order_id: "SALESID", customer_id: "CUSTACCOUNT", currency: "CURRENCYCODE" },
      generic: { order_id: "order_id", customer_id: "customer_id", order_date: "order_date", net_value: "net_value", currency: "currency" },
    },
    checks: [
      { id: "id_present", type: "not_null", column: "{order_id}" },
      { id: "id_unique", type: "unique", column: "{order_id}" },
      { id: "customer_present", type: "not_null", column: "{customer_id}" },
      { id: "value_non_negative", type: "range", column: "{net_value}", min: 0, severity: "warn", description: "Negative net values are usually credit memos posted as orders" },
      { id: "currency_iso", type: "regex", column: "{currency}", format: "iso_currency" },
      { id: "fresh", type: "freshness", column: "{order_date}", max_age: "2d", severity: "warn" },
    ],
  },
};

// ------------------------------------------------------------------ SQL helpers (SQLite dialect)
const qi = s => '"' + String(s).replace(/"/g, '""') + '"';
const tbl = ref => String(ref).trim().startsWith("(") ? ref : String(ref).split(".").map(qi).join(".");
const lit = v => v === null || v === undefined ? "NULL" : typeof v === "boolean" ? (v ? "1" : "0")
  : typeof v === "number" ? String(v) : "'" + String(v).replace(/'/g, "''") + "'";
const where = (...cl) => { const p = cl.filter(Boolean).map(c => `(${c})`); return p.length ? " WHERE " + p.join(" AND ") : ""; };
const limit = (sql, n) => `${sql} LIMIT ${n | 0}`;
const toInt = v => Number(v || 0) | 0;

class CheckError extends Error {}

function parseDuration(t) {
  if (typeof t === "number") return t * 1000;
  const m = /^\s*(\d+(?:\.\d+)?)\s*([smhdw])\s*$/.exec(String(t));
  if (!m) throw new CheckError(`bad duration ${t}; use e.g. 30m, 6h, 2d, 1w`);
  return parseFloat(m[1]) * { s: 1e3, m: 6e4, h: 36e5, d: 864e5, w: 6048e5 }[m[2]];
}

function toDate(v) {
  if (v === null || v === undefined) return null;
  if (typeof v === "number") return new Date(v > 1e11 ? v : v * 1000);
  let s = String(v).trim();
  if (/^\d{8}$/.test(s)) s = `${s.slice(0, 4)}-${s.slice(4, 6)}-${s.slice(6)}`;
  if (/^\d{4}-\d{2}-\d{2}$/.test(s)) s += "T00:00:00";
  s = s.replace(" ", "T");
  if (!/(Z|[+-]\d{2}:?\d{2})$/.test(s)) s += "Z";
  const d = new Date(s);
  if (isNaN(d)) throw new CheckError(`cannot read ${v} as a timestamp`);
  return d;
}

// ------------------------------------------------------------------ connections
class Conn {
  constructor(name, db) { this.name = name; this.db = db; this.dialect = "sqlite"; }
  rows(sql) {
    const st = this.db.prepare(sql); const out = [];
    try { while (st.step()) out.push(st.get()); } finally { st.free(); }
    return out;
  }
  query(sql) {
    const st = this.db.prepare(sql); const out = [];
    try { while (st.step()) out.push(st.getAsObject()); } finally { st.free(); }
    return out;
  }
  scalar(sql) { const r = this.rows(sql); return r.length ? r[0][0] : null; }
  listTables() { return this.rows("SELECT name FROM sqlite_master WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' ORDER BY name").map(r => r[0]); }
  getColumns(t) { return this.query(`PRAGMA table_info(${qi(t)})`).map(r => [r.name, String(r.type || "").toUpperCase()]); }
  viewDefinitions() { const o = {}; for (const r of this.query("SELECT name, sql FROM sqlite_master WHERE type='view'")) o[r.name] = r.sql; return o; }
}

// ------------------------------------------------------------------ results store (in memory)
class Store {
  constructor() { this.runs = []; this.results = []; this.metrics = []; this.schemas = []; this.profiles = []; this.seq = 0; }
  startRun(project, scope = "all") {
    const now = new Date();
    const id = now.toISOString().replace(/[-:]/g, "").slice(0, 15) + "-" + Math.random().toString(16).slice(2, 8);
    this.runs.push({ run_id: id, project, scope, started_at: now.toISOString(), finished_at: null, status: "running", score: null, checks: 0, failed: 0, warned: 0, errored: 0, seq: ++this.seq });
    return id;
  }
  finishRun(id, status, score, c) {
    Object.assign(this.runs.find(r => r.run_id === id), { finished_at: new Date().toISOString(), status, score, checks: c.total || 0, failed: c.fail || 0, warned: c.warn || 0, errored: c.error || 0 });
  }
  saveResult(id, r) { this.results.push({ run_id: id, ...r, score: scoreOf(r), seq: ++this.seq }); }
  recordMetric(id, asset, metric, value) {
    this.metrics = this.metrics.filter(m => !(m.run_id === id && m.asset === asset && m.metric === metric));
    this.metrics.push({ run_id: id, asset, metric, value: Number(value), seq: ++this.seq });
  }
  metricHistory(asset, metric, n = 30, exclude = null) {
    return this.metrics.filter(m => m.asset === asset && m.metric === metric && m.run_id !== exclude).sort((a, b) => b.seq - a.seq).slice(0, n).map(m => m.value);
  }
  saveSchema(id, asset, cols) { this.schemas.push({ run_id: id, asset, cols, seq: ++this.seq }); }
  lastSchema(asset) { const s = this.schemas.filter(x => x.asset === asset).sort((a, b) => b.seq - a.seq)[0]; return s ? s.cols : null; }
  listRuns(n = 50) { return [...this.runs].sort((a, b) => b.seq - a.seq).slice(0, n); }
  scoreTrend(n = 30) { return this.runs.filter(r => r.scope === "all" && r.status !== "running").sort((a, b) => b.seq - a.seq).slice(0, n).reverse(); }
  runResults(id) { return this.results.filter(r => r.run_id === id); }
  currentResults(ids) {
    const seen = new Set(), out = [];
    for (const r of [...this.results].sort((a, b) => b.seq - a.seq)) {
      if (seen.has(r.check_id) || (ids && !ids.has(r.check_id))) continue;
      seen.add(r.check_id);
      out.push({ ...r, checked_at: (this.runs.find(x => x.run_id === r.run_id) || {}).started_at });
    }
    return out;
  }
}

function scoreOf(r) {
  if (r.status === "error" || r.status === "skipped") return null;
  if (r.total_rows && r.failed_rows !== null && r.failed_rows !== undefined) return Math.max(0, 1 - r.failed_rows / r.total_rows);
  return r.status === "pass" ? 1 : 0;
}
const mean100 = xs => { const v = xs.filter(x => x !== null && x !== undefined); return v.length ? Math.round(v.reduce((a, b) => a + b, 0) / v.length * 10000) / 100 : null; };

// ------------------------------------------------------------------ project config
function fill(t, map) {
  if (typeof t === "string") return t.replace(/\{([a-z_][a-z0-9_]*)\}/g, (_, k) => { if (!(k in map)) throw new Error(`pack field ${k} has no column mapping`); return map[k]; });
  if (Array.isArray(t)) return t.map(x => fill(x, map));
  if (t && typeof t === "object") return Object.fromEntries(Object.entries(t).map(([k, v]) => [k, fill(v, map)]));
  return t;
}

function expandPack(cfg) {
  const pack = PACKS[cfg.pack];
  if (!pack) throw new Error(`pack ${cfg.pack} not found; available: ${Object.keys(PACKS).join(", ")}`);
  const map = {};
  if (cfg.mapping) {
    if (!pack.mappings[cfg.mapping]) throw new Error(`pack ${cfg.pack} has no mapping ${cfg.mapping}; available: ${Object.keys(pack.mappings).join(", ")}`);
    Object.assign(map, pack.mappings[cfg.mapping]);
  }
  Object.assign(map, cfg.columns || {});
  const checks = [];
  for (const c of pack.checks) {
    const needed = [...JSON.stringify(c).matchAll(/\{([a-z_][a-z0-9_]*)\}/g)].map(m => m[1]);
    if (needed.some(f => !(f in map))) continue;
    checks.push(fill(JSON.parse(JSON.stringify(c)), map));
  }
  return { checks, table: cfg.mapping ? pack.tables[cfg.mapping] : null };
}

function loadProject(text) {
  const raw = (root.jsyaml.load(text) || {});
  if (typeof raw !== "object" || Array.isArray(raw)) throw new Error("the config must be a YAML mapping");
  const connections = raw.connections || {};
  for (const [n, c] of Object.entries(connections)) if (!c || !c.type) throw new Error(`source ${n}: type is required`);
  const assets = {};
  for (const [name, cfg0] of Object.entries(raw.assets || {})) {
    const cfg = cfg0 || {};
    let checks = [...(cfg.checks || [])], table = cfg.table;
    if (cfg.pack) { const p = expandPack(cfg); checks = [...p.checks, ...checks]; table = table || p.table; }
    if (!table) throw new Error(`asset ${name}: table is required`);
    if (!(cfg.connection in connections)) throw new Error(`asset ${name}: unknown connection ${cfg.connection}`);
    for (const c of checks) if (!c || !c.type) throw new Error(`asset ${name}: every check needs a type`);
    assets[name] = { name, connection: cfg.connection, table, description: cfg.description || "", owner: cfg.owner || "", tags: cfg.tags || [], filter: cfg.filter || null, checks };
  }
  for (const c of raw.checks || []) {
    const t = c.asset || c.source;
    if (!assets[t]) throw new Error(`check ${c.id}: unknown asset ${t}`);
    assets[t].checks.push(c);
  }
  return { name: raw.project || "project", connections, assets, lineage: raw.lineage || [], settings: raw.settings || {}, raw };
}

function expandChecks(asset) {
  const out = [], seen = {};
  for (const c0 of asset.checks) {
    const c = { ...c0 };
    if (PER_COLUMN.has(c.type) && Array.isArray(c.columns) && c.columns.length > 1) {
      const cols = c.columns, gid = c.id; delete c.columns; delete c.id;
      for (const col of cols) out.push({ ...c, column: col, ...(gid ? { id: `${gid}.${col}` } : {}) });
    } else out.push(c);
  }
  for (const c of out) {
    if (c.id && !String(c.id).startsWith(asset.name + ".")) c.id = `${asset.name}.${c.id}`;
    if (!c.id) {
      let col = c.column || (typeof c.columns === "string" ? c.columns : null);
      if (Array.isArray(c.columns) && c.type === "unique") col = c.columns.join("+");
      c.id = [asset.name, c.type || "?", ...(col ? [col] : []), ...(c.target ? [c.target] : [])].join(".");
    }
    const n = seen[c.id] || 0; seen[c.id] = n + 1;
    if (n) c.id = `${c.id}#${n + 1}`;
  }
  return out;
}

// ------------------------------------------------------------------ checks
const cols = c => { const v = c.columns || c.column; if (!v) throw new CheckError("`column` is required"); return Array.isArray(v) ? v : [v]; };
function within(c, failed, total) {
  const mf = c.max_failures, mp = c.max_failure_pct;
  if (mf == null && mp == null) return failed === 0;
  const pct = total ? failed / total * 100 : 0;
  return !((mf != null && failed > mf) || (mp != null && pct > mp));
}
function thresholdText(c) {
  const b = [];
  if (c.max_failures != null) b.push(`<= ${c.max_failures} failing rows`);
  if (c.max_failure_pct != null) b.push(`<= ${c.max_failure_pct}% failing rows`);
  return b.join(" and ") || "0 failing rows";
}
function rowLevel(ctx, a, c, pred) {
  const conn = ctx.conn(a), t = tbl(a.table);
  const sql = `SELECT COUNT(*) AS total_rows, SUM(CASE WHEN ${pred} THEN 1 ELSE 0 END) AS failed_rows FROM ${t} src${where(a.filter, c.filter)}`;
  const [total, failed] = conn.rows(sql)[0].map(toInt);
  const samples = failed && ctx.sampleLimit ? conn.query(limit(`SELECT * FROM ${t} src${where(a.filter, c.filter, pred)}`, ctx.sampleLimit)) : [];
  const ok = within(c, failed, total), pct = total ? failed / total * 100 : 0;
  return { ok, failed_rows: failed, total_rows: total, observed: `${failed} rows (${pct.toFixed(2)}%)`, expected: thresholdText(c), samples, sql,
    message: failed ? `${failed} of ${total} rows fail` + (ok ? " (within threshold)" : "") : `all ${total} rows pass` };
}
function distinctKeys(conn, a, col, maxKeys, extra) {
  const sql = `SELECT DISTINCT ${qi(col)} FROM ${tbl(a.table)} src${where(a.filter, extra, qi(col) + " IS NOT NULL")}`;
  const rows = conn.rows(limit(sql, maxKeys + 1));
  if (rows.length > maxKeys) throw new CheckError(`more than ${maxKeys} distinct keys in ${a.name}`);
  return new Set(rows.map(r => String(r[0])));
}
const METRIC = /^(count|sum|avg|min|max|count_distinct)(?::(.+))?$/;
function metricSql(m, map) {
  const x = METRIC.exec(m);
  if (!x) throw new CheckError(`bad metric ${m}; use count, sum:col, avg:col, min:col, max:col, count_distinct:col`);
  if (x[1] === "count") return "COUNT(*)";
  if (!x[2]) throw new CheckError(`metric ${x[1]} needs a column, e.g. ${x[1]}:amount`);
  const q = qi(map[x[2]] || x[2]);
  return x[1] === "count_distinct" ? `COUNT(DISTINCT ${q})` : `${x[1].toUpperCase()}(${q})`;
}

const CHECKS = {
  not_null(ctx, a, c) {
    const col = qi(cols(c)[0]);
    let pred = `${col} IS NULL`;
    if (c.blank_is_null !== false) pred += ` OR TRIM(CAST(${col} AS TEXT)) = ''`;
    return rowLevel(ctx, a, c, pred);
  },
  accepted_values(ctx, a, c) {
    const col = qi(cols(c)[0]);
    if (!c.values || !c.values.length) throw new CheckError("`values` is required");
    return rowLevel(ctx, a, c, `${col} IS NOT NULL AND ${col} NOT IN (${c.values.map(lit).join(", ")})`);
  },
  range(ctx, a, c) {
    const col = qi(cols(c)[0]), conds = [];
    if (c.min != null) conds.push(`${col} ${c.exclusive_min ? "<=" : "<"} ${lit(c.min)}`);
    if (c.max != null) conds.push(`${col} ${c.exclusive_max ? ">=" : ">"} ${lit(c.max)}`);
    if (!conds.length) throw new CheckError("`min` and/or `max` is required");
    return rowLevel(ctx, a, c, conds.join(" OR "));
  },
  regex(ctx, a, c) {
    const col = qi(cols(c)[0]), p = c.pattern || FORMATS[c.format || ""];
    if (!p) throw new CheckError(`\`pattern\` or a known \`format\` (${Object.keys(FORMATS).join(", ")}) is required`);
    return rowLevel(ctx, a, c, `${col} IS NOT NULL AND NOT (${col} REGEXP ${lit(p)})`);
  },
  expression(ctx, a, c) {
    if (!c.condition) throw new CheckError("`condition` is required");
    return rowLevel(ctx, a, c, `NOT (${c.condition})`);
  },
  unique(ctx, a, c) {
    const conn = ctx.conn(a), cs = cols(c).map(qi), list = cs.join(", "), t = tbl(a.table);
    const total = toInt(conn.scalar(`SELECT COUNT(*) FROM ${t} src${where(a.filter, c.filter)}`));
    const groups = `SELECT ${list}, COUNT(*) AS dq_n FROM ${t} src${where(a.filter, c.filter, cs.map(x => x + " IS NOT NULL").join(" AND "))} GROUP BY ${list} HAVING COUNT(*) > 1`;
    const sql = `SELECT COUNT(*) AS dup_keys, SUM(dq_n) AS dup_rows FROM (${groups}) dq_dups`;
    const [keys, rows] = conn.rows(sql)[0].map(toInt);
    const samples = keys && ctx.sampleLimit ? conn.query(limit(`${groups} ORDER BY COUNT(*) DESC`, ctx.sampleLimit)) : [];
    return { ok: within(c, rows, total), failed_rows: rows, total_rows: total, sql, samples, observed: `${keys} duplicated keys / ${rows} rows`, expected: thresholdText(c),
      message: keys ? `${keys} key value${keys > 1 ? "s appear" : " appears"} more than once (${rows} rows)` : `(${cols(c).join(", ")}) is unique across ${total} rows` };
  },
  referential_integrity(ctx, a, c) {
    const ref = c.references || {}, parent = ctx.assets[ref.asset];
    if (!parent) throw new CheckError("`references.asset` must name a known asset");
    const col = cols(c)[0], pcol = ref.column || col;
    if (a.connection === parent.connection) {
      const pred = `src.${qi(col)} IS NOT NULL AND NOT EXISTS (SELECT 1 FROM ${tbl(parent.table)} dq_p WHERE dq_p.${qi(pcol)} = src.${qi(col)}${parent.filter ? " AND (" + parent.filter + ")" : ""})`;
      const r = rowLevel(ctx, a, c, pred);
      r.message = r.failed_rows ? `${r.failed_rows} rows reference a missing ${parent.name}.${pcol}` : `all ${col} values exist in ${parent.name}.${pcol}`;
      return r;
    }
    const max = c.max_keys || 1000000;
    const ck = distinctKeys(ctx.conn(a), a, col, max, c.filter), pk = distinctKeys(ctx.conn(parent), parent, pcol, max);
    const orphans = [...ck].filter(k => !pk.has(k)).sort();
    return { ok: within(c, orphans.length, ck.size), failed_rows: orphans.length, total_rows: ck.size, observed: `${orphans.length} orphan keys`, expected: thresholdText(c),
      samples: orphans.slice(0, ctx.sampleLimit).map(k => ({ [col]: k })),
      message: orphans.length ? `${orphans.length} distinct ${col} values missing from ${parent.name}.${pcol} (cross-system: ${a.connection} → ${parent.connection})` : `all ${col} values exist in ${parent.name}.${pcol}` };
  },
  row_count(ctx, a, c) {
    const sql = `SELECT COUNT(*) FROM ${tbl(a.table)} src${where(a.filter, c.filter)}`;
    const n = toInt(ctx.conn(a).scalar(sql));
    ctx.store.recordMetric(ctx.runId, a.name, "row_count", n);
    const ok = (c.min == null || n >= c.min) && (c.max == null || n <= c.max);
    return { ok, observed: n, expected: [c.min != null ? `>= ${c.min}` : "", c.max != null ? `<= ${c.max}` : ""].filter(Boolean).join(" and ") || "any", sql, message: `${n} rows` };
  },
  freshness(ctx, a, c) {
    const col = qi(cols(c)[0]), maxAge = parseDuration(c.max_age || "24h");
    const sql = `SELECT MAX(${col}) FROM ${tbl(a.table)} src${where(a.filter, c.filter)}`;
    const latest = toDate(ctx.conn(a).scalar(sql));
    if (!latest) return { ok: false, observed: "no rows", expected: `newer than ${c.max_age || "24h"}`, sql, message: "table is empty, no timestamp to check" };
    const age = ctx.now - latest, h = age / 36e5;
    return { ok: age <= maxAge, observed: `${h.toFixed(1)}h old (latest ${latest.toISOString().slice(0, 19).replace("T", " ")} UTC)`, expected: `<= ${c.max_age || "24h"}`, sql,
      message: `latest ${cols(c)[0]} is ${h.toFixed(1)}h old` };
  },
  schema(ctx, a, c) {
    const actual = ctx.conn(a).getColumns(a.table);
    if (!actual.length) throw new CheckError(`table ${a.table} not found or has no columns`);
    const am = Object.fromEntries(actual.map(([n, t]) => [n.toLowerCase(), t]));
    const problems = [], drift = [], exp = c.columns || [];
    for (const n of exp) if (!(n.toLowerCase() in am)) problems.push(`missing column ${n}`);
    for (const [n, t] of Object.entries(c.types || {})) if (am[n.toLowerCase()] && !am[n.toLowerCase()].includes(String(t).toUpperCase())) problems.push(`${n} is ${am[n.toLowerCase()]}, expected ${t}`);
    if (exp.length && c.allow_extra === false) for (const n of Object.keys(am)) if (!exp.map(x => x.toLowerCase()).includes(n)) problems.push(`unexpected column ${n}`);
    if (c.detect_drift !== false) {
      const prev = ctx.store.lastSchema(a.name);
      if (prev) {
        const pm = Object.fromEntries(prev.map(([n, t]) => [n.toLowerCase(), t]));
        for (const n in am) if (!(n in pm)) drift.push(`column added: ${n}`);
        for (const n in pm) if (!(n in am)) drift.push(`column removed: ${n}`);
        for (const n in am) if (n in pm && pm[n] !== am[n]) drift.push(`type changed: ${n} ${pm[n]} → ${am[n]}`);
      }
      ctx.store.saveSchema(ctx.runId, a.name, actual);
    }
    const issues = [...problems, ...drift];
    return { ok: !issues.length, observed: `${actual.length} columns`, expected: "schema contract + no drift", samples: issues.map(i => ({ issue: i })),
      message: issues.length ? issues.join("; ") : `${actual.length} columns match contract, no drift` };
  },
  volume_anomaly(ctx, a, c) {
    const sql = `SELECT COUNT(*) FROM ${tbl(a.table)} src${where(a.filter, c.filter)}`;
    const n = toInt(ctx.conn(a).scalar(sql));
    const hist = ctx.store.metricHistory(a.name, "row_count", c.lookback || 30, ctx.runId);
    ctx.store.recordMetric(ctx.runId, a.name, "row_count", n);
    const minH = c.min_history || 5;
    if (hist.length < minH) return { skipped: true, observed: n, sql, message: `collecting baseline: ${hist.length}/${minH} previous runs` };
    const m = hist.reduce((x, y) => x + y, 0) / hist.length, sd = Math.sqrt(hist.reduce((x, y) => x + (y - m) ** 2, 0) / hist.length);
    const z = sd ? (n - m) / sd : 0, lim = c.z_threshold || 3;
    const ok = sd ? Math.abs(z) <= lim : Math.abs(n - m) / Math.max(m, 1) <= (c.flat_tolerance_pct || 5) / 100;
    return { ok, observed: `${n} rows (z=${z >= 0 ? "+" : ""}${z.toFixed(2)})`, expected: `|z| <= ${lim} vs mean ${m.toFixed(0)}`, sql, message: `${n} rows vs baseline ${m.toFixed(0)} ± ${sd.toFixed(0)}` };
  },
  reconciliation(ctx, a, c) {
    const target = ctx.assets[c.target];
    if (!target) throw new CheckError("`target` must name a known asset");
    const metrics = c.metrics || ["count"], map = c.column_map || {}, tol = Number(c.tolerance_pct || 0);
    const measure = (x, mp, f) => {
      const sql = `SELECT ${metrics.map((m, i) => `${metricSql(m, mp)} AS m${i}`).join(", ")} FROM ${tbl(x.table)} src${where(x.filter, f)}`;
      return [ctx.conn(x).rows(sql)[0], sql];
    };
    const [sv, sql] = measure(a, {}, c.filter), [tv] = measure(target, map, c.target_filter);
    const rows = []; let mism = 0;
    metrics.forEach((m, i) => {
      const s = sv[i], t = tv[i]; let d = null, ok;
      if (typeof s === "number" && typeof t === "number") { d = s === t ? 0 : Math.abs(s - t) / Math.max(Math.abs(s), 1e-12) * 100; ok = d <= tol; } else ok = s === t;
      if (!ok) mism++;
      rows.push({ metric: m, [a.name]: typeof s === "number" ? Math.round(s * 100) / 100 : s, [target.name]: typeof t === "number" ? Math.round(t * 100) / 100 : t, diff_pct: d === null ? null : Math.round(d * 1e4) / 1e4, ok });
    });
    let note = "";
    if (c.key) {
      const max = c.max_keys || 1000000;
      const sk = distinctKeys(ctx.conn(a), a, c.key, max, c.filter), tk = distinctKeys(ctx.conn(target), target, map[c.key] || c.key, max, c.target_filter);
      const missing = [...sk].filter(k => !tk.has(k)).sort(), extra = [...tk].filter(k => !sk.has(k)).sort();
      if (missing.length || extra.length) {
        mism++;
        rows.push({ metric: `keys missing in ${target.name}`, count: missing.length, examples: missing.slice(0, ctx.sampleLimit).join(", ") });
        rows.push({ metric: `keys only in ${target.name}`, count: extra.length, examples: extra.slice(0, ctx.sampleLimit).join(", ") });
      }
      note = `; ${missing.length} keys missing in target, ${extra.length} unexpected`;
    }
    return { ok: !mism, observed: `${mism} mismatched measures`, expected: `all measures within ${tol}%`, samples: rows, sql,
      message: mism ? `${a.name} vs ${target.name}: ${mism} mismatches${note}` : `${a.name} and ${target.name} reconcile on ${metrics.join(", ")}` };
  },
  custom_sql(ctx, a, c) {
    if (!c.sql) throw new CheckError("`sql` is required");
    const conn = ctx.conn(a), sql = c.sql.replace(/\{table\}/g, tbl(a.table)).trim().replace(/;$/, "");
    if (["min", "max", "equals"].some(k => k in c)) {
      const v = Number(conn.scalar(sql));
      const ok = (c.min == null || v >= c.min) && (c.max == null || v <= c.max) && (!("equals" in c) || v === c.equals);
      return { ok, observed: v, sql, message: `query returned ${v}`, expected: ["min", "max", "equals"].filter(k => k in c).map(k => `${k} ${c[k]}`).join(", ") };
    }
    const failed = toInt(conn.scalar(`SELECT COUNT(*) FROM (${sql}) dq_q`));
    return { ok: within(c, failed, failed), failed_rows: failed, observed: `${failed} rows`, expected: thresholdText(c), sql,
      samples: failed ? conn.query(limit(`SELECT * FROM (${sql}) dq_q`, ctx.sampleLimit)) : [],
      message: failed ? `query returned ${failed} failing row${failed > 1 ? "s" : ""}` : "query returned no failing rows" };
  },
};

// ------------------------------------------------------------------ lineage
const ATOM = '(?:`[^`]+`|"[^"]+"|\\[[^\\]]+\\]|[A-Za-z_][\\w$]*)';
const IDENT = ATOM + "(?:\\s*\\.\\s*" + ATOM + "){0,2}";
const RX_TARGET = new RegExp("\\b(?:CREATE\\s+(?:OR\\s+REPLACE\\s+)?(?:TEMP(?:ORARY)?\\s+)?(?:TABLE|VIEW|MATERIALIZED\\s+VIEW)(?:\\s+IF\\s+NOT\\s+EXISTS)?|INSERT\\s+(?:INTO|OVERWRITE)(?:\\s+TABLE)?|MERGE\\s+INTO)\\s+(" + IDENT + ")", "i");
const RX_SOURCE = new RegExp("\\b(?:FROM|JOIN|USING)\\s+(" + IDENT + ")", "gi");
const RX_CTE = /(?:\bWITH|,)\s*(?:RECURSIVE\s+)?([A-Za-z_]\w*)\s+AS\s*\(/gi;
const clean = id => id.split(/\s*\.\s*/).map(p => p.trim().replace(/^[`"[]|[`"\]]$/g, "")).join(".");

function parseSqlLineage(sql) {
  const out = [];
  for (const stmt of sql.replace(/--[^\n]*|\/\*[\s\S]*?\*\//g, " ").split(";")) {
    if (!stmt.trim()) continue;
    const tm = RX_TARGET.exec(stmt), target = tm ? clean(tm[1]) : null;
    const ctes = new Set([...stmt.matchAll(RX_CTE)].map(m => m[1].toLowerCase()));
    const sources = [];
    for (const m of stmt.matchAll(RX_SOURCE)) {
      const n = clean(m[1]), l = n.toLowerCase();
      if (ctes.has(l) || ["select", "lateral", "unnest", "table", "values", "only"].includes(l)) continue;
      if (target && l === target.toLowerCase()) continue;
      if (!sources.includes(n)) sources.push(n);
    }
    if (sources.length || target) out.push([sources, target]);
  }
  return out;
}

class Lineage {
  constructor() { this.nodes = {}; this.edges = new Map(); this.down = {}; this.up = {}; }
  addNode(n, attrs = {}) { this.nodes[n] = Object.assign(this.nodes[n] || {}, attrs); }
  addEdge(a, b, via = "") {
    if (a === b) return;
    this.addNode(a); this.addNode(b);
    (this.down[a] = this.down[a] || new Set()).add(b);
    (this.up[b] = this.up[b] || new Set()).add(a);
    const k = a + "\u0000" + b; if (!this.edges.has(k)) this.edges.set(k, via);
  }
  walk(start, g) {
    const seen = [], q = [...(g[start] || [])];
    while (q.length) { const n = q.shift(); if (seen.includes(n) || n === start) continue; seen.push(n); q.push(...(g[n] || [])); }
    return seen;
  }
  downstream(n) { return this.walk(n, this.down); }
  upstream(n) { return this.walk(n, this.up); }
}

function buildLineage(project, conns, transforms) {
  const g = new Lineage(), byTable = new Map();
  for (const a of Object.values(project.assets)) {
    g.addNode(a.name, { kind: "asset", connection: a.connection, table: a.table, owner: a.owner });
    const full = a.table.toLowerCase();
    byTable.set(a.connection + "|" + full, a.name);
    byTable.set(a.connection + "|" + full.split(".").pop(), a.name);
  }
  const resolve = (name, conn) => {
    if (project.assets[name]) return name;
    const k = name.toLowerCase(), short = k.split(".").pop();
    for (const x of [k, short]) if (conn && byTable.has(conn + "|" + x)) return byTable.get(conn + "|" + x);
    const hits = new Set([...byTable.entries()].filter(([kk]) => { const t = kk.split("|")[1]; return t === k || t === short; }).map(([, v]) => v));
    if (hits.size === 1) return [...hits][0];
    const node = conn ? `${conn}:${name}` : name;
    g.addNode(node, { kind: "external" });
    return node;
  };
  for (const e of project.lineage) {
    const fs = [].concat(e.from), ts = [].concat(e.to);
    for (const s of fs) for (const d of ts) g.addEdge(resolve(String(s)), resolve(String(d)), e.via || "declared");
  }
  const hint = project.settings.transforms_connection;
  for (const [file, sql] of Object.entries(transforms || {}))
    for (const [srcs, target] of parseSqlLineage(sql)) if (target) for (const s of srcs) g.addEdge(resolve(s, hint), resolve(target, hint), file);
  for (const [cname, conn] of Object.entries(conns)) {
    let views = {};
    try { views = conn.viewDefinitions(); } catch (e) { continue; }
    for (const [v, sql] of Object.entries(views)) {
      const body = String(sql || "").replace(/^\s*CREATE\s+[\s\S]*?\bAS\b/i, "");
      const vn = resolve(v, cname);
      for (const [srcs] of parseSqlLineage(body)) for (const s of srcs) g.addEdge(resolve(s, cname), vn, "view");
    }
  }
  return g;
}

function lineageSvg(g, status = {}) {
  const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const nodes = Object.keys(g.nodes).sort();
  if (!nodes.length || !g.edges.size) return "";
  const layer = Object.fromEntries(nodes.map(n => [n, 0]));
  const edges = [...g.edges.keys()].map(k => k.split("\u0000"));
  for (let i = 0; i < nodes.length; i++) {
    let ch = false;
    for (const [a, b] of edges) if (layer[b] < layer[a] + 1 && layer[a] + 1 <= nodes.length) { layer[b] = layer[a] + 1; ch = true; }
    if (!ch) break;
  }
  const colsL = {};
  for (const n of nodes) (colsL[layer[n]] = colsL[layer[n]] || []).push(n);
  const pos = {};
  for (const L of Object.keys(colsL).map(Number).sort((x, y) => x - y)) {
    const bary = n => { const ps = [...(g.up[n] || [])].filter(p => p in pos).map(p => pos[p]); return ps.length ? ps.reduce((x, y) => x + y, 0) / ps.length : 0; };
    colsL[L].sort((x, y) => bary(x) - bary(y) || x.localeCompare(y));
    colsL[L].forEach((n, i) => pos[n] = i);
  }
  const W = 196, H = 42, GX = 60, GY = 16, P = 12, nc = Object.keys(colsL).length;
  const rows = Math.max(...Object.values(colsL).map(v => v.length));
  const width = P * 2 + nc * W + (nc - 1) * GX, height = P * 2 + rows * H + (rows - 1) * GY;
  const xy = {};
  for (const [L, ns] of Object.entries(colsL)) { const off = (rows - ns.length) * (H + GY) / 2; ns.forEach((n, i) => xy[n] = [P + L * (W + GX), P + off + i * (H + GY)]); }
  const parts = [`<svg class="lineage" viewBox="0 0 ${width} ${height}" width="${width}" role="img" aria-label="Lineage graph"><defs><marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" class="arrow"/></marker></defs>`];
  for (const [k, via] of [...g.edges.entries()].sort()) {
    const [a, b] = k.split("\u0000");
    const x1 = xy[a][0] + W, y1 = xy[a][1] + H / 2, x2 = xy[b][0], y2 = xy[b][1] + H / 2, mx = (x1 + x2) / 2;
    parts.push(`<path class="edge" d="M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2 - 2},${y2}" marker-end="url(#arr)"><title>${esc(a)} → ${esc(b)}${via ? " (" + esc(via) + ")" : ""}</title></path>`);
  }
  for (const n of nodes) {
    const [x, y] = xy[n], ext = g.nodes[n].kind === "external", label = n.length <= 25 ? n : n.slice(0, 24) + "…";
    parts.push(`<g class="node ${status[n] || "none"}${ext ? " ext" : ""}" data-node="${esc(n)}"><title>${esc(n)}</title><rect x="${x}" y="${y}" width="${W}" height="${H}" rx="${ext ? H / 2 : 8}"/><text x="${x + W / 2}" y="${y + H / 2 + 4.5}" text-anchor="middle">${esc(label)}</text></g>`);
  }
  parts.push("</svg>");
  return parts.join("");
}

// ------------------------------------------------------------------ profiling
const NUMERIC = /INT|NUM|DEC|FLOAT|DOUBLE|REAL|MONEY/i, TEMPORAL = /DATE|TIME/i;
function profileAsset(conn, a) {
  const cs = conn.getColumns(a.table);
  if (!cs.length) throw new Error(`${a.table}: no columns found`);
  const t = tbl(a.table), w = a.filter ? ` WHERE ${a.filter}` : "";
  const ex = ["COUNT(*) AS dq_rows"];
  cs.forEach(([c, ty], i) => { const q = qi(c); ex.push(`COUNT(${q}) AS c${i}_nn`, `COUNT(DISTINCT ${q}) AS c${i}_nd`, `MIN(${q}) AS c${i}_min`, `MAX(${q}) AS c${i}_max`); if (NUMERIC.test(ty)) ex.push(`AVG(${q}) AS c${i}_avg`); });
  const r = conn.query(`SELECT ${ex.join(", ")} FROM ${t}${w}`)[0], total = toInt(r.dq_rows);
  const out = { asset: a.name, table: a.table, rows: total, columns: [] };
  cs.forEach(([c, ty], i) => {
    const nn = toInt(r[`c${i}_nn`]), nd = toInt(r[`c${i}_nd`]);
    const col = { name: c, type: ty, non_null: nn, nulls: total - nn, null_pct: total ? Math.round((total - nn) / total * 1e4) / 100 : 0, distinct: nd, min: r[`c${i}_min`], max: r[`c${i}_max`] };
    if (`c${i}_avg` in r) col.avg = r[`c${i}_avg`] === null ? null : Math.round(r[`c${i}_avg`] * 1e4) / 1e4;
    if (nd > 0 && nd <= 20) {
      col.top_values = conn.rows(`SELECT ${qi(c)}, COUNT(*) FROM ${t}${w} GROUP BY ${qi(c)} ORDER BY COUNT(*) DESC LIMIT 5`);
      col.values = conn.rows(`SELECT DISTINCT ${qi(c)} FROM ${t}${w}`).map(x => x[0]).filter(v => v !== null).sort();
    }
    out.columns.push(col);
  });
  return out;
}
function suggestChecks(p) {
  const rows = p.rows, out = [{ type: "row_count", min: 1 }, { type: "schema", columns: p.columns.map(c => c.name) }];
  if (rows >= 20) out.push({ type: "volume_anomaly", z_threshold: 3 });
  for (const c of p.columns) {
    const low = c.name.toLowerCase(), num = NUMERIC.test(c.type);
    if (rows && c.nulls === 0) out.push({ type: "not_null", column: c.name });
    else if (rows && c.null_pct < 5) out.push({ type: "not_null", column: c.name, max_failure_pct: Math.max(1, Math.round(c.null_pct * 20) / 10), severity: "warn" });
    const keyLike = /(^|_)(id|key|code|no|num|number)$/.test(low) || low.endsWith("id");
    if (rows && c.non_null && c.distinct === c.non_null && keyLike) out.push({ type: "unique", column: c.name });
    if (c.values && c.values.length > 1 && c.values.length <= 15 && rows >= 50 && !num) out.push({ type: "accepted_values", column: c.name, values: c.values });
    if (num && c.min !== null && Number(c.min) >= 0) out.push({ type: "range", column: c.name, min: 0 });
    if (low.includes("email")) out.push({ type: "regex", column: c.name, format: "email", max_failure_pct: 1 });
    if (["country", "country_code", "land1"].includes(low) || low.endsWith("_country")) out.push({ type: "regex", column: c.name, format: "iso_country" });
    if (["currency", "currency_code", "waerk", "curr"].includes(low)) out.push({ type: "regex", column: c.name, format: "iso_currency" });
    if (TEMPORAL.test(c.type) || /(updated|modified|created|loaded)(_at|_on|_date|_ts)?$/.test(low) || /_date$|_at$/.test(low))
      out.push({ type: "freshness", column: c.name, max_age: "24h", description: "Review the SLA. 24h is a suggested default." });
  }
  return out;
}

// ------------------------------------------------------------------ CSV import
function parseCsv(text) {
  const rows = []; let row = [], f = "", q = false;
  text = text.replace(/^﻿/, "");
  const delim = (() => { const line = text.split(/\r?\n/, 1)[0]; const cnt = d => line.split(d).length; return [",", ";", "\t", "|"].sort((a, b) => cnt(b) - cnt(a))[0]; })();
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (q) { if (ch === '"') { if (text[i + 1] === '"') { f += '"'; i++; } else q = false; } else f += ch; }
    else if (ch === '"') q = true;
    else if (ch === delim) { row.push(f); f = ""; }
    else if (ch === "\n" || ch === "\r") { if (ch === "\r" && text[i + 1] === "\n") i++; row.push(f); rows.push(row); row = []; f = ""; }
    else f += ch;
  }
  if (f !== "" || row.length) { row.push(f); rows.push(row); }
  return rows.filter(r => r.some(v => v !== ""));
}
function importCsv(db, tableName, text) {
  const rows = parseCsv(text);
  if (rows.length < 2) throw new Error("the file needs a header row and at least one data row");
  const used = {};
  const header = rows[0].map((h, i) => { let n = String(h || "").trim().replace(/[^\w]+/g, "_").replace(/^_+|_+$/g, "") || `column_${i + 1}`; if (used[n.toLowerCase()]) n += "_" + i; used[n.toLowerCase()] = 1; return n; });
  const data = rows.slice(1).map(r => header.map((_, i) => r[i] === undefined || r[i] === "" ? null : r[i]));
  const types = header.map((_, i) => {
    const vals = data.map(r => r[i]).filter(v => v !== null);
    if (vals.length && vals.every(v => /^-?\d+$/.test(v))) return "INTEGER";
    if (vals.length && vals.every(v => /^-?\d*\.?\d+(e[+-]?\d+)?$/i.test(v))) return "REAL";
    return "TEXT";
  });
  db.run(`DROP TABLE IF EXISTS ${qi(tableName)}`);
  db.run(`CREATE TABLE ${qi(tableName)} (${header.map((h, i) => `${qi(h)} ${types[i]}`).join(", ")})`);
  const st = db.prepare(`INSERT INTO ${qi(tableName)} VALUES (${header.map(() => "?").join(",")})`);
  db.run("BEGIN");
  for (const r of data) st.run(r.map((v, i) => v === null ? null : types[i] === "TEXT" ? v : Number(v)));
  db.run("COMMIT"); st.free();
  return { table: tableName, columns: header.length, rows: data.length };
}

// ------------------------------------------------------------------ demo data
const DEMO_YAML = `project: demo-erp-to-warehouse
settings:
  sample_rows: 5
  transforms_connection: warehouse

connections:
  erp:                        # stand-in for any ERP database (SAP, Oracle, Dynamics...)
    type: sqlite
    path: erp.db
  warehouse:                  # stand-in for BigQuery / Snowflake / Postgres
    type: sqlite
    path: warehouse.db

assets:
  erp_customers:
    connection: erp
    pack: customer_master       # ERP-agnostic rule pack...
    mapping: generic            # ...mapped onto this system (sap, oracle_ebs, dynamics365)
    owner: sales-ops
    tags: [erp, master-data]
    checks:
      - {type: row_count, min: 100}
      - type: schema
        columns: [customer_id, name, email, country, currency, created_at, status]
      - {type: accepted_values, column: status, values: [ACTIVE, BLOCKED, ARCHIVED]}

  erp_orders:
    connection: erp
    pack: sales_order
    mapping: generic
    owner: order-to-cash
    tags: [erp, transactions]
    checks:
      - {type: accepted_values, column: status, values: [OPEN, SHIPPED, INVOICED, CANCELLED]}
      - type: referential_integrity
        column: customer_id
        references: {asset: erp_customers, column: customer_id}
      - {type: volume_anomaly, z_threshold: 3, min_history: 5}

  erp_order_lines:
    connection: erp
    table: order_lines
    owner: order-to-cash
    tags: [erp, transactions]
    checks:
      - {type: unique, columns: [order_id, line_no]}
      - {type: not_null, columns: [order_id, product_id, quantity]}
      - {type: range, column: quantity, min: 1, max: 10000}
      - type: expression
        condition: "ABS(line_amount - quantity * unit_price) < 0.01"
        description: "Line amount must equal quantity x unit price"
      - type: referential_integrity
        column: order_id
        references: {asset: erp_orders, column: order_id}

  stg_orders:
    connection: warehouse
    table: stg_orders
    owner: data-platform
    tags: [warehouse]
    checks:
      - {type: freshness, column: _loaded_at, max_age: 24h}
      - type: schema
        columns: [order_id, customer_id, order_date, status, currency, net_value, _loaded_at]
        types: {order_id: INT, net_value: REAL}
      - type: referential_integrity         # cross-system: warehouse -> ERP
        column: customer_id
        references: {asset: erp_customers, column: customer_id}
        severity: warn

  stg_customers:
    connection: warehouse
    table: stg_customers
    owner: data-platform
    tags: [warehouse]

  fct_sales:
    connection: warehouse
    table: fct_sales
    owner: analytics
    tags: [warehouse, mart]
    checks:
      - {type: unique, column: order_id}
      - {type: not_null, columns: [order_id, country, net_value]}
      - type: custom_sql
        description: "No revenue booked against cancelled orders"
        sql: "SELECT order_id, net_value FROM {table} WHERE status = 'CANCELLED' AND net_value <> 0"
        severity: warn

  rpt_revenue_by_country:
    connection: warehouse
    table: rpt_revenue_by_country
    owner: finance
    checks:
      - {type: row_count, min: 1}

checks:   # system-integrity checks spanning two systems
  - id: erp_orders.reconcile_to_warehouse
    type: reconciliation
    source: erp_orders
    target: stg_orders
    metrics: [count, "sum:net_value", "count_distinct:customer_id"]
    key: order_id
  - id: stg_orders.reconcile_to_fct_sales
    type: reconciliation
    source: stg_orders
    target: fct_sales
    metrics: [count, "sum:net_value"]
    tolerance_pct: 0.1
    severity: warn
    description: "Orders lost in the stg -> fct join (e.g. unknown customers)"

lineage:   # edges no database can tell us about: ingestion jobs, BI tools
  - {from: erp_orders, to: stg_orders, via: nightly ERP extract}
  - {from: erp_customers, to: stg_customers, via: nightly ERP extract}
  - {from: erp_order_lines, to: "erp:billing_engine", via: CDC}
  - {from: rpt_revenue_by_country, to: "Finance dashboard (BI)", via: dashboard}
`;

const FCT_SALES_SQL = `-- Sales fact: one row per order, enriched with customer attributes.
CREATE TABLE fct_sales AS
WITH orders AS (
    SELECT order_id, customer_id, order_date, status, currency, net_value FROM stg_orders
)
SELECT o.order_id, o.order_date, o.status, o.currency, o.net_value,
       c.customer_id, c.country
FROM orders o
JOIN stg_customers c ON c.customer_id = o.customer_id;`;

const VIEW_SQL = `CREATE VIEW rpt_revenue_by_country AS
SELECT country, currency, COUNT(*) AS orders, SUM(net_value) AS revenue
FROM fct_sales
WHERE status <> 'CANCELLED'
GROUP BY country, currency`;

function rng(seed) {
  let a = seed >>> 0;
  const next = () => { a |= 0; a = a + 0x6D2B79F5 | 0; let t = Math.imul(a ^ a >>> 15, 1 | a); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; };
  return { next, int: (lo, hi) => lo + Math.floor(next() * (hi - lo + 1)), pick: xs => xs[Math.floor(next() * xs.length)], uniform: (lo, hi) => lo + next() * (hi - lo) };
}
const isoTs = d => d.toISOString().slice(0, 19).replace("T", " ");
const insertMany = (db, sql, rows) => { const st = db.prepare(sql); db.run("BEGIN"); for (const r of rows) st.run(r); db.run("COMMIT"); st.free(); };

function buildDemoDbs(SQL, seed = 7) {
  const R = rng(seed), now = new Date(Math.floor(Date.now() / 1000) * 1000), ago = ms => new Date(now - ms);
  const COUNTRIES = ["DE", "FR", "GB", "US", "IN", "NL", "ES", "IT", "SE", "JP"];
  const CUR = { DE: "EUR", FR: "EUR", NL: "EUR", ES: "EUR", IT: "EUR", GB: "GBP", US: "USD", IN: "INR", SE: "SEK", JP: "JPY" };
  const erp = new SQL.Database();
  erp.run(`CREATE TABLE customers (customer_id TEXT, name TEXT, email TEXT, country TEXT, currency TEXT, created_at TEXT, status TEXT);
    CREATE TABLE products (product_id TEXT PRIMARY KEY, name TEXT, unit_price REAL);
    CREATE TABLE sales_orders (order_id INTEGER, customer_id TEXT, order_date TEXT, status TEXT, currency TEXT, net_value REAL);
    CREATE TABLE order_lines (order_id INTEGER, line_no INTEGER, product_id TEXT, quantity INTEGER, unit_price REAL, line_amount REAL);`);
  const customers = [];
  for (let i = 1; i <= 240; i++) {
    const c = R.pick(COUNTRIES);
    customers.push([`C${String(i).padStart(5, "0")}`, `Customer ${i}`, `buyer${i}@example.com`, c, CUR[c], isoTs(ago(R.int(30, 900) * 864e5)), R.pick(["ACTIVE", "ACTIVE", "ACTIVE", "ACTIVE", "ACTIVE", "ACTIVE", "ACTIVE", "BLOCKED"])]);
  }
  for (const i of [5, 41, 77]) customers[i][2] = null;                                   // missing email
  for (const [i, bad] of [[12, "no-at-sign.example.com"], [90, "x@y"], [150, "sales@@corp.com"], [201, "n/a"]]) customers[i][2] = bad;
  customers[33][3] = "Germany"; customers[134][3] = "uk";                                  // country not ISO coded
  customers.push([...customers[60]]);                                                      // duplicated master record
  insertMany(erp, "INSERT INTO customers VALUES (?,?,?,?,?,?,?)", customers);
  const products = Array.from({ length: 40 }, (_, i) => [`P${String(i + 1).padStart(3, "0")}`, `Product ${i + 1}`, Math.round(R.uniform(5, 500) * 100) / 100]);
  insertMany(erp, "INSERT INTO products VALUES (?,?,?)", products);
  const orders = [], lines = [], valid = customers.slice(0, 240);
  for (let oid = 100001; oid <= 101200; oid++) {
    const cust = R.pick(valid), age = oid === 101200 ? 2 * 36e5 : R.int(180, 90 * 24 * 60) * 6e4;
    const status = R.pick(["OPEN", "SHIPPED", "SHIPPED", "INVOICED", "INVOICED", "INVOICED", "CANCELLED"]);
    let total = 0;
    for (let ln = 1, n = R.int(1, 4); ln <= n; ln++) {
      const p = R.pick(products), q = R.int(1, 20), amt = Math.round(q * p[2] * 100) / 100;
      lines.push([oid, ln, p[0], q, p[2], amt]); total += amt;
    }
    orders.push([oid, cust[0], isoTs(ago(age)), status, cust[4], status === "CANCELLED" ? 0 : Math.round(total * 100) / 100]);
  }
  for (const i of [17, 230, 611, 840, 1102]) orders[i][1] = `C9${String(i).padStart(4, "0")}`;   // orphan orders
  for (const i of [45, 700]) orders[i][5] = -Math.abs(orders[i][5]) || -10;              // credit memo posted as order
  orders[333][3] = "shpd";                                                                 // invalid status code
  orders[512][3] = "CANCELLED"; orders[512][5] = 99;                                       // revenue on a cancelled order
  for (const i of [8, 400, 950]) lines[i][5] = Math.round(lines[i][5] * 110) / 100;       // line amount != qty x price
  lines[77][3] = 0;                                                                        // zero quantity
  insertMany(erp, "INSERT INTO sales_orders VALUES (?,?,?,?,?,?)", orders);
  insertMany(erp, "INSERT INTO order_lines VALUES (?,?,?,?,?,?)", lines);

  const wh = new SQL.Database();
  wh.run(`CREATE TABLE stg_customers (customer_id TEXT, name TEXT, country TEXT, currency TEXT, _loaded_at TEXT);
    CREATE TABLE stg_orders (order_id INTEGER, customer_id TEXT, order_date TEXT, status TEXT, currency TEXT, net_value REAL, _loaded_at TEXT);`);
  const loaded = isoTs(ago(30 * 36e5));                                                    // last load 30h ago (SLA 24h)
  insertMany(wh, "INSERT INTO stg_customers VALUES (?,?,?,?,?)", customers.slice(0, 240).map(c => [c[0], c[1], c[3], c[4], loaded]));
  const newest = [...orders].sort((a, b) => a[2] < b[2] ? -1 : 1).slice(-6).map(o => o[0]);
  const dropped = new Set([...newest, orders[300][0], orders[301][0]]);                    // extract silently dropped orders
  insertMany(wh, "INSERT INTO stg_orders VALUES (?,?,?,?,?,?,?)", orders.filter(o => !dropped.has(o[0])).map(o => [...o, loaded]));
  wh.run(FCT_SALES_SQL); wh.run(VIEW_SQL);
  return { "erp.db": erp, "warehouse.db": wh };
}

const INCIDENTS = {
  schema_change: "ERP upgrade adds a column to the customer master",
  volume_drop: "Half of the ERP orders disappear",
  bad_batch: "A bad batch posts orders with invalid currency codes and negative values",
  fix_load: "Re-run the warehouse load: fresh timestamps, missing orders back",
};
function applyIncident(dbs, kind) {
  const erp = dbs["erp.db"], wh = dbs["warehouse.db"], now = isoTs(new Date());
  const c = new Conn("erp", erp);
  if (kind === "schema_change") { if (!c.getColumns("customers").some(x => x[0] === "vat_id")) erp.run("ALTER TABLE customers ADD COLUMN vat_id TEXT"); }
  else if (kind === "volume_drop") { const n = toInt(c.scalar("SELECT COUNT(*) FROM sales_orders")); erp.run(`DELETE FROM sales_orders WHERE order_id IN (SELECT order_id FROM sales_orders ORDER BY order_id DESC LIMIT ${n >> 1})`); }
  else if (kind === "bad_batch") {
    const start = toInt(c.scalar("SELECT MAX(order_id) FROM sales_orders")) + 1;
    insertMany(erp, "INSERT INTO sales_orders VALUES (?,?,?,?,?,?)", Array.from({ length: 12 }, (_, i) => [start + i, "C00010", now, "OPEN", i % 2 ? "EURO" : "eu", -50 * (i + 1)]));
  } else if (kind === "fix_load") {
    wh.run("DELETE FROM stg_orders");
    insertMany(wh, "INSERT INTO stg_orders VALUES (?,?,?,?,?,?,?)", c.rows("SELECT * FROM sales_orders").map(r => [...r, now]));
    wh.run(`UPDATE stg_customers SET _loaded_at = '${now}'`);
    wh.run("DROP VIEW IF EXISTS rpt_revenue_by_country; DROP TABLE IF EXISTS fct_sales;");
    wh.run(FCT_SALES_SQL); wh.run(VIEW_SQL);
  } else throw new Error(`unknown incident ${kind}`);
  return INCIDENTS[kind];
}

// ------------------------------------------------------------------ engine
const SERVER_ONLY = "Live connections to BigQuery and databases run in the server edition (python -m dq_platform serve). This browser prototype can only read the demo databases and files you upload.";

class Engine {
  constructor(SQL) { this.SQL = SQL; this.reset(); }
  reset() {
    this.dbs = buildDemoDbs(this.SQL);
    this.dbs.uploads = new this.SQL.Database();
    this.store = new Store();
    this.configText = DEMO_YAML;
    this.transforms = { "fct_sales.sql": FCT_SALES_SQL };
    for (const db of Object.values(this.dbs)) this._regexp(db);
  }
  _regexp(db) {
    const cache = {};
    db.create_function("REGEXP", (p, v) => v === null ? null : ((cache[p] = cache[p] || new RegExp(p)).test(String(v)) ? 1 : 0));
  }
  project() { return loadProject(this.configText); }
  openConn(name, cfg) {
    if (cfg.type === "sqlite" && this.dbs[cfg.path]) return new Conn(name, this.dbs[cfg.path]);
    if (cfg.type === "sqlite") throw new Error(`database file ${cfg.path} is not loaded in this browser. Upload it as CSV instead, or use the server edition.`);
    if (cfg.type === "files") return new Conn(name, this.dbs.uploads);
    throw new Error(SERVER_ONLY);
  }
  run({ assets = null, now = null } = {}) {
    const p = this.project(), store = this.store;
    const scope = assets && assets.length ? "partial: " + assets.join(", ") : "all";
    const runId = store.startRun(p.name, scope);
    const selected = Object.values(p.assets).filter(a => !assets || !assets.length || assets.includes(a.name));
    const conns = {}, connErr = {};
    for (const [n, cfg] of Object.entries(p.connections)) { try { conns[n] = this.openConn(n, cfg); } catch (e) { connErr[n] = e.message; } }
    const ctx = { assets: p.assets, store, runId, now: now || new Date(), sampleLimit: p.settings.sample_rows ?? 5, conn: a => conns[a.connection] };
    const results = [];
    for (const a of selected) for (const c of expandChecks(a)) {
      const r = { check_id: c.id, asset: a.name, type: c.type, dimension: c.dimension || DEFAULT_DIMENSION[c.type] || "validity", severity: (c.severity || "error").toLowerCase(), status: "error", message: "", description: c.description || "", observed: null, expected: null, failed_rows: null, total_rows: null, samples: [], sql: null, duration_ms: 0 };
      const t0 = performance.now();
      try {
        const needed = [a.connection, ...(c.target && p.assets[c.target] ? [p.assets[c.target].connection] : []), ...(c.references && p.assets[c.references.asset] ? [p.assets[c.references.asset].connection] : [])];
        const bad = needed.find(n => connErr[n]);
        if (bad) throw new CheckError(`source ${bad} unavailable: ${connErr[bad]}`);
        const fn = CHECKS[c.type];
        if (!fn) throw new CheckError(`unknown check type ${c.type}; available: ${Object.keys(CHECKS).sort().join(", ")}`);
        const o = fn(ctx, a, c);
        for (const k of ["observed", "expected", "failed_rows", "total_rows", "samples", "sql", "message"]) if (k in o) r[k] = o[k];
        r.status = o.skipped ? "skipped" : o.ok ? "pass" : r.severity === "warn" ? "warn" : "fail";
      } catch (e) { r.status = "error"; r.message = e.message; }
      r.duration_ms = Math.round(performance.now() - t0);
      store.saveResult(runId, r); results.push({ ...r, score: scoreOf(r) });
    }
    const counts = { total: results.length };
    for (const r of results) counts[r.status] = (counts[r.status] || 0) + 1;
    const status = ["fail", "error", "warn"].find(s => counts[s]) || "pass", score = mean100(results.map(r => r.score));
    store.finishRun(runId, status, score, counts);
    return { run_id: runId, status, score, counts, connection_errors: connErr };
  }
  lineage(p) {
    const conns = {};
    for (const [n, cfg] of Object.entries(p.connections)) { try { conns[n] = this.openConn(n, cfg); } catch (e) { /* lineage is best effort */ } }
    return buildLineage(p, conns, this.transforms);
  }
  current(p) {
    const ids = new Set(Object.values(p.assets).flatMap(a => expandChecks(a).map(c => c.id)));
    return this.store.currentResults(ids);
  }
  saveConfig(text) { loadProject(text); this.configText = text; }
  editRaw(fn) {
    const raw = root.jsyaml.load(this.configText) || {};
    fn(raw);
    this.saveConfig(root.jsyaml.dump(raw, { lineWidth: 110, noRefs: true }));
  }
}

const DQ = { Engine, loadProject, expandChecks, parseSqlLineage, buildLineage, lineageSvg, profileAsset, suggestChecks, importCsv, parseCsv,
  applyIncident, INCIDENTS, DIMENSIONS, FORMATS, CHECKS, PACKS, mean100, SERVER_ONLY };
root.DQ = DQ;
if (typeof module !== "undefined") module.exports = DQ;
})(typeof window !== "undefined" ? window : globalThis);
