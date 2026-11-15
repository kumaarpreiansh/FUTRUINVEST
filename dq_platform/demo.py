"""Builds a self-contained demo: an "ERP" database, a "warehouse" fed from it,
SQL transforms, a BI view, and a project file — with realistic, seeded defects
so every kind of check has something to find."""

from __future__ import annotations

import random
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_YAML = """\
project: demo-erp-to-warehouse
store: .dq/results.db
transforms_dir: transforms
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
  # gcp:
  #   type: bigquery
  #   project: ${GCP_PROJECT}
  #   dataset: analytics

assets:
  erp_customers:
    connection: erp
    pack: customer_master       # ERP-agnostic rule pack...
    mapping: generic            # ...mapped onto this system's columns (sap, oracle_ebs, dynamics365)
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
    tolerance_pct: 0
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
"""

FCT_SALES_SQL = """\
-- Sales fact: one row per order, enriched with customer attributes.
CREATE TABLE fct_sales AS
WITH orders AS (
    SELECT order_id, customer_id, order_date, status, currency, net_value FROM stg_orders
)
SELECT o.order_id, o.order_date, o.status, o.currency, o.net_value,
       c.customer_id, c.country
FROM orders o
JOIN stg_customers c ON c.customer_id = o.customer_id;
"""

VIEW_SQL = """\
CREATE VIEW rpt_revenue_by_country AS
SELECT country, currency, COUNT(*) AS orders, SUM(net_value) AS revenue
FROM fct_sales
WHERE status <> 'CANCELLED'
GROUP BY country, currency
"""

COUNTRIES = ["DE", "FR", "GB", "US", "IN", "NL", "ES", "IT", "SE", "JP"]
CURRENCY = {"DE": "EUR", "FR": "EUR", "NL": "EUR", "ES": "EUR", "IT": "EUR", "GB": "GBP",
            "US": "USD", "IN": "INR", "SE": "SEK", "JP": "JPY"}


def build_demo(target: str | Path = "dq_demo", seed: int = 7, now: datetime | None = None) -> Path:
    rnd = random.Random(seed)
    now = now or datetime.now(timezone.utc).replace(microsecond=0)
    root = Path(target)
    (root / "transforms").mkdir(parents=True, exist_ok=True)
    for f in ("erp.db", "warehouse.db"):
        (root / f).unlink(missing_ok=True)

    iso = lambda dt: dt.strftime("%Y-%m-%d %H:%M:%S")  # noqa: E731

    # ---------------------------------------------------------------- ERP
    erp = sqlite3.connect(root / "erp.db")
    erp.executescript("""
        CREATE TABLE customers (customer_id TEXT, name TEXT, email TEXT, country TEXT,
                                currency TEXT, created_at TEXT, status TEXT);
        CREATE TABLE products (product_id TEXT PRIMARY KEY, name TEXT, unit_price REAL);
        CREATE TABLE sales_orders (order_id INTEGER, customer_id TEXT, order_date TEXT, status TEXT,
                                   currency TEXT, net_value REAL);
        CREATE TABLE order_lines (order_id INTEGER, line_no INTEGER, product_id TEXT,
                                  quantity INTEGER, unit_price REAL, line_amount REAL);
    """)
    customers = []
    for i in range(1, 241):
        cid = f"C{i:05d}"
        country = rnd.choice(COUNTRIES)
        customers.append([cid, f"Customer {i}", f"buyer{i}@example.com", country, CURRENCY[country],
                          iso(now - timedelta(days=rnd.randint(30, 900))), rnd.choice(["ACTIVE"] * 8 + ["BLOCKED"])])
    for i in (5, 41, 77):
        customers[i][2] = None                                   # missing email
    for i, bad in ((12, "no-at-sign.example.com"), (90, "x@y"), (150, "sales@@corp.com"), (201, "n/a")):
        customers[i][2] = bad                                     # malformed email
    customers[33][3] = "Germany"                                  # country not ISO coded
    customers[134][3] = "uk"
    customers.append(list(customers[60]))                         # duplicated master record
    erp.executemany("INSERT INTO customers VALUES (?,?,?,?,?,?,?)", customers)

    products = [(f"P{i:03d}", f"Product {i}", round(rnd.uniform(5, 500), 2)) for i in range(1, 41)]
    erp.executemany("INSERT INTO products VALUES (?,?,?)", products)

    orders, lines = [], []
    valid_customers = [c for c in customers[:240]]
    for oid in range(100001, 101201):
        cust = rnd.choice(valid_customers)
        age = timedelta(hours=2) if oid == 101200 else timedelta(minutes=rnd.randint(180, 90 * 24 * 60))
        status = rnd.choice(["OPEN", "SHIPPED", "SHIPPED", "INVOICED", "INVOICED", "INVOICED", "CANCELLED"])
        total = 0.0
        for ln in range(1, rnd.randint(1, 4) + 1):
            p = rnd.choice(products)
            qty = rnd.randint(1, 20)
            amt = round(qty * p[2], 2)
            lines.append([oid, ln, p[0], qty, p[2], amt])
            total += amt
        net = 0.0 if status == "CANCELLED" else round(total, 2)
        orders.append([oid, cust[0], iso(now - age), status, cust[4], net])
    for i in (17, 230, 611, 840, 1102):
        orders[i][1] = f"C9{i:04d}"                               # orphan: customer not in master
    for i in (45, 700):
        orders[i][5] = -abs(orders[i][5]) or -10.0                # credit memo posted as order
    orders[333][3] = "shpd"                                       # invalid status code
    orders[512][3], orders[512][5] = "CANCELLED", 99.0            # revenue on a cancelled order
    for i in (8, 400, 950):
        lines[i][5] = round(lines[i][5] * 1.1, 2)                 # line amount != qty x price
    lines[77][3] = 0                                              # zero quantity
    erp.executemany("INSERT INTO sales_orders VALUES (?,?,?,?,?,?)", orders)
    erp.executemany("INSERT INTO order_lines VALUES (?,?,?,?,?,?)", lines)
    erp.commit()
    erp.close()

    # ---------------------------------------------------------- warehouse
    wh = sqlite3.connect(root / "warehouse.db")
    wh.executescript("""
        CREATE TABLE stg_customers (customer_id TEXT, name TEXT, country TEXT, currency TEXT, _loaded_at TEXT);
        CREATE TABLE stg_orders (order_id INTEGER, customer_id TEXT, order_date TEXT, status TEXT,
                                 currency TEXT, net_value REAL, _loaded_at TEXT);
    """)
    loaded = iso(now - timedelta(hours=30))                         # last load ran 30h ago (SLA 24h)
    wh.executemany("INSERT INTO stg_customers VALUES (?,?,?,?,?)",
                   [(c[0], c[1], c[3], c[4], loaded) for c in customers[:240]])
    # the extract silently dropped the newest orders and a few others
    dropped = {o[0] for o in sorted(orders, key=lambda o: o[2])[-6:]} | {orders[300][0], orders[301][0]}
    wh.executemany("INSERT INTO stg_orders VALUES (?,?,?,?,?,?,?)",
                   [(*o, loaded) for o in orders if o[0] not in dropped])
    (root / "transforms" / "fct_sales.sql").write_text(FCT_SALES_SQL)
    wh.executescript(FCT_SALES_SQL)
    wh.executescript(VIEW_SQL)
    wh.commit()
    wh.close()

    (root / "dq_project.yml").write_text(PROJECT_YAML)
    return root / "dq_project.yml"


