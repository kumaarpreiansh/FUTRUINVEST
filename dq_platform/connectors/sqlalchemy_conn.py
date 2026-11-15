"""Any database with a SQLAlchemy driver: Postgres, MySQL, SQL Server, Oracle,
Snowflake, Redshift, Databricks, SAP HANA (the database under S/4HANA), Trino...

    connections:
      erp:
        type: sqlalchemy
        url: "mssql+pyodbc://${ERP_USER}:${ERP_PASSWORD}@erp-db/ERP?driver=ODBC+Driver+18+for+SQL+Server"
        schema: dbo            # optional default schema for discovery
"""

from __future__ import annotations

from .base import Connector

_SCHEME_DIALECT = {
    "postgresql": "postgresql", "postgres": "postgresql", "redshift": "redshift",
    "mysql": "mysql", "mariadb": "mysql", "mssql": "mssql", "oracle": "oracle",
    "snowflake": "snowflake", "databricks": "databricks", "hana": "hana",
    "bigquery": "bigquery", "sqlite": "sqlite", "trino": "generic", "duckdb": "duckdb",
}


class SQLAlchemyConnector(Connector):
    def __init__(self, name, settings):
        try:
            import sqlalchemy as sa
        except ImportError as e:  # pragma: no cover - optional dependency
            raise RuntimeError("pip install sqlalchemy (plus the driver for your database)") from e
        self._sa = sa
        url = settings["url"]
        scheme = url.split(":", 1)[0].split("+", 1)[0].lower()
        settings = {"dialect": _SCHEME_DIALECT.get(scheme, "generic"), **settings}
        super().__init__(name, settings)
        self.engine = sa.create_engine(url, **settings.get("engine_options", {}))
        self.schema = settings.get("schema")

    def query(self, sql):
        with self.engine.connect() as conn:
            result = conn.execute(self._sa.text(sql))
            return [dict(r._mapping) for r in result]

    def list_tables(self):
        insp = self._sa.inspect(self.engine)
        return sorted(insp.get_table_names(schema=self.schema) + insp.get_view_names(schema=self.schema))

    def get_columns(self, table):
        schema, _, tbl = table.rpartition(".")
        insp = self._sa.inspect(self.engine)
        cols = insp.get_columns(tbl, schema=schema or self.schema)
        return [(c["name"], str(c["type"]).upper()) for c in cols]

    def view_definitions(self):
        insp = self._sa.inspect(self.engine)
        out = {}
        for v in insp.get_view_names(schema=self.schema):
            try:
                out[v] = insp.get_view_definition(v, schema=self.schema) or ""
            except Exception:  # noqa: BLE001 - not every driver supports it
                continue
        return out

    def close(self):
        self.engine.dispose()
