"""CSV / Parquet / JSON files (local paths, or gs:// with gcsfs installed).
Each file becomes a table named after its stem, queried through an in-memory
SQLite database so the same SQL checks apply to flat-file ERP extracts.

    connections:
      extracts:
        type: files
        path: ./exports             # directory or glob
        pattern: "*.csv"
"""

from __future__ import annotations

import glob
import os
import sqlite3

from .sqlite import SQLiteConnector


class FilesConnector(SQLiteConnector):
    def __init__(self, name, settings):
        try:
            import pandas as pd
        except ImportError as e:  # pragma: no cover
            raise RuntimeError("pip install pandas (and pyarrow for Parquet)") from e
        conn = sqlite3.connect(":memory:")
        base = settings["path"]
        pattern = settings.get("pattern", "*")
        paths = sorted(glob.glob(os.path.join(base, pattern)) if os.path.isdir(base) else glob.glob(base))
        readers = {".csv": pd.read_csv, ".parquet": pd.read_parquet, ".json": pd.read_json,
                   ".jsonl": lambda p: pd.read_json(p, lines=True)}
        self.loaded = {}
        for p in paths:
            stem, ext = os.path.splitext(os.path.basename(p))
            reader = readers.get(ext.lower())
            if reader is None:
                continue
            df = reader(p)
            df.to_sql(stem, conn, index=False)
            self.loaded[stem] = p
        super().__init__(name, {**settings, "path": ":memory:"}, conn=conn)