INCIDENTS = {
    "schema_change": "ERP upgrade adds a column to the customer master (schema drift)",
    "volume_drop": "Half of today's ERP orders disappear (volume anomaly + reconciliation)",
    "bad_batch": "A bad batch posts orders with invalid currency and negative values",
    "fix_load": "Re-run the warehouse load: fresh timestamps, missing orders back",
}


def apply_incident(root: str | Path, kind: str) -> str:
    """Mutate the demo databases to simulate a real-world data incident."""
    root = Path(root)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    erp = sqlite3.connect(root / "erp.db")
    wh = sqlite3.connect(root / "warehouse.db")
    try:
        if kind == "schema_change":
            cols = [r[1] for r in erp.execute("PRAGMA table_info(customers)")]
            if "vat_id" not in cols:
                erp.execute("ALTER TABLE customers ADD COLUMN vat_id TEXT")
        elif kind == "volume_drop":
            n = erp.execute("SELECT COUNT(*) FROM sales_orders").fetchone()[0]
            erp.execute("DELETE FROM sales_orders WHERE order_id IN "
                        "(SELECT order_id FROM sales_orders ORDER BY order_id DESC LIMIT ?)", (n // 2,))
        elif kind == "bad_batch":
            start = (erp.execute("SELECT MAX(order_id) FROM sales_orders").fetchone()[0] or 100000) + 1
            rows = [(start + i, "C00010", now, "OPEN", "EURO" if i % 2 else "eu", -50.0 * (i + 1))
                    for i in range(12)]
            erp.executemany("INSERT INTO sales_orders VALUES (?,?,?,?,?,?)", rows)
        elif kind == "fix_load":
            wh.execute("DELETE FROM stg_orders")
            erp_rows = erp.execute("SELECT * FROM sales_orders").fetchall()
            wh.executemany("INSERT INTO stg_orders VALUES (?,?,?,?,?,?,?)", [(*r, now) for r in erp_rows])
            wh.execute("UPDATE stg_customers SET _loaded_at = ?", (now,))
            wh.execute("DROP VIEW IF EXISTS rpt_revenue_by_country")
            wh.execute("DROP TABLE IF EXISTS fct_sales")
            wh.executescript(FCT_SALES_SQL)
            wh.executescript(VIEW_SQL)
        else:
            raise ValueError(f"unknown incident {kind!r}; choose from {', '.join(INCIDENTS)}")
        erp.commit()
        wh.commit()
    finally:
        erp.close()
        wh.close()
    return INCIDENTS[kind]
