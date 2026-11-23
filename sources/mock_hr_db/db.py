"""Create the mock HR database and read its change log.

``create_database`` builds the file: tables, seed rows, a snapshot of them in
the change log, then the triggers (in that order, so the seed rows are logged
once, as snapshot reads, not as inserts). ``read_changes`` is what a CDC reader
does: everything after the last ``seq`` it saw.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
from contextlib import closing
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sources.common.seed import DATA_DIR, SeedError, read_yaml
from sources.mock_hr_db.schema import TABLE_DDL, TABLES, snapshot_sql, trigger_ddl

SEED_FILE = DATA_DIR / "hr_db.yaml"


class CdcOp(StrEnum):
    SNAPSHOT = "r"
    CREATE = "c"
    UPDATE = "u"
    DELETE = "d"


class ChangeRecord(BaseModel):
    """One change-log entry, shaped like a Debezium event."""

    model_config = ConfigDict(frozen=True)

    seq: int
    table: str
    op: CdcOp
    key: dict[str, Any]
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    logged_at: datetime


class _OfficeRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    office_id: str = Field(pattern=r"^[A-Z]{3}$")
    city: str
    country: str = Field(pattern=r"^[A-Z]{2}$")
    headcount: int = Field(ge=0)


class _LeaveRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    employee_id: str = Field(pattern=r"^E-\d{4}$")
    year: int
    days_total: float = Field(ge=0)
    days_taken: float = Field(ge=0)

    @model_validator(mode="after")
    def _taken_within_total(self) -> _LeaveRow:
        if self.days_taken > self.days_total:
            raise ValueError("days_taken can't exceed days_total")
        return self


_ROW_MODELS: dict[str, type[BaseModel]] = {
    "office_headcount": _OfficeRow,
    "leave_balances": _LeaveRow,
}


def create_database(path: Path, seed_file: Path = SEED_FILE) -> None:
    """Create a new database file at ``path``. Refuses to overwrite an existing one."""
    if path.exists():
        raise FileExistsError(f"{path} already exists")
    seed = read_yaml(seed_file)
    unknown = set(seed) - set(_ROW_MODELS)
    if unknown:
        raise SeedError(f"{seed_file.name}: unknown tables {sorted(unknown)}")
    # Validate every row before creating the file, so a bad seed leaves nothing behind.
    rows_by_table = {
        table.name: [
            _ROW_MODELS[table.name].model_validate(raw).model_dump()
            for raw in seed.get(table.name, [])
        ]
        for table in TABLES
    }
    with tempfile.TemporaryDirectory(dir=path.parent) as folder:
        staged = Path(folder) / "hr.sqlite"
        with closing(sqlite3.connect(staged)) as conn, conn:
            _populate(conn, rows_by_table)
        # Publish a complete file atomically, without replacing a concurrent creator.
        path.hardlink_to(staged)


def _populate(conn: sqlite3.Connection, rows_by_table: dict[str, list[dict[str, Any]]]) -> None:
    conn.executescript(TABLE_DDL)
    for table in TABLES:
        rows = rows_by_table[table.name]
        placeholders = ", ".join(f":{c}" for c in table.columns)
        conn.executemany(
            f"INSERT INTO {table.name} ({', '.join(table.columns)}) VALUES ({placeholders})",  # noqa: S608  # nosec B608: names come from TABLES, values are bound
            rows,
        )
        conn.execute(snapshot_sql(table))
        conn.executescript(trigger_ddl(table))


def read_changes(conn: sqlite3.Connection, since: int = 0) -> list[ChangeRecord]:
    """Change-log entries after ``since``, in commit order."""
    rows = conn.execute(
        "SELECT seq, table_name, op, row_key, before, after, logged_at "
        "FROM cdc_log WHERE seq > ? ORDER BY seq",
        (since,),
    ).fetchall()
    return [
        ChangeRecord(
            seq=seq,
            table=table,
            op=CdcOp(op),
            key=json.loads(key),
            before=json.loads(before) if before else None,
            after=json.loads(after) if after else None,
            logged_at=logged_at,
        )
        for seq, table, op, key, before, after, logged_at in rows
    ]
