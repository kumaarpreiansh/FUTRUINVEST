from __future__ import annotations

import re
import sqlite3

from .base import Connector


def _regexp(pattern, value):
    if value is None:
        return None
    return 1 if re.search(pattern, str(value)) else 0


class SQLiteConnector(Connector):
    dialect_name = "sqlite"

    def __init__(self, name, settings, conn: sqlite3.Connection | None = None):
        super().__init__(name, settings)
        if conn is None:
            path = settings.get("path", ":memory:")
            uri = path != ":memory:"
            target = f"file:{path}?mode=ro" if uri and settings.get("read_only", True) else path
            conn = sqlite3.connect(target, uri=uri and settings.get("read_only", True))
        self.conn = conn
        self.conn.row_factory = sqlite3.Row
        self.conn.create_function("REGEXP", 2, _regexp, deterministic=True)

    def query(self, sql):
        cur = self.conn.execute(sql)
        return [dict(r) for r in cur.fetchall()]

    def list_tables(self):
        rows = self.query(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view') "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name")
        return [r["name"] for r in rows]

    def get_columns(self, table):
        rows = self.query(f"PRAGMA table_info({self.dialect.quote(table)})")
        return [(r["name"], (r["type"] or "").upper()) for r in rows]

    def view_definitions(self):
        rows = self.query("SELECT name, sql FROM sqlite_master WHERE type = 'view'")
        return {r["name"]: r["sql"] for r in rows}

    def close(self):
        self.conn.close()
