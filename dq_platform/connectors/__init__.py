from __future__ import annotations

from .base import Connector


def create_connector(name: str, settings: dict) -> Connector:
    kind = settings.get("type", "").lower()
    if kind == "sqlite":
        from .sqlite import SQLiteConnector
        return SQLiteConnector(name, settings)
    if kind in ("bigquery", "gcp"):
        from .bigquery import BigQueryConnector
        return BigQueryConnector(name, settings)
    if kind in ("files", "csv", "parquet"):
        from .files import FilesConnector
        return FilesConnector(name, settings)
    if kind in ("sqlalchemy", "postgres", "postgresql", "mysql", "mssql", "snowflake",
                "oracle", "redshift", "databricks", "hana", "sap_hana"):
        from .sqlalchemy_conn import SQLAlchemyConnector
        return SQLAlchemyConnector(name, settings)
    raise ValueError(f"connection {name!r}: unknown type {kind!r}")


__all__ = ["Connector", "create_connector"]
