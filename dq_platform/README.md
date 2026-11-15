# dq_platform: data quality, integrity and lineage

A data quality engine that works with any ERP and any warehouse. You declare
checks once in YAML. They are compiled to SQL for each source's dialect and
**run inside the database**, so only counts and a few sample rows come back.
It works the same way on BigQuery, Snowflake, Postgres, SQL Server, Oracle,
SAP HANA, an ERP's own database, or CSV/Parquet extracts.

```
 ERP DBs ─┐                                  ┌─► HTML report (offline, light/dark)
 BigQuery ─┤   YAML checks ─► SQL per dialect ─┤─► JSON results / CI exit code
 Snowflake ┤   (pushed down to the source)     ├─► results store: trends, baselines, drift
 Files ────┘                                  └─► lineage graph + downstream impact
```

## Browser edition (no install)

`browser/` holds a version that runs entirely in a web page. It uses the same
YAML project format and checks, scoring, lineage and profiling, on SQLite
compiled to WebAssembly (sql.js 1.10.2). It ships with the sample ERP and
warehouse, an incident simulator and CSV upload. It can't open live
warehouse connections, because a browser page can't reach databases; use the
server edition below for those. To run it locally, serve the folder
(`python -m http.server -d dq_platform/browser`) and open `index.html`.
Engine tests: `node dq_platform/browser/engine_test.js`.

## Run the prototype (web app)

```bash
pip install PyYAML
python -m dq_platform serve --demo        # then open http://127.0.0.1:8765
python -m dq_platform serve -p dq_project.yml   # your own project
```

Pages:
- **Overview**: quality score, failing checks, score by dimension, trend over full runs, open issues. In demo mode there are also buttons that simulate real incidents (ERP schema change, lost orders, a bad batch, a fixed load).
- **Issues**: filter by status, asset, dimension or text. Each issue shows observed vs expected, sample rows, the SQL that ran and downstream impact.
- **Assets**: score per asset. Open one to run it, add or remove checks, or **profile it and accept suggested checks** with a click.
- **Lineage**: graph coloured by health and impact. Click a node to see what feeds it and what it affects.
- **Sources**: add BigQuery, a database URL, files or SQLite, test the connection, browse tables and register them as assets.
- **History**: every run. Open one to see its results.
- **Config**: edit the project YAML in the browser. It is validated before saving.

Overview, Issues and Assets show the *current state*: the latest result of
every check, so running one asset never hides issues found elsewhere.
The server uses only the Python standard library and binds to localhost with
no login. Put it behind your SSO (e.g. IAP on Cloud Run) before sharing it.
Saving from the UI rewrites the YAML, which drops comments.

## Try it from the command line

```bash
pip install PyYAML
python -m dq_platform demo          # builds a sample ERP + warehouse with seeded defects, runs 36 checks
open dq_demo/reports/latest.html
```

The demo is a working pipeline: an ERP database, a nightly extract into a
warehouse, a SQL transform (`fct_sales`), a reporting view, and a BI dashboard.
It has realistic defects: duplicate customers, orders pointing at missing
customers, a stale load, orders dropped in the extract, line amounts that
don't add up, and a cancelled order carrying revenue. Every one of them is caught.

## What it checks

