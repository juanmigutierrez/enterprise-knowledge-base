"""Change-log reader for the HR database: what Debezium does for Postgres.

The product reads the database the way a CDC tool does, with SQL, never by
importing the mock. It opens the file **read-only**: a connector must never
be able to change a source. Each read returns the entries after a cursor
(``seq``), in commit order.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict


class CdcEntry(BaseModel):
    """One ``cdc_log`` row: Debezium's vocabulary (``op`` r / c / u / d, before / after)."""

    model_config = ConfigDict(frozen=True)

    seq: int
    table: str
    op: str
    key: dict[str, Any]
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    logged_at: datetime


class CdcReader:
    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)  # opened per read, so building a reader touches nothing

    def read(self, since: int) -> list[CdcEntry]:
        uri = f"{self._path.resolve().as_uri()}?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as conn:
            rows = conn.execute(
                "SELECT seq, table_name, op, row_key, before, after, logged_at "
                "FROM cdc_log WHERE seq > ? ORDER BY seq",
                (since,),
            ).fetchall()
        entries: list[CdcEntry] = []
        for seq, table, op, key, before, after, logged_at in rows:
            entry = CdcEntry(
                seq=seq,
                table=table,
                op=op,
                key=json.loads(key),
                before=json.loads(before) if before else None,
                after=json.loads(after) if after else None,
                logged_at=logged_at,
            )
            # Older mock databases recorded key changes as one update under the new key.
            if entry.op == "u" and entry.before:
                old_key = {column: entry.before[column] for column in entry.key}
                if old_key != entry.key:
                    entries.append(
                        entry.model_copy(update={"op": "d", "key": old_key, "after": None})
                    )
                    entry = entry.model_copy(update={"op": "c", "before": None})
            entries.append(entry)
        return entries
