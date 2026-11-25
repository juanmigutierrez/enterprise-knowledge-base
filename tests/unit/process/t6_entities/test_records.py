"""Reading HR records as entities (S9): canonical IDs, and fail closed without one."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime

import pytest

from ekb.contracts import ChangeEvent, ChangeOp, Entity, EntityKind
from ekb.process.t6_entities.records import RecordError, hr_record

NOW = datetime(2026, 11, 25, tzinfo=UTC)
EMPLOYEE = {
    "id": "E-1009",
    "name": "Lucía Navarro",
    "work_email": "lucia.navarro@example.com",
    "team_id": "T-PPL",
    "office": "Madrid",
    "manager_id": "E-1002",
    "start_date": "2021-04-12",
    "updated_at": "2026-11-25T00:00:00+00:00",
    "updated_by": None,
}


def event(source_id: str, person_id: str | None = None) -> ChangeEvent:
    return ChangeEvent(
        source="hr_api",
        source_id=source_id,
        op=ChangeOp.UPSERT,
        version="2026-11-25T00:00:00+00:00",
        occurred_at=NOW,
        raw_pointer=f"mem://raw/hr_api:{source_id}",
        person_id=person_id,
    )


def stored(record: Mapping[str, object]) -> bytes:
    return json.dumps(record, sort_keys=True).encode()


def test_an_employee_becomes_a_person_under_their_identity_user_id() -> None:
    entity = hr_record(event("employees/E-1009", person_id="lucia"), stored(EMPLOYEE))
    assert entity == Entity(
        entity_id="person:lucia",
        kind=EntityKind.PERSON,
        name="Lucía Navarro",
        aliases=("lucia.navarro@example.com",),
        team_id="team:T-PPL",
        source_key="hr_api:employees/E-1009",
        source_version="2026-11-25T00:00:00+00:00",
    )


def test_a_team_becomes_a_team_under_its_hr_id() -> None:
    record = {"id": "T-PPL", "name": "People", "department": "people", "lead_id": "E-1002"}
    entity = hr_record(event("teams/T-PPL"), stored(record))
    assert entity is not None
    assert (entity.entity_id, entity.kind, entity.name, entity.team_id) == (
        "team:T-PPL",
        EntityKind.TEAM,
        "People",
        None,
    )


def test_an_employee_with_no_identity_user_is_no_entity() -> None:
    # The connector could not map E-1013 to an identity user: no second ID is made up.
    record = {**EMPLOYEE, "id": "E-1013", "name": "Sara Ricci"}
    assert hr_record(event("employees/E-1013", person_id=None), stored(record)) is None


def test_an_employee_without_an_email_or_a_team_still_has_a_name() -> None:
    record = {"id": "E-1009", "name": "Lucía Navarro"}
    entity = hr_record(event("employees/E-1009", person_id="lucia"), stored(record))
    assert entity is not None
    assert (entity.aliases, entity.team_id) == ((), None)


@pytest.mark.parametrize(
    ("source_id", "original", "message"),
    [
        ("employees/E-1009", b"not json", "not an HR employees record"),
        ("employees/E-1009", stored({"id": "E-1009"}), "name"),
        ("employees/E-1009", stored({**EMPLOYEE, "id": "E-1010"}), "is E-1010, not E-1009"),
        ("offices/MIL", stored({"id": "MIL", "name": "Milan"}), "'offices'"),
    ],
)
def test_a_record_that_cannot_be_read_raises(source_id: str, original: bytes, message: str) -> None:
    with pytest.raises(RecordError, match=message):
        hr_record(event(source_id, person_id="lucia"), original)
