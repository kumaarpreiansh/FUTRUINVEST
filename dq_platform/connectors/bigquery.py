"""Google BigQuery via the official client and Application Default Credentials.

    connections:
      gcp:
        type: bigquery
        project: my-gcp-project
        dataset: analytics          # default dataset for discovery / bare table names
        location: EU                # optional
        maximum_bytes_billed: 10000000000   # optional cost guard per query

Authenticate with `gcloud auth application-default login`, or set
GOOGLE_APPLICATION_CREDENTIALS to a service-account key (never commit it).
Tables are referenced as `dataset.table` or `project.dataset.table`.
"""

from __future__ import annotations

from .base import Connector


class BigQueryConnector(Connector):
    dialect_name = "bigquery"

    def __init__(self, name, settings):
        super().__init__(name, settings)
        try:
            from google.cloud import bigquery
        except ImportError as e:  # pragma: no cover - optional dependency
            raise RuntimeError("pip install google-cloud-bigquery") from e
        self._bq = bigquery
        self.project = settings.get("project")
        self.dataset = settings.get("dataset")
        self.client = bigquery.Client(project=self.project, location=settings.get("location"))

    def _qualify(self, table: str) -> str:
        parts = table.split(".")
        if len(parts) == 1 and self.dataset:
            parts = [self.dataset] + parts
        if len(parts) == 2 and self.project:
            parts = [self.project] + parts
        return ".".join(parts)

    def query(self, sql):
        cfg = self._bq.QueryJobConfig(
            maximum_bytes_billed=self.settings.get("maximum_bytes_billed"),
            default_dataset=f"{self.project}.{self.dataset}" if self.project and self.dataset else None,
        )
        rows = self.client.query(sql, job_config=cfg).result()
        return [dict(r.items()) for r in rows]

    def list_tables(self):
        if not self.dataset:
            raise ValueError(f"connection {self.name}: set `dataset` to list tables")
        return sorted(t.table_id for t in self.client.list_tables(f"{self.project}.{self.dataset}"))

    def get_columns(self, table):
        t = self.client.get_table(self._qualify(table))
        return [(f.name, f.field_type.upper()) for f in t.schema]

    def view_definitions(self):
        if not self.dataset:
            return {}
        rows = self.query(
            f"SELECT table_name, view_definition FROM "
            f"`{self.project}.{self.dataset}.INFORMATION_SCHEMA.VIEWS`")
        return {r["table_name"]: r["view_definition"] for r in rows}

    def close(self):
        self.client.close()
