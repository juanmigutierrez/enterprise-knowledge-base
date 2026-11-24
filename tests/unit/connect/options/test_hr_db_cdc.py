"""CDC connector: snapshot, then every insert, update and delete in commit order."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sources.mock_hr_db.db import create_database

from ekb.connect.hr_db import CdcEntry, CdcReader
from ekb.connect.normalize import hr_db as normalize
from ekb.connect.normalize.common import UnknownChangeKindError
from ekb.connect.options.hr_db_cdc import HrDbCdcConnector
from ekb.connect.people import PersonMap
from ekb.contracts import Acl, ChangeEvent, ChangeOp
from tests.unit.connect.conftest import Originals


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "hr.sqlite"
    create_database(path)
    return path


@pytest.fixture
def cdc(
    db: Path, events: list[ChangeEvent], originals: Originals, people: PersonMap
) -> HrDbCdcConnector:
    return HrDbCdcConnector(CdcReader(db), events.append, originals, people)


def _sql(db: Path, *statements: str) -> None:
    """Play the HR application: write rows, naming the writer in ``changed_by``."""
    with closing(sqlite3.connect(db)) as conn, conn:
        for statement in statements:
            conn.execute(statement)


def test_the_snapshot_becomes_one_upsert_per_row(
    cdc: HrDbCdcConnector, events: list[ChangeEvent]
) -> None:
    assert cdc.sync() == 16  # 4 offices + 12 leave balances
    assert {e.op for e in events} == {ChangeOp.UPSERT}
    by_id = {e.source_id: e for e in events}
    alice = by_id["leave_balances/E-1001/2026"]
    assert alice.person_id == "alice"
    assert alice.acl == Acl(users=frozenset({"alice"}), groups=frozenset({"hr-team"}))
    milan = by_id["office_headcount/MIL"]
    assert (milan.person_id, milan.acl) == (None, Acl(groups=frozenset({"all-staff"})))
    assert [e.version for e in events] == [str(n) for n in range(1, 17)]  # commit order
    assert cdc.cursor == 16


def test_changes_follow_in_commit_order_with_their_writer(
    cdc: HrDbCdcConnector, db: Path, events: list[ChangeEvent]
) -> None:
    cdc.sync()
    events.clear()
    _sql(
        db,
        "UPDATE leave_balances SET days_taken = 10, changed_by = 'dana' "
        "WHERE employee_id = 'E-1001'",
        "DELETE FROM office_headcount WHERE office_id = 'BLQ'",
    )
    assert cdc.sync() == 2
    updated, deleted = events
    assert (updated.op, updated.version, updated.changed_by) == (ChangeOp.UPSERT, "17", "dana")
    assert (deleted.op, deleted.source_id, deleted.version) == (
        ChangeOp.DELETE,
        "office_headcount/BLQ",
        "18",
    )
    # Deletion gate: no content, hash, ACL, person or writer (the row is gone).
    assert (deleted.raw_pointer, deleted.content_hash, deleted.acl, deleted.person_id) == (
        None,
        None,
        Acl(),
        None,
    )
    assert deleted.changed_by is None
    assert cdc.sync() == 0  # resumes at the cursor; nothing new


def test_an_unmapped_employee_row_is_readable_by_hr_only(
    cdc: HrDbCdcConnector, db: Path, events: list[ChangeEvent], people: PersonMap
) -> None:
    cdc.sync()
    events.clear()
    _sql(
        db,
        "INSERT INTO leave_balances (employee_id, year, days_total, days_taken, changed_by) "
        "VALUES ('E-1099', 2026, 25, 0, 'mallory')",
    )
    cdc.sync()
    (row,) = events
    assert row.acl == Acl(groups=frozenset({"hr-team"}))  # fail closed
    assert (row.person_id, row.changed_by) == (None, None)
    assert people.unmapped == {"employee:E-1099", "user:mallory"}  # both reported


def test_the_reader_cannot_change_the_source(db: Path) -> None:
    uri = f"{db.resolve().as_uri()}?mode=ro"  # how CdcReader opens the file
    with closing(sqlite3.connect(uri, uri=True)) as conn, pytest.raises(sqlite3.OperationalError):
        conn.execute("DELETE FROM leave_balances")


def test_a_connector_can_start_after_a_known_cursor(
    db: Path, events: list[ChangeEvent], originals: Originals, people: PersonMap
) -> None:
    connector = HrDbCdcConnector(CdcReader(db), events.append, originals, people, start_after=16)
    assert connector.sync() == 12  # revalidate personal rows on resume
    assert all(event.op is ChangeOp.ACL_CHANGE for event in events)
    assert connector.sync() == 0


def _entry(**changes: object) -> CdcEntry:
    base: dict[str, object] = {
        "seq": 1,
        "table": "office_headcount",
        "op": "c",
        "key": {"office_id": "LIS"},
        "before": None,
        "after": {"office_id": "LIS"},
        "logged_at": datetime(2026, 11, 23),
    }
    return CdcEntry.model_validate({**base, **changes})


@pytest.mark.parametrize("entry", [_entry(table="payroll"), _entry(op="t")])
def test_unknown_tables_and_ops_are_errors(entry: CdcEntry) -> None:
    with pytest.raises(UnknownChangeKindError):
        normalize.event_for(
            "hr_db",
            entry,
            store_original=lambda key, version, original: "mem://x",
            person_id=None,
            changed_by=None,
        )


def test_key_change_deletes_old_leave_row_before_adding_new(
    cdc: HrDbCdcConnector, db: Path, events: list[ChangeEvent]
) -> None:
    cdc.sync()
    events.clear()
    _sql(db, "UPDATE leave_balances SET employee_id = 'E-1999' WHERE employee_id = 'E-1001'")
    assert cdc.sync() == 2
    deleted, added = events
    assert (deleted.op, deleted.source_id, deleted.acl) == (
        ChangeOp.DELETE,
        "leave_balances/E-1001/2026",
        Acl(),
    )
    assert (added.op, added.source_id, added.acl.users) == (
        ChangeOp.UPSERT,
        "leave_balances/E-1999/2026",
        frozenset(),
    )


def test_leave_acl_tracks_hr_changes_without_a_database_write(
    cdc: HrDbCdcConnector, hr: TestClient, events: list[ChangeEvent]
) -> None:
    cdc.sync()
    events.clear()
    employee = hr.get("/employees/E-1012").json()
    hr.delete("/employees/E-1012").raise_for_status()
    assert cdc.sync() == 1
    revoked = events[-1]
    assert (revoked.op, revoked.person_id, revoked.acl) == (
        ChangeOp.ACL_CHANGE,
        None,
        Acl(groups=frozenset({"hr-team"})),
    )
    for field in ("updated_at", "updated_by"):
        employee.pop(field, None)
    hr.post("/employees", json=employee).raise_for_status()
    assert cdc.sync() == 1
    restored = events[-1]
    assert restored.person_id == "olivia"
    assert restored.acl.users == {"olivia"}
    assert restored.version != revoked.version
    assert cdc.sync() == 0


def test_older_log_retries_both_halves_of_a_key_change(
    db: Path,
    people: PersonMap,
    originals: Originals,
) -> None:
    events: list[ChangeEvent] = []
    fail_once = True

    def sink(event: ChangeEvent) -> None:
        nonlocal fail_once
        if event.source_id == "office_headcount/NEW" and fail_once:
            fail_once = False
            raise ValueError("retry")
        events.append(event)

    connector = HrDbCdcConnector(CdcReader(db), sink, originals, people)
    connector.sync()
    events.clear()
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute(
            "INSERT INTO cdc_log (table_name, op, row_key, before, after) VALUES (?, ?, ?, ?, ?)",
            (
                "office_headcount",
                "u",
                json.dumps({"office_id": "NEW"}),
                json.dumps({"office_id": "MIL"}),
                json.dumps({"office_id": "NEW"}),
            ),
        )
    with pytest.raises(ValueError, match="retry"):
        connector.sync()
    assert connector.cursor == 16
    assert connector.sync() == 2
    assert [e.op for e in events] == [ChangeOp.DELETE, ChangeOp.DELETE, ChangeOp.UPSERT]
    assert [e.source_id for e in events[-2:]] == ["office_headcount/MIL", "office_headcount/NEW"]
    assert connector.cursor == 17


def test_directory_outage_still_delivers_deletes_and_revokes_individual_access(
    db: Path,
    cdc: HrDbCdcConnector,
    people: PersonMap,
    events: list[ChangeEvent],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cdc.sync()
    events.clear()

    def offline() -> None:
        raise OSError("directory offline")

    _sql(db, "DELETE FROM leave_balances WHERE employee_id = 'E-1001'")
    monkeypatch.setattr(people, "refresh", offline)
    assert cdc.sync() == 12
    assert events[0].op is ChangeOp.DELETE
    assert all(e.acl.users == frozenset() for e in events)
    assert cdc.sync() == 0
