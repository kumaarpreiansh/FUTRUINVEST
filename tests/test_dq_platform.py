import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dq_platform.config import expand_pack, parse_duration
from dq_platform.demo import build_demo
from dq_platform.dialects import get_dialect
from dq_platform.lineage import parse_sql_lineage
from dq_platform.report import to_html, to_json
from dq_platform.runner import run_project
from dq_platform.store import Store


class TestDemoRun(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.project = build_demo(Path(cls.tmp.name) / "demo")
        cls.report = run_project(cls.project)
        cls.by_id = {r.check_id: r for r in cls.report.results}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def status(self, check_id):
        return self.by_id[check_id].status

    def test_no_execution_errors(self):
        errors = [(r.check_id, r.message) for r in self.report.results if r.status == "error"]
        self.assertEqual(errors, [])

    def test_seeded_defects_are_found(self):
        self.assertEqual(self.status("erp_customers.id_unique"), "fail")
        self.assertEqual(self.by_id["erp_customers.country_iso"].failed_rows, 2)
        self.assertEqual(self.by_id["erp_orders.referential_integrity.customer_id"].failed_rows, 5)
        self.assertEqual(self.by_id["erp_order_lines.expression"].failed_rows, 3 + 1)  # 3 bad amounts + zero qty row
        self.assertEqual(self.status("stg_orders.freshness._loaded_at"), "fail")
        self.assertEqual(self.status("erp_orders.reconcile_to_warehouse"), "fail")
        self.assertEqual(self.status("stg_orders.referential_integrity.customer_id"), "warn")

    def test_clean_checks_pass(self):
        for cid in ("erp_orders.id_unique", "erp_order_lines.unique.order_id+line_no",
                    "erp_orders.fresh", "fct_sales.unique.order_id"):
            self.assertEqual(self.status(cid), "pass", cid)

    def test_threshold_allows_some_failures(self):
        r = self.by_id["erp_customers.email_valid"]
        self.assertEqual((r.status, r.failed_rows), ("pass", 4))

    def test_lineage_impact(self):
        self.assertIn("rpt_revenue_by_country", self.report.impact["stg_orders"])
        self.assertIn("Finance dashboard (BI)", self.report.lineage.downstream("erp_orders"))
        self.assertIn("stg_orders", self.report.lineage.upstream("fct_sales"))  # parsed from SQL file
        self.assertIn("fct_sales", self.report.lineage.upstream("rpt_revenue_by_country"))  # from view

    def test_scores_and_reports(self):
        self.assertTrue(0 < self.report.score < 100)
        html = to_html(self.report)
        self.assertIn("svg class=\"lineage\"", html)
        self.assertIn("erp_customers.id_unique", to_json(self.report))

    def test_schema_drift_and_volume_anomaly(self):
        root = self.project.parent
        for _ in range(5):
            run_project(self.project)
        con = sqlite3.connect(root / "erp.db")
        con.execute("ALTER TABLE customers ADD COLUMN vat_id TEXT")
        con.execute("DELETE FROM sales_orders WHERE order_id > 100600")
        con.commit()
        con.close()
        rep = run_project(self.project)
        by = {r.check_id: r for r in rep.results}
        self.assertEqual(by["erp_customers.schema"].status, "fail")
        self.assertIn("vat_id", by["erp_customers.schema"].message)
        self.assertEqual(by["erp_orders.volume_anomaly"].status, "fail")
        st = Store(root / ".dq/results.db")
        self.assertGreaterEqual(len(st.runs()), 7)
        st.close()


class TestUnits(unittest.TestCase):
    def test_sql_lineage_parser(self):
        sql = """WITH x AS (SELECT * FROM raw.orders) -- comment FROM nope
                 INSERT INTO mart.fct SELECT * FROM x JOIN `proj.ds.customers` c ON 1=1"""
        self.assertEqual(parse_sql_lineage(sql), [(["raw.orders", "proj.ds.customers"], "mart.fct")])

    def test_dialect_quoting(self):
        self.assertEqual(get_dialect("bigquery").table("p.d.t"), "`p`.`d`.`t`")
        self.assertEqual(get_dialect("mssql").limit("SELECT a FROM t", 5), "SELECT TOP 5 a FROM t")
        self.assertIn("REGEXP_CONTAINS", get_dialect("bigquery").regex_match("`c`", "^a$"))

    def test_pack_maps_to_sap(self):
        checks, table = expand_pack({"pack": "customer_master", "mapping": "sap"})
        self.assertEqual(table, "KNA1")
        cols = {c.get("column") for c in checks}
        self.assertIn("KUNNR", cols)
        self.assertNotIn("{email}", cols)  # SAP mapping has no email -> check skipped

    def test_duration(self):
        self.assertEqual(parse_duration("2d").days, 2)


if __name__ == "__main__":
    unittest.main()


class TestWebApi(unittest.TestCase):
    """Drives the web API the UI uses, against a live server on a free port."""

    @classmethod
    def setUpClass(cls):
        import threading
        from http.server import ThreadingHTTPServer
        from dq_platform.web import App, make_handler, seed_demo
        cls.tmp = tempfile.TemporaryDirectory()
        demo = Path(cls.tmp.name) / "demo"
        project = seed_demo(demo, baseline_runs=2)
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(App(project, demo)))
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.tmp.cleanup()

    def call(self, path, method="GET", body=None):
        import json
        import urllib.error
        import urllib.request
        req = urllib.request.Request(self.base + path, method=method,
                                     data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_ui_served(self):
        import urllib.request
        with urllib.request.urlopen(self.base + "/") as r:
            self.assertIn(b"DQ Platform", r.read())

    def test_workflow(self):
        s, o = self.call("/api/overview")
        self.assertEqual(s, 200)
        self.assertEqual(o["assets"], 7)
        self.assertEqual(self.call("/api/demo/incident", "POST", {"kind": "bad_batch"})[0], 200)
        s, run = self.call("/api/run", "POST", {})
        self.assertEqual((s, run["status"]), (200, "fail"))
        s, tables = self.call("/api/connections/erp/tables")
        self.assertIn("products", [t["table"] for t in tables])
        s, _ = self.call("/api/assets", "POST", {"name": "erp_products", "connection": "erp", "table": "products"})
        self.assertEqual(s, 200)
        s, prof = self.call("/api/profile", "POST", {"asset": "erp_products"})
        picks = [x["check"] for x in prof["suggestions"] if not x["exists"]]
        self.assertNotIn(("unique", "name"), [(c["type"], c.get("column")) for c in picks])
        self.assertEqual(self.call("/api/assets/erp_products/checks", "POST", {"checks": picks})[0], 200)
        s, part = self.call("/api/run", "POST", {"assets": ["erp_products"]})
        self.assertEqual(part["status"], "pass")
        # a partial run must not hide issues found by the full run
        s, cur = self.call("/api/runs/latest")
        self.assertTrue(any(r["check_id"] == "erp_orders.currency_iso" and r["status"] == "fail" for r in cur["results"]))
        self.assertIn("svg", cur["lineage_svg"])
        s, err = self.call("/api/config", "PUT", {"text": "assets:\n  x: {connection: nope, table: t}\n"})
        self.assertEqual(s, 400)
        self.assertEqual(self.call("/api/overview")[1]["assets"], 8)  # bad config was not saved
