"""SQL dialect differences the check compiler needs to know about."""

from __future__ import annotations


class Dialect:
    name = "generic"
    quote_open = '"'
    quote_close = '"'

    def quote(self, identifier: str) -> str:
        ident = identifier.replace(self.quote_close, self.quote_close * 2)
        return f"{self.quote_open}{ident}{self.quote_close}"

    def table(self, ref: str) -> str:
        """Quote a dotted table reference part by part (project.dataset.table)."""
        if ref.strip().startswith("("):  # inline subquery: use as-is
            return ref
        return ".".join(self.quote(p) for p in ref.split("."))

    def literal(self, value) -> str:
        if value is None:
            return "NULL"
        if isinstance(value, bool):
            return "TRUE" if value else "FALSE"
        if isinstance(value, (int, float)):
            return repr(value)
        return "'" + str(value).replace("'", "''") + "'"

    def regex_match(self, expr: str, pattern: str) -> str:
        raise NotImplementedError(f"regex checks are not supported for dialect {self.name}")

    def limit(self, sql: str, n: int) -> str:
        return f"{sql} LIMIT {int(n)}"

    def trim(self, expr: str) -> str:
        return f"TRIM({expr})"

    def as_text(self, expr: str) -> str:
        return f"CAST({expr} AS VARCHAR)"


class SQLiteDialect(Dialect):
    name = "sqlite"

    def regex_match(self, expr, pattern):  # REGEXP is registered by the connector
        return f"{expr} REGEXP {self.literal(pattern)}"

    def as_text(self, expr):
        return f"CAST({expr} AS TEXT)"


class PostgresDialect(Dialect):
    name = "postgresql"

    def regex_match(self, expr, pattern):
        return f"CAST({expr} AS TEXT) ~ {self.literal(pattern)}"

    def as_text(self, expr):
        return f"CAST({expr} AS TEXT)"


class RedshiftDialect(PostgresDialect):
    name = "redshift"


class MySQLDialect(Dialect):
    name = "mysql"
    quote_open = quote_close = "`"

    def regex_match(self, expr, pattern):
        return f"{expr} REGEXP {self.literal(pattern)}"

    def literal(self, value):
        if isinstance(value, str):
            return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"
        return super().literal(value)

    def as_text(self, expr):
        return f"CAST({expr} AS CHAR)"


class BigQueryDialect(Dialect):
    name = "bigquery"
    quote_open = quote_close = "`"

    def literal(self, value):
        if isinstance(value, str):
            return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"
        return super().literal(value)

    def regex_match(self, expr, pattern):
        return f"REGEXP_CONTAINS(CAST({expr} AS STRING), {self.literal(pattern)})"

    def as_text(self, expr):
        return f"CAST({expr} AS STRING)"


class _BackslashEscapes:
    """Dialects where a backslash inside a string literal is an escape character."""

    def literal(self, value):
        if isinstance(value, str):
            return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"
        return super().literal(value)


class SnowflakeDialect(_BackslashEscapes, Dialect):
    name = "snowflake"

    def regex_match(self, expr, pattern):
        # REGEXP_LIKE is anchored in Snowflake; wrap to get "contains" semantics.
        return f"REGEXP_INSTR(CAST({expr} AS VARCHAR), {self.literal(pattern)}) > 0"


class DatabricksDialect(_BackslashEscapes, Dialect):
    name = "databricks"
    quote_open = quote_close = "`"

    def regex_match(self, expr, pattern):
        return f"CAST({expr} AS STRING) RLIKE {self.literal(pattern)}"

    def as_text(self, expr):
        return f"CAST({expr} AS STRING)"


class MSSQLDialect(Dialect):
    name = "mssql"
    quote_open, quote_close = "[", "]"

    def limit(self, sql, n):
        s = sql.lstrip()
        if s.upper().startswith("SELECT DISTINCT "):
            return f"SELECT DISTINCT TOP {int(n)} " + s[len("SELECT DISTINCT "):]
        if s.upper().startswith("SELECT "):
            return f"SELECT TOP {int(n)} " + s[len("SELECT "):]
        return sql

    def trim(self, expr):
        return f"LTRIM(RTRIM({expr}))"

    def as_text(self, expr):
        return f"CAST({expr} AS NVARCHAR(4000))"


class OracleDialect(Dialect):
    name = "oracle"

    def limit(self, sql, n):
        return f"{sql} FETCH FIRST {int(n)} ROWS ONLY"

    def regex_match(self, expr, pattern):
        return f"REGEXP_LIKE({expr}, {self.literal(pattern)})"

    def as_text(self, expr):
        return f"TO_CHAR({expr})"


class HanaDialect(Dialect):
    """SAP HANA (S/4HANA, BW/4HANA)."""
    name = "hana"

    def regex_match(self, expr, pattern):
        return f"{expr} LIKE_REGEXPR {self.literal(pattern)}"

    def as_text(self, expr):
        return f"TO_NVARCHAR({expr})"


_DIALECTS = {
    d.name: d for d in (
        Dialect, SQLiteDialect, PostgresDialect, RedshiftDialect, MySQLDialect,
        BigQueryDialect, SnowflakeDialect, DatabricksDialect, MSSQLDialect,
        OracleDialect, HanaDialect,
    )
}
_ALIASES = {
    "postgres": "postgresql", "mariadb": "mysql", "sqlserver": "mssql",
    "spark": "databricks", "saphana": "hana", "trino": "generic", "duckdb": "postgresql",
}


def get_dialect(name: str | None) -> Dialect:
    key = (name or "generic").lower()
    key = _ALIASES.get(key, key)
    return _DIALECTS.get(key, Dialect)()
