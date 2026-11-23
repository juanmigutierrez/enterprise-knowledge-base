"""The HR record API: pull-based deltas, and deletes that leave no trace."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from sources.mock_hr_api.models import EmployeeCreate, EmployeeUpdate, TeamUpdate
from sources.mock_hr_api.store import (
    SEED_FILE,
    HrStore,
    InvalidReferenceError,
    RecordConflictError,
    RecordNotFoundError,
)

from tests.sources.conftest import ManualClock, TickingClock


@pytest.fixture
def store(ticking_clock: TickingClock) -> HrStore:
    return HrStore.from_seed(clock=ticking_clock)


def _new_hire(**overrides: object) -> EmployeeCreate:
    fields: dict[str, object] = {
        "id": "E-1013",
        "name": "Sofia Marino",
        "work_email": "sofia.marino@example.com",
        "team_id": "T-ENG",
        "office": "Milan",
        "manager_id": "E-1004",
        "start_date": "2026-12-01",
    }
    return EmployeeCreate.model_validate(fields | overrides)


# ------------------------------------------------------------------- seed


def test_seed_has_twelve_employees_in_five_teams_and_three_offices(store: HrStore) -> None:
    employees = store.employees().records
    assert len(employees) == 12
    assert len(store.teams().records) == 5
    offices = {o: sum(e.office == o for e in employees) for o in ("Milan", "Madrid", "Toronto")}
    assert offices == {"Milan": 6, "Madrid": 3, "Toronto": 3}


def test_seed_names_are_unique(store: HrStore) -> None:
    names = [e.name for e in store.employees().records]
    assert len(names) == len(set(names))


def test_listing_is_sorted_by_id(store: HrStore) -> None:
    ids = [e.id for e in store.employees().records]
    assert ids == sorted(ids)


# ----------------------------------------------------------------- deltas


def test_delta_returns_only_records_changed_since(store: HrStore) -> None:
    as_of = store.employees().as_of
    store.update_employee("E-1004", EmployeeUpdate(office="Madrid"))
    delta = store.employees(updated_since=as_of)
    assert [e.id for e in delta.records] == ["E-1004"]
    assert delta.records[0].office == "Madrid"
    assert store.employees(updated_since=delta.as_of).records == []


def test_updated_since_is_inclusive(manual_clock: ManualClock) -> None:
    store = HrStore.from_seed(clock=manual_clock)
    moment = manual_clock.advance()
    store.update_employee("E-1001", EmployeeUpdate(office="Toronto"))  # stamped exactly `moment`
    assert [e.id for e in store.employees(updated_since=moment).records] == ["E-1001"]
    later = moment + timedelta(microseconds=1)
    assert store.employees(updated_since=later).records == []


def test_change_at_the_as_of_instant_is_never_missed(manual_clock: ManualClock) -> None:
    """A change stamped at the very instant of the previous poll's ``as_of``.

    With an exclusive filter (``>``) the next poll would skip it for ever. With the
    inclusive one it is returned; asking again from the same cursor returns it
    again (a harmless duplicate that T2 drops in S4), and once the cursor moves
    past that instant it stops coming back.
    """
    store = HrStore.from_seed(clock=manual_clock)
    first = store.employees(updated_since=manual_clock.advance())
    store.update_employee("E-1001", EmployeeUpdate(office="Madrid"))  # stamped == first.as_of
    manual_clock.advance()
    second = store.employees(updated_since=first.as_of)
    assert [e.id for e in second.records] == ["E-1001"]  # not missed
    assert [e.id for e in store.employees(updated_since=first.as_of).records] == ["E-1001"]
    assert store.employees(updated_since=second.as_of).records == []


def test_saving_without_changes_still_stamps_the_record(store: HrStore) -> None:
    before = store.employee("E-1001")
    as_of = store.employees().as_of
    after = store.update_employee("E-1001", EmployeeUpdate(office=before.office))
    assert after.updated_at > before.updated_at
    assert [e.id for e in store.employees(updated_since=as_of).records] == ["E-1001"]


def test_time_without_zone_is_read_as_utc(store: HrStore) -> None:
    as_of = store.employees().as_of
    store.update_employee("E-1001", EmployeeUpdate(office="Madrid"))
    naive = as_of.replace(tzinfo=None)
    assert [e.id for e in store.employees(updated_since=naive).records] == ["E-1001"]


def test_team_delta(store: HrStore) -> None:
    as_of = store.teams().as_of
    team = store.update_team("T-PRD", TeamUpdate(name="Product & Design"))
    assert team.name == "Product & Design"
    assert [t.id for t in store.teams(updated_since=as_of).records] == ["T-PRD"]


# ---------------------------------------------------------------- deletes


def test_delete_leaves_no_trace_in_any_delta(store: HrStore) -> None:
    before = store.employees()
    store.delete_employee("E-1012")
    assert store.employees(updated_since=before.as_of).records == []
    assert store.teams(updated_since=before.as_of).records == []
    with pytest.raises(RecordNotFoundError):
        store.employee("E-1012")


def test_only_comparing_two_listings_reveals_a_delete(store: HrStore) -> None:
    before = {e.id for e in store.employees().records}
    store.delete_employee("E-1012")
    after = {e.id for e in store.employees().records}
    assert before - after == {"E-1012"}


def test_cannot_delete_someone_who_manages_people_or_leads_a_team(store: HrStore) -> None:
    with pytest.raises(RecordConflictError, match="manages"):
        store.delete_employee("E-1004")  # leads Engineering and manages three people
    with pytest.raises(RecordConflictError, match="leads"):
        store.delete_employee("E-1010")  # leads Product, manages nobody
    assert len(store.employees().records) == 12


def test_deleting_a_missing_record_is_not_found(store: HrStore) -> None:
    store.delete_employee("E-1012")
    with pytest.raises(RecordNotFoundError):
        store.delete_employee("E-1012")
    with pytest.raises(RecordNotFoundError):
        store.update_employee("E-1012", EmployeeUpdate(office="Milan"))


# ----------------------------------------------------- partial updates


def test_explicit_null_clears_the_manager_but_omission_keeps_it(store: HrStore) -> None:
    kept = store.update_employee("E-1001", EmployeeUpdate(office="Milan"))
    assert kept.manager_id == "E-1004"
    cleared = store.update_employee("E-1001", EmployeeUpdate(manager_id=None))
    assert cleared.manager_id is None


def test_null_for_a_required_field_is_ignored(store: HrStore) -> None:
    employee = store.update_employee("E-1001", EmployeeUpdate.model_validate({"name": None}))
    assert employee.name == "Alice Rossi"


# ------------------------------------------------------ integrity rules


def test_hire_adds_a_record_that_shows_in_the_delta(store: HrStore) -> None:
    as_of = store.employees().as_of
    hired = store.hire(_new_hire())
    assert [e.id for e in store.employees(updated_since=as_of).records] == [hired.id]


@pytest.mark.parametrize(
    ("overrides", "error", "message"),
    [
        ({"id": "E-1001"}, RecordConflictError, "exists"),
        ({"name": "Alice Rossi"}, RecordConflictError, "1-1"),
        ({"team_id": "T-XXX"}, InvalidReferenceError, "team T-XXX"),
        ({"manager_id": "E-9999"}, InvalidReferenceError, "manager E-9999"),
        ({"manager_id": "E-1013"}, InvalidReferenceError, "themselves"),
    ],
)
def test_hire_is_refused_when_it_breaks_a_rule(
    store: HrStore, overrides: dict[str, object], error: type[Exception], message: str
) -> None:
    with pytest.raises(error, match=message):
        store.hire(_new_hire(**overrides))
    assert len(store.employees().records) == 12


def test_rename_to_an_existing_name_is_refused(store: HrStore) -> None:
    with pytest.raises(RecordConflictError, match="1-1"):
        store.update_employee("E-1001", EmployeeUpdate(name="Olivia Chen"))
    assert store.employee("E-1001").name == "Alice Rossi"


def test_team_lead_must_exist(store: HrStore) -> None:
    with pytest.raises(InvalidReferenceError, match="E-9999"):
        store.update_team("T-PRD", TeamUpdate(lead_id="E-9999"))
    with pytest.raises(RecordNotFoundError):
        store.team("T-XXX")


def test_seed_with_a_missing_team_lead_fails(tmp_path: Path) -> None:
    seed = tmp_path / "hr_api.yaml"
    seed.write_text(
        SEED_FILE.read_text(encoding="utf-8").replace("lead_id: E-1010", "lead_id: E-9999"),
        encoding="utf-8",
    )
    with pytest.raises(InvalidReferenceError, match="E-9999"):
        HrStore.from_seed(seed)


def test_seed_with_a_duplicate_employee_id_fails(tmp_path: Path) -> None:
    seed = tmp_path / "hr_api.yaml"
    text = SEED_FILE.read_text(encoding="utf-8").replace("id: E-1012", "id: E-1011")
    seed.write_text(text, encoding="utf-8")
    with pytest.raises(RecordConflictError, match="E-1011"):
        HrStore.from_seed(seed)


def test_seed_with_a_duplicate_team_id_fails(tmp_path: Path) -> None:
    seed = tmp_path / "hr_api.yaml"
    text = SEED_FILE.read_text(encoding="utf-8").replace("id: T-PRD", "id: T-ENG", 1)
    seed.write_text(text, encoding="utf-8")
    with pytest.raises(RecordConflictError, match="duplicate team T-ENG"):
        HrStore.from_seed(seed)