| Dimension | Check types |
|---|---|
| Completeness | `not_null` (blank strings count as null), `row_count`, `volume_anomaly` (z-score against the asset's own history) |
| Uniqueness | `unique` (single or composite keys) |
| Validity | `accepted_values`, `range`, `regex` (or `format: email / iso_country / iso_currency / uuid / phone`), `expression` (row-level business rules), `custom_sql` |
| Timeliness | `freshness` (`max_age: 6h / 2d`; understands timestamps, dates, epoch values and SAP `YYYYMMDD`) |
| Consistency (system integrity) | `reconciliation`: compares counts, sums and distinct counts between two systems, with optional key-level diff |
| Integrity | `referential_integrity` (same database as an SQL join; **across systems** as a key-set diff), `schema` (contract + automatic drift detection) |

Each check has a `severity` (`error`/`warn`) and a tolerance (`max_failures`,
`max_failure_pct`), and can take an optional `filter`. Every result records
what was observed, what was expected, sample failing rows and the exact SQL.
Scores are reported per check, per asset, per dimension and per run.

## ERP-agnostic packs

The rules for an entity are written once against logical field names, then
mapped to each ERP's own columns:

```yaml
assets:
  customers:
    connection: s4hana
    pack: customer_master
    mapping: sap            # -> table KNA1, customer_id=KUNNR, country=LAND1, ...
  customers_ebs:
    connection: oracle_erp
    pack: customer_master
    mapping: oracle_ebs     # -> HZ_CUST_ACCOUNTS
```

The included packs are `customer_master` and `sales_order`, each mapped for
SAP, Oracle EBS, Dynamics 365 and a generic schema. If a field doesn't exist in
a given ERP, the checks that need it are skipped. Use `columns:` to override
individual mappings, or add your own packs under `pack_dirs`. The mappings are
starter templates: check them against your release and any customisations.

## Connecting sources

```yaml
connections:
  gcp:
    type: bigquery
    project: ${GCP_PROJECT}          # ${VAR} reads from the environment; never put secrets in YAML
    dataset: analytics
    maximum_bytes_billed: 10000000000
  erp:
    type: sqlalchemy
    url: "mssql+pyodbc://${ERP_USER}:${ERP_PASSWORD}@erp-db/ERP?driver=ODBC+Driver+18+for+SQL+Server"
  snowflake:
    type: sqlalchemy
    url: "snowflake://${SF_USER}:${SF_PASSWORD}@${SF_ACCOUNT}/DB/SCHEMA?warehouse=DQ_WH"
  extracts:
    type: files
    path: ./exports
    pattern: "*.parquet"
```

BigQuery uses Application Default Credentials (`gcloud auth application-default login`
or a service account). Grant the account read-only roles. The platform only
ever issues `SELECT` statements.

## Lineage

The graph is built from three sources:
1. **Declared edges** in YAML, for things no database can see: ingestion jobs, CDC, BI dashboards.
2. **SQL transforms**: `CREATE TABLE … AS`, `INSERT INTO`, `MERGE` statements in `transforms_dir` (CTEs are ignored).
3. **Database views**: definitions read from each connection (SQLite, SQLAlchemy inspectors, BigQuery `INFORMATION_SCHEMA.VIEWS`).

When a check fails, the report lists every downstream asset and dashboard
affected, and colours the lineage graph to match.

## Commands

```bash
python -m dq_platform run -p dq_project.yml [--asset X] [--tag erp] [--json out.json] [--fail-on error|warn|never]
python -m dq_platform discover -p dq_project.yml <connection> [--table T]
python -m dq_platform profile  -p dq_project.yml --asset X      # profile + suggested checks as YAML
python -m dq_platform lineage  -p dq_project.yml [--asset X] [--format mermaid|json|dot]
python -m dq_platform history  -p dq_project.yml
python -m dq_platform serve    -p dq_project.yml [--port 8765] [--host 127.0.0.1]
```

`run` exits with code 1 when checks fail, so it can be dropped into CI, Airflow
or Cloud Composer, or a Cloud Run job on Cloud Scheduler, as a quality gate.

## Layout

```
dq_platform/
  connectors/   sqlite, sqlalchemy (most databases), bigquery, files
  dialects.py   quoting, regex, LIMIT/TOP, casts per SQL dialect
  checks.py     the check library
  config.py     project YAML, ${ENV} secrets, pack expansion
  runner.py     execute, score, store, attach lineage impact
  lineage.py    SQL lineage parser, graph, impact queries, SVG/Mermaid/DOT
  profiler.py   column profiling + rule suggestions
  store.py      run history (SQLite): results, metrics, schema snapshots
  report.py     HTML + JSON reports
  packs/        ERP-agnostic rule packs
  demo.py       sample ERP -> warehouse project + incident simulator
  web.py        JSON API + server for the web app
  ui/           single-page web UI (no build step, no CDN)
  browser/      browser edition: JS engine + UI on SQLite/WebAssembly
```

## Known limits / next steps

- Lineage is table-level. Column-level lineage would need a full SQL parser (e.g. sqlglot).
- Cross-system key diffs load distinct keys into memory (capped by `max_keys`, default 1M).
  For larger tables, stage both sides in one warehouse so the comparison runs as a SQL join.
- Checks run one after another. Running them in parallel per connection is a straightforward next step.
- The BigQuery, SQLAlchemy and file connectors are written against their public
  APIs but are only exercised by the tests via SQLite. Validate them against your
  own systems before relying on them.
- Planned: alert routing (Slack, email), scheduled runs, user logins and roles, and an OpenLineage export.
