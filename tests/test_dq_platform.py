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
