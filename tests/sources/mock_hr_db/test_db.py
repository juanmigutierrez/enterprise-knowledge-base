"""The HR database: change data capture, deletes included, in commit order."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from sources.common.seed import SeedError
from sources.mock_hr_db.__main__ import main
from sources.mock_hr_db.db import SEED_FILE, CdcOp, create_database, read_changes

SNAPSHOT_ROWS = 4 + 12  # offices + leave balances in hr_db.yaml


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "hr.sqlite"
    create_database(path)
    return path


@pytest.fixture
def conn(db_path: Path) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(db_path)
    yield connection
    connection.close()


def test_seed_rows_are_logged_once_as_a_snapshot(conn: sqlite3.Connection) -> None:
    changes = read_changes(conn)
    assert len(changes) == SNAPSHOT_ROWS
    assert {c.op for c in changes} == {CdcOp.SNAPSHOT}
    assert [c.seq for c in changes] == list(range(1, SNAPSHOT_ROWS + 1))
    assert [c.table for c in changes[:4]] == ["office_headcount"] * 4
    assert changes[0].key == {"office_id": "BLQ"}  # snapshot rows come in key order
    assert all(c.before is None and c.after is not None for c in changes)


def test_insert_update_delete_each_log_one_entry_in_commit_order(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO office_headcount (office_id, city, country, headcount, changed_by) "
        "VALUES ('LIS', 'Lisbon', 'PT', 0, 'dana')"
    )
    conn.execute("UPDATE leave_balances SET days_taken = 10 WHERE employee_id = 'E-1001'")
    conn.execute("DELETE FROM office_headcount WHERE office_id = 'BLQ'")
    conn.commit()
    insert, update, delete = read_changes(conn, since=SNAPSHOT_ROWS)

    assert (insert.op, insert.table, insert.key) == (
        CdcOp.CREATE,
        "office_headcount",
        {"office_id": "LIS"},
    )
    assert insert.before is None
    assert insert.after == {
        "office_id": "LIS",
        "city": "Lisbon",
        "country": "PT",
        "headcount": 0,
        "changed_by": "dana",  # the application says who wrote the row
    }

    assert update.op is CdcOp.UPDATE
    assert update.key == {"employee_id": "E-1001", "year": 2026}
    assert update.before is not None
    assert update.after is not None
    assert (update.before["days_taken"], update.after["days_taken"]) == (8, 10)

    assert delete.op is CdcOp.DELETE
    assert delete.before == {
        "office_id": "BLQ",
        "city": "Bologna",
        "country": "IT",
        "headcount": 0,
        "changed_by": None,  # seeded rows name nobody
    }
    assert delete.after is None  # the row is gone; only its last state is logged

    assert [c.seq for c in (insert, update, delete)] == [
        SNAPSHOT_ROWS + 1,
        SNAPSHOT_ROWS + 2,
        SNAPSHOT_ROWS + 3,
    ]


def test_one_statement_touching_many_rows_logs_each_row(conn: sqlite3.Connection) -> None:
    conn.execute("UPDATE leave_balances SET days_total = days_total + 1 WHERE year = 2026")
    conn.commit()
    changes = read_changes(conn, since=SNAPSHOT_ROWS)
    assert len(changes) == 12
    assert {c.op for c in changes} == {CdcOp.UPDATE}


def test_rolled_back_change_leaves_no_log_entry(conn: sqlite3.Connection) -> None:
    conn.execute("UPDATE leave_balances SET days_taken = 0 WHERE employee_id = 'E-1002'")
    conn.rollback()
    assert read_changes(conn, since=SNAPSHOT_ROWS) == []
    (taken,) = conn.execute(
        "SELECT days_taken FROM leave_balances WHERE employee_id = 'E-1002'"
    ).fetchone()
    assert taken == 14


def test_change_refused_by_a_constraint_leaves_no_log_entry(conn: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE leave_balances SET days_taken = 99 WHERE employee_id = 'E-1001'")
    conn.commit()
    assert read_changes(conn, since=SNAPSHOT_ROWS) == []


def test_reader_resumes_from_its_cursor(conn: sqlite3.Connection) -> None:
    conn.execute("UPDATE office_headcount SET headcount = 7 WHERE office_id = 'MIL'")
    conn.commit()
    cursor = read_changes(conn)[-1].seq
    conn.execute("UPDATE office_headcount SET headcount = 4 WHERE office_id = 'MAD'")
    conn.commit()
    [change] = read_changes(conn, since=cursor)
    assert change.key == {"office_id": "MAD"}
    assert read_changes(conn, since=change.seq) == []


def test_two_connections_see_the_same_log(db_path: Path) -> None:
    writer, reader = sqlite3.connect(db_path), sqlite3.connect(db_path)
    try:
        writer.execute("DELETE FROM leave_balances WHERE employee_id = 'E-1012'")
        assert read_changes(reader, since=SNAPSHOT_ROWS) == []  # not committed yet
        writer.commit()
        assert [c.op for c in read_changes(reader, since=SNAPSHOT_ROWS)] == [CdcOp.DELETE]
    finally:
        writer.close()
        reader.close()


def test_create_refuses_to_overwrite(db_path: Path) -> None:
    with pytest.raises(FileExistsError):
        create_database(db_path)


def test_duplicate_seed_key_leaves_destination_available_for_retry(tmp_path: Path) -> None:
    seed = tmp_path / "bad.yaml"
    seed.write_text(
        SEED_FILE.read_text(encoding="utf-8").replace("E-1012", "E-1011"), encoding="utf-8"
    )
    path = tmp_path / "hr.sqlite"
    with pytest.raises(sqlite3.IntegrityError):
        create_database(path, seed)
    create_database(path)


def test_office_requires_a_key_for_cdc(conn: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO office_headcount (office_id, city, country, headcount) "
            "VALUES (NULL, 'Lisbon', 'PT', 0)"
        )
    assert read_changes(conn, since=SNAPSHOT_ROWS) == []


@pytest.mark.parametrize(
    ("old", "new", "error"),
    [
        ("days_taken: 8 }", "days_taken: 30 }", "exceed"),
        ("office_id: MIL", "office_id: milan", "office_id"),
        ("leave_balances:", "leave_balance:", "unknown tables"),
    ],
)
def test_bad_seed_is_refused(tmp_path: Path, old: str, new: str, error: str) -> None:
    seed = tmp_path / "hr_db.yaml"
    seed.write_text(SEED_FILE.read_text(encoding="utf-8").replace(old, new, 1), encoding="utf-8")
    with pytest.raises((SeedError, ValueError), match=error):
        create_database(tmp_path / "hr.sqlite", seed)
    assert not (tmp_path / "hr.sqlite").exists()  # nothing half-built is left behind


def test_command_line(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "sub" / "hr.sqlite"
    main(["init", "--path", str(path)])
    main(["sql", "--path", str(path), "DELETE FROM office_headcount WHERE office_id = 'BLQ'"])
    main(["sql", "--path", str(path), "SELECT count(*) FROM office_headcount"])
    main(["changes", "--path", str(path), "--since", str(SNAPSHOT_ROWS)])
    out = capsys.readouterr().out
    assert "created" in out
    assert "1 row(s) changed" in out
    assert "(3,)" in out
    assert '"op":"d"' in out
    with pytest.raises(SystemExit):
        main(["changes", "--path", str(tmp_path / "missing.sqlite")])
