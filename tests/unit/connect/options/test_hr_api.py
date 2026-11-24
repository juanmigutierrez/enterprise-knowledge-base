"""HR API connectors: the delta for edits and hires, the full listing for deletes."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sources.mock_hr_api.store import HrStore

from ekb.connect.hr_api import HrApi
from ekb.connect.normalize.common import canonical_json, content_hash
from ekb.connect.options.hr_api_delta import HrApiDeltaConnector
from ekb.connect.options.hr_api_full_listing import HrApiFullListingConnector
from ekb.connect.people import PersonMap
from ekb.contracts import Acl, ChangeEvent, ChangeOp
from tests.unit.connect.conftest import Originals

START = datetime(2026, 11, 23, 9, 0, tzinfo=UTC)
NOTICED = datetime(2026, 11, 24, 2, 0, tzinfo=UTC)
ALL_STAFF = Acl(groups=frozenset({"all-staff"}))


class Clock:
    """Moves one second per call unless frozen: control over the inclusive boundary."""

    def __init__(self) -> None:
        self.now = START
        self.frozen = False

    def __call__(self) -> datetime:
        if not self.frozen:
            self.now += timedelta(seconds=1)
        return self.now


@pytest.fixture
def clock(hr: TestClient) -> Clock:
    clock = Clock()
    hr.app.state.store = HrStore.from_seed(clock=clock)  # type: ignore[attr-defined]
    return clock


@pytest.fixture
def delta(
    hr: TestClient, clock: Clock, events: list[ChangeEvent], originals: Originals, people: PersonMap
) -> HrApiDeltaConnector:
    return HrApiDeltaConnector(HrApi(hr), events.append, originals, people)


MakeListing = Callable[[dict[str, str]], HrApiFullListingConnector]


@pytest.fixture
def make_listing(
    hr: TestClient, clock: Clock, events: list[ChangeEvent], originals: Originals, people: PersonMap
) -> MakeListing:
    def build(known: dict[str, str]) -> HrApiFullListingConnector:
        return HrApiFullListingConnector(
            HrApi(hr), events.append, originals, people, known=lambda: known, clock=lambda: NOTICED
        )

    return build


def _known(events: list[ChangeEvent]) -> dict[str, str]:
    known: dict[str, str] = {}
    for event in events:
        if event.op is ChangeOp.DELETE:
            known.pop(event.source_id, None)
        else:
            known[event.source_id] = event.version
    return known


def test_the_first_poll_loads_every_record(
    delta: HrApiDeltaConnector, events: list[ChangeEvent], originals: Originals
) -> None:
    assert delta.sync() == 17  # 12 employees + 5 teams
    by_id = {e.source_id: e for e in events}
    alice = by_id["employees/E-1001"]
    assert alice.document_key == "hr_api:employees/E-1001"
    assert (alice.op, alice.acl, alice.person_id, alice.changed_by) == (
        ChangeOp.UPSERT,
        ALL_STAFF,
        "alice",  # E-1001 is mapped to the identity user
        None,  # seeded: nobody named
    )
    assert by_id["teams/T-ENG"].person_id is None  # a team is about no one person
    original = originals.saved[alice.raw_pointer or ""]
    assert original == canonical_json(json.loads(original))  # stored as canonical JSON
    assert alice.content_hash == content_hash(original)
    assert all(e.person_id for e in events if e.source_id.startswith("employees/"))


def test_the_next_poll_returns_only_what_changed(
    delta: HrApiDeltaConnector, hr: TestClient, events: list[ChangeEvent]
) -> None:
    delta.sync()
    events.clear()
    hr.patch("/employees/E-1008", json={"office": "Madrid"}, headers={"X-Actor": "lucia"})
    assert delta.sync() == 1
    (moved,) = events
    assert (moved.source_id, moved.person_id, moved.changed_by) == (
        "employees/E-1008",
        "javier",
        "lucia",
    )
    assert b'"office":"Madrid"' in _original_of(delta, moved)


def _original_of(delta: HrApiDeltaConnector, event: ChangeEvent) -> bytes:
    originals: Originals = delta._store_original  # type: ignore[assignment]
    return originals.saved[event.raw_pointer or ""]


def test_a_delete_is_invisible_to_the_delta(
    delta: HrApiDeltaConnector, hr: TestClient, events: list[ChangeEvent]
) -> None:
    delta.sync()
    events.clear()
    hr.delete("/employees/E-1012", headers={"X-Actor": "dana"})
    assert delta.sync() == 0  # the source says nothing; only a listing can tell


def test_a_change_at_the_boundary_repeats_with_the_same_version(
    delta: HrApiDeltaConnector, hr: TestClient, clock: Clock, events: list[ChangeEvent]
) -> None:
    delta.sync()
    clock.frozen = True  # the next save is stamped exactly at the cursor instant
    hr.patch("/employees/E-1008", json={"office": "Madrid"})
    delta.sync()
    delta.sync()  # inclusive filter: the same record comes back
    first, again = [e for e in events if e.source_id == "employees/E-1008"][-2:]
    assert (first.version, first.content_hash) == (again.version, again.content_hash)


def test_an_unmapped_hire_is_emitted_fail_closed_and_reported(
    delta: HrApiDeltaConnector, hr: TestClient, events: list[ChangeEvent], people: PersonMap
) -> None:
    delta.sync()
    events.clear()
    hr.post(
        "/employees",
        json={
            "id": "E-1013",
            "name": "Sara Neri",
            "work_email": "sara.neri@example.com",
            "team_id": "T-ENG",
            "office": "Milan",
            "start_date": "2026-12-01",
        },
        headers={"X-Actor": "dana"},
    )
    delta.sync()
    (hired,) = events
    assert (hired.person_id, hired.changed_by) == (None, "dana")
    assert people.unmapped == {"employee:E-1013"}


def test_a_failed_poll_keeps_the_cursor(
    clock: Clock, events: list[ChangeEvent], originals: Originals, people: PersonMap
) -> None:
    def down(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    api = HrApi(httpx.Client(base_url="http://hr", transport=httpx.MockTransport(down)))
    connector = HrApiDeltaConnector(api, events.append, originals, people)
    with pytest.raises(httpx.HTTPStatusError):
        connector.sync()
    assert connector.cursors == {"employees": None, "teams": None}


def test_the_listing_finds_the_delete_and_nothing_else(
    delta: HrApiDeltaConnector,
    make_listing: MakeListing,
    hr: TestClient,
    events: list[ChangeEvent],
) -> None:
    delta.sync()
    known = _known(events)
    assert make_listing(known).sync() == 0  # nothing changed, nothing emitted
    events.clear()
    hr.delete("/employees/E-1012", headers={"X-Actor": "dana"})
    hr.patch("/teams/T-ENG", json={"name": "Engineering"})
    assert make_listing(known).sync() == 2
    edited, gone = events
    assert (edited.op, edited.source_id) == (ChangeOp.UPSERT, "teams/T-ENG")
    assert (gone.op, gone.source_id, gone.version) == (
        ChangeOp.DELETE,
        "employees/E-1012",
        known["employees/E-1012"],
    )
    assert (gone.occurred_at, gone.changed_by, gone.person_id, gone.acl) == (
        NOTICED,  # the source gives no time: when the listing noticed
        None,  # dana deleted it, but nothing records that
        None,
        Acl(),
    )
