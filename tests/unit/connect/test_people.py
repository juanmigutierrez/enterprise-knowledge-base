"""One canonical person ID: every person is mapped to an identity user, or reported."""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from ekb.connect.people import People, PersonMap

NEW_HIRE = {
    "id": "E-1013",
    "name": "Sara Neri",
    "work_email": "sara.neri@example.com",
    "team_id": "T-ENG",
    "office": "Milan",
    "start_date": "2026-12-01",
}


def test_the_synthetic_company_is_fully_mapped(people: PersonMap) -> None:
    """The double check: every HR employee has exactly one identity user."""
    assert people.coverage() == (12, 12)
    assert people.gaps() == []
    assert people.for_employee("E-1001") == "alice"
    assert people.for_employee("E-1012") == "olivia"
    assert people.user("dana") == "dana"
    assert people.unmapped == frozenset()


def test_no_one_named_is_not_a_gap(people: PersonMap) -> None:
    assert people.user(None) is None
    assert people.unmapped == frozenset()


def test_an_unmapped_employee_is_reported_and_logged(
    people: PersonMap, hr: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    hr.post("/employees", json=NEW_HIRE)  # hired in HR, no identity account yet
    with caplog.at_level(logging.WARNING, logger="ekb.connect.people"):
        assert people.for_employee("E-1013") is None
    assert people.unmapped == {"employee:E-1013"}
    assert people.gaps() == ["employee:E-1013"]  # the reload saw the hire
    assert "unmapped person employee:E-1013" in caplog.text


def test_a_joiner_is_picked_up_by_reloading(
    people: PersonMap, hr: TestClient, idp: TestClient
) -> None:
    hr.post("/employees", json=NEW_HIRE)
    idp.post(
        "/scim/v2/Users",
        json={"id": "sara", "name": "Sara Neri", "location": "Milan", "groups": ["all-staff"]},
    )
    assert people.for_employee("E-1013") == "sara"
    assert people.user("sara") == "sara"
    assert people.unmapped == frozenset()
    assert people.coverage() == (13, 13)


def test_unknown_users_are_dropped_from_a_set(people: PersonMap) -> None:
    assert people.users_in(["alice", "mallory"]) == {"alice"}
    assert people.unmapped == {"user:mallory"}


def test_two_users_with_one_name_are_refused() -> None:
    def load() -> People:
        return People(
            users={"anna": "Anna Rossi", "anna2": "Anna Rossi"}, employees={"E-1099": "Anna Rossi"}
        )

    assert PersonMap(load).for_employee("E-1099") is None
