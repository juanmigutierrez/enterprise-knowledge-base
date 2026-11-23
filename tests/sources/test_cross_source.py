"""The synthetic sources describe one company, so they must agree with each other.

Different systems, different IDs: the HR API says ``E-1001``, the identity
provider says ``alice``, the database uses the HR API's IDs. These tests pin the
links later steps depend on (decided in the S1b-S1d plan): HR records and
identity users match 1-1 by full name, and the database's figures match the HR API.
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from contextlib import closing
from pathlib import Path

from sources.common.directory import Directory
from sources.mock_hr_api.store import HrStore
from sources.mock_hr_db.db import create_database


def test_every_identity_user_matches_exactly_one_hr_record_by_name() -> None:
    users = Directory.load().users
    employees = HrStore.from_seed().employees().records
    user_names = Counter(u.name for u in users.values())
    employee_names = Counter(e.name for e in employees)
    assert user_names == employee_names  # same names, each exactly once on both sides
    assert set(user_names.values()) == {1}


def test_matched_people_agree_on_their_office() -> None:
    users = {u.name: u for u in Directory.load().users.values()}
    for employee in HrStore.from_seed().employees().records:
        assert users[employee.name].location == employee.office


def test_database_headcount_matches_the_hr_records(tmp_path: Path) -> None:
    path = tmp_path / "hr.sqlite"
    create_database(path)
    with closing(sqlite3.connect(path)) as conn:
        headcount = dict(conn.execute("SELECT city, headcount FROM office_headcount").fetchall())
        leave_ids = {row[0] for row in conn.execute("SELECT employee_id FROM leave_balances")}
    per_office = Counter(e.office for e in HrStore.from_seed().employees().records)
    assert {city: n for city, n in headcount.items() if n} == dict(per_office)
    assert leave_ids == {e.id for e in HrStore.from_seed().employees().records}
