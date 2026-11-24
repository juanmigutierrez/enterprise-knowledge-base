"""S1d · Sources: the mock HR database, read with change data capture (CDC)."""

from __future__ import annotations

import json
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

import typer

from ekb.app.demos._app import demo_app
from ekb.app.demos._mocks import Echo, MockStartError, fail, run_mock_command

MODULE = "sources.mock_hr_db"


def _changes(conn: sqlite3.Connection, since: int) -> list[tuple[int, str, str, str, str, str]]:
    rows: list[tuple[int, str, str, str, str, str]] = conn.execute(
        "SELECT seq, op, table_name, row_key, coalesce(before, ''), coalesce(after, '') "
        "FROM cdc_log WHERE seq > ? ORDER BY seq",
        (since,),
    ).fetchall()
    return rows


def _row(text: str) -> str:
    return ", ".join(f"{k}={v}" for k, v in json.loads(text).items()) if text else "-"


def run_s1d(db_path: Path, echo: Echo = typer.echo) -> None:
    """The S1d story against a database file created by ``python -m sources.mock_hr_db init``."""
    echo("S1d · The mock HR database (stands in for Postgres read with Debezium)\n")
    with closing(sqlite3.connect(db_path)) as conn:
        offices = conn.execute("SELECT count(*) FROM office_headcount").fetchone()[0]
        balances = conn.execute("SELECT count(*) FROM leave_balances").fetchone()[0]
        snapshot = _changes(conn, 0)
        cursor = snapshot[-1][0] if snapshot else 0
        echo(f"1. Two tables: office_headcount ({offices} rows), leave_balances ({balances} rows).")
        echo(f"   The change log already holds {len(snapshot)} entries, all op=r (snapshot read):")
        echo("   CDC starts by reading every row once, then streams the changes.\n")

        echo("2. The HR application writes, in one transaction (nobody sends any event):")
        statements = [
            "INSERT INTO office_headcount (office_id, city, country, headcount) "
            "VALUES ('LIS', 'Lisbon', 'PT', 0)",
            "UPDATE leave_balances SET days_taken = 10 WHERE employee_id = 'E-1001'",
            "DELETE FROM office_headcount WHERE office_id = 'BLQ'",
        ]
        for statement in statements:
            conn.execute(statement)
            echo(f"   {statement}")
        conn.commit()

        conn.execute("UPDATE leave_balances SET days_taken = 0 WHERE employee_id = 'E-1002'")
        conn.rollback()
        echo("\n3. Someone starts another change and rolls it back.\n")

        echo(f"4. The change log after cursor {cursor} (what a CDC reader sees):")
        for seq, op, table, key, before, after in _changes(conn, cursor):
            echo(f"   #{seq:<3} op={op}  {table:<17} key {_row(key)}")
            echo(f"        before: {_row(before)}")
            echo(f"        after:  {_row(after)}")
    echo("\n   Three entries, in commit order. The delete keeps the row's last state,")
    echo("   so a reader knows exactly what disappeared. The rolled-back change left")
    echo("   nothing: the log is written in the same transaction as the data.")
    echo("   employee_id is the HR API's ID (E-1001 = Alice Rossi), and leave balances")
    echo("   are personal: later steps let each row be read only by that person and HR.")
    echo("   Rows like these are queried with SQL, not embedded (decision D7).\n")
    echo("Try it yourself:")
    echo("   uv run python -m sources.mock_hr_db init --path data/hr.sqlite")
    echo('   uv run python -m sources.mock_hr_db sql --path data/hr.sqlite "DELETE FROM ..."')
    echo("   uv run python -m sources.mock_hr_db changes --path data/hr.sqlite --since 16")


@demo_app.command("s1d")
def s1d() -> None:
    """S1d · Sources: write rows to the HR database and read the change log (CDC)."""
    try:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            db_path = Path(folder) / "hr.sqlite"
            run_mock_command(MODULE, ["init", "--path", str(db_path)])
            run_s1d(db_path)
    except (MockStartError, sqlite3.Error) as exc:
        raise fail(exc) from exc
