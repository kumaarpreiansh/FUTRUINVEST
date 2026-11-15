"""dq_platform — a platform-agnostic data quality, integrity and lineage engine.

Checks are declared once in YAML, compiled to SQL for the target dialect and
pushed down to the source (BigQuery, Postgres, Snowflake, SQL Server, MySQL,
an ERP's database, or plain CSV/Parquet files). Results are stored for trend
and anomaly detection, scored by data quality dimension, and joined with the
lineage graph so a failure shows what it affects downstream.
"""

__version__ = "0.1.0"
