"""Connector contract. Every source — warehouse, ERP database, files — implements this."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..dialects import Dialect, get_dialect


class Connector(ABC):
    dialect_name = "generic"

    def __init__(self, name: str, settings: dict):
        self.name = name
        self.settings = settings
        self.dialect: Dialect = get_dialect(settings.get("dialect") or self.dialect_name)

    @abstractmethod
    def query(self, sql: str) -> list[dict]:
        """Run a read-only query, return rows as dicts."""

    def scalar(self, sql: str):
        rows = self.query(sql)
        if not rows:
            return None
        return next(iter(rows[0].values()))

    @abstractmethod
    def list_tables(self) -> list[str]:
        ...

    @abstractmethod
    def get_columns(self, table: str) -> list[tuple[str, str]]:
        """[(column_name, data_type), ...] in table order."""

    def view_definitions(self) -> dict[str, str]:
        """{view_name: SQL} — used for automatic lineage. Optional."""
        return {}

    def close(self) -> None:
        pass

    def __repr__(self):
        return f"<{type(self).__name__} {self.name} ({self.dialect.name})>"
