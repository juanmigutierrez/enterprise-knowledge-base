"""Tables, change log and triggers of the mock HR database.

The triggers are the CDC: each one copies the changed row into ``cdc_log`` in
the same transaction as the change. So a rolled-back change leaves no log entry,
and because SQLite runs one writer at a time, ``seq`` order is commit order.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Table:
    name: str
    columns: tuple[str, ...]
    key: tuple[str, ...]


TABLES = (
    Table("office_headcount", ("office_id", "city", "country", "headcount"), ("office_id",)),
    Table(
        "leave_balances",
        ("employee_id", "year", "days_total", "days_taken"),
        ("employee_id", "year"),
    ),
)

TABLE_DDL = """
CREATE TABLE office_headcount (
    office_id  TEXT NOT NULL PRIMARY KEY,
    city       TEXT NOT NULL,
    country    TEXT NOT NULL CHECK (length(country) = 2),
    headcount  INTEGER NOT NULL CHECK (headcount >= 0)
);

-- Personal data: later steps let a row be read only by that employee and hr-team.
CREATE TABLE leave_balances (
    employee_id TEXT NOT NULL,   -- the HR record API's ID (E-....)
    year        INTEGER NOT NULL,
    days_total  REAL NOT NULL CHECK (days_total >= 0),
    days_taken  REAL NOT NULL CHECK (days_taken >= 0 AND days_taken <= days_total),
    PRIMARY KEY (employee_id, year)
);

-- The change log CDC reads. op: r = snapshot read, c = create, u = update, d = delete.
CREATE TABLE cdc_log (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    table_name TEXT NOT NULL,
    op         TEXT NOT NULL CHECK (op IN ('r', 'c', 'u', 'd')),
    row_key    TEXT NOT NULL,   -- JSON object of the primary key
    before     TEXT,            -- JSON row before the change (NULL for r and c)
    after      TEXT,            -- JSON row after the change (NULL for d)
    logged_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
"""


def _json(table: Table, columns: tuple[str, ...], row: str) -> str:
    """``json_object('a', NEW.a, 'b', NEW.b)`` for the given columns of NEW or OLD."""
    return "json_object(" + ", ".join(f"'{c}', {row}.{c}" for c in columns) + ")"


def trigger_ddl(table: Table) -> str:
    """The three AFTER triggers that log every change to ``table``."""
    log = "INSERT INTO cdc_log (table_name, op, row_key, before, after) VALUES"
    new_key, old_key = _json(table, table.key, "NEW"), _json(table, table.key, "OLD")
    new_row, old_row = _json(table, table.columns, "NEW"), _json(table, table.columns, "OLD")
    return f"""
CREATE TRIGGER {table.name}_cdc_insert AFTER INSERT ON {table.name} BEGIN
    {log} ('{table.name}', 'c', {new_key}, NULL, {new_row});
END;
CREATE TRIGGER {table.name}_cdc_update AFTER UPDATE ON {table.name} BEGIN
    {log} ('{table.name}', 'u', {new_key}, {old_row}, {new_row});
END;
CREATE TRIGGER {table.name}_cdc_delete AFTER DELETE ON {table.name} BEGIN
    {log} ('{table.name}', 'd', {old_key}, {old_row}, NULL);
END;
"""


def snapshot_sql(table: Table) -> str:
    """Log every existing row as a snapshot read (``op = 'r'``), in key order.

    Debezium starts the same way: it reads the current rows once, then streams
    changes. A reader that starts at seq 0 gets the whole state, then the changes.
    """
    # Every name below comes from TABLES, never from input: no injection possible.
    key_order = ", ".join(table.key)
    key, row = _json(table, table.key, table.name), _json(table, table.columns, table.name)
    select = (
        f"SELECT '{table.name}', 'r', {key}, NULL, {row} FROM {table.name} ORDER BY {key_order}"  # noqa: S608  # nosec B608
    )
    return f"INSERT INTO cdc_log (table_name, op, row_key, before, after) {select}"
