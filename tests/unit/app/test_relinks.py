"""The worker's re-links (S16): pages processed again when the people or teams they name
change, or when their context headers failed. Against the four mocks, offline."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from ekb.app.pipeline import EventFailedError
from ekb.app.relinks import Relinks
from ekb.contracts import TraceStatus
from ekb.platform.gateway.errors import GatewayError
from ekb.process.t4_chunk.options.header_model import ModelHeaderWriter
from ekb.process.t4_chunk.step import ChunkStep
from ekb.process.t6_entities.relink import EntityChange
from tests.unit.app.test_pipeline import World
from tests.unit.platform.gateway.helpers import Scripted

KEY = "hr_docs:HR-PL-11"
DANA, BOB = {"X-Actor": "dana"}, {"X-Actor": "bob"}
BODY = (
    "# Mentoring programme\n\nAsk Lucía Navarro to join a group.\n\n"
    "## Coordination\n\nSara Esposito coordinates the sessions."
)
PAGE = {
    "id": "HR-PL-11",
    "title": "Mentoring programme",
    "language": "en",
    "department": "people",
    "acl": {"groups": ["all-staff"]},
    "body": BODY,
}


def links(world: World, key: str = KEY) -> set[str]:
    return {e for chunk in world.stores.search.chunks(key) for e in chunk.entity_ids}


def mentoring(world: World) -> None:
    world.load()
    world.docs.post("/pages", json=PAGE, headers=DANA)
    world.listing()
    assert world.pipeline.relink() == []  # nothing changed in the entity store


def hire_sara(world: World, *, identity_first: bool = True) -> tuple[object, Callable[[], None]]:
    """Sara joins: an identity account and an HR record. With ``identity_first=False`` only
    the HR record is created; the returned callable creates the account later."""

    def account() -> None:
        world.idp.post(
            "/scim/v2/Users",
            json={
                "id": "sara",
                "name": "Sara Esposito",
                "location": "Milan",
                "groups": ["all-staff"],
            },
            headers=DANA,
        ).raise_for_status()

    if identity_first:
        account()
    record = world.hr.post(
        "/employees",
        json={
            "id": "E-1013",
            "name": "Sara Esposito",
            "work_email": "sara.esposito@example.com",
            "team_id": "T-PPL",
            "office": "Milan",
            "manager_id": "E-1002",
            "start_date": "2026-12-01",
        },
        headers=BOB,
    )
    record.raise_for_status()
    return record.json(), account


def test_the_initial_load_needs_no_relink(world: World) -> None:
    world.load()  # records first: every page was linked against today's entities

    assert world.pipeline.relink() == []
    assert len(world.pipeline.relinks) == 0


def test_a_hire_is_linked_on_a_page_that_already_named_them_without_an_edit(
    world: World,
) -> None:
    mentoring(world)
    assert links(world) == {"person:lucia"}
    version = world.stores.search.chunks(KEY)[0].document_version

    hire_sara(world)
    world.frequent()  # identity sync, then the HR delta: person:sara added
    outcomes = world.pipeline.relink()

    assert [o.trace.status for o in outcomes] == [TraceStatus.RELINKED]
    trace = outcomes[0].trace
    assert (trace.document_key, trace.partition, trace.action) == (KEY, None, None)
    assert trace.steps[0].step == "re-link"
    assert trace.steps[0].detail == "person:sara added"
    assert links(world) == {"person:lucia", "person:sara"}
    assert world.stores.search.chunks(KEY)[0].document_version == version  # no edit
    assert world.pipeline.relink() == []  # done: nothing left queued


def test_a_hire_whose_account_comes_after_the_hr_record_is_linked_after_the_listing(
    world: World,
) -> None:
    """HR hires first (as it usually does); the identity account follows. The HR full
    listing sends the record again with its person, and the page is re-linked."""
    mentoring(world)
    sara_in_hr, sara_in_idp = hire_sara(world, identity_first=False)
    world.frequent()
    assert world.stores.entities.get("person:sara") is None  # no account yet: fail closed

    sara_in_idp()
    world.frequent()  # the identity sync: a membership, still no entity
    assert world.pipeline.relink() == []
    world.listing()  # the HR listing sends E-1013 again, now about sara
    outcomes = world.pipeline.relink()

    assert world.stores.entities.get("person:sara") is not None
    assert [o.trace.status for o in outcomes] == [TraceStatus.RELINKED]
    assert links(world) == {"person:lucia", "person:sara"}
    assert sara_in_hr is not None


def test_a_leaver_found_only_by_the_full_listing_is_unlinked(world: World) -> None:
    mentoring(world)

    world.hr.delete("/employees/E-1009", headers=BOB).raise_for_status()
    world.frequent()  # the HR delta never reports a delete (S1b)
    assert world.stores.entities.get("person:lucia") is not None
    assert world.pipeline.relink() == []
    world.listing()  # the full listing finds it: T2 purges the record, the entity goes
    outcomes = world.pipeline.relink()

    assert [o.trace.status for o in outcomes] == [TraceStatus.RELINKED]
    assert outcomes[0].trace.steps[0].detail == "person:lucia removed"
    assert links(world) == set()
    assert "Navarro" not in str([o.trace for o in outcomes])  # traces hold no names


def test_a_record_change_no_link_can_see_relinks_nothing(world: World) -> None:
    mentoring(world)

    world.hr.patch("/employees/E-1009", json={"office": "Milan"}, headers=BOB)
    world.frequent()

    assert world.pipeline.relink() == []
    assert links(world) == {"person:lucia"}


def test_a_page_in_the_dead_letters_is_left_to_its_retry(world: World) -> None:
    mentoring(world)
    world.embedder.down.add(KEY)
    world.docs.put("/pages/HR-PL-11", json={"body": BODY + "\n\nNew line."}, headers=DANA)
    for attempt in range(3):
        try:
            world.listing() if attempt == 0 else world.pipeline.run()
        except EventFailedError:  # the first two attempts fail on purpose
            continue
    assert world.pipeline.dead_letters.withdrawn(KEY)

    hire_sara(world)
    world.frequent()

    assert world.pipeline.relink() == []  # withdrawn: nothing published to re-link
    world.embedder.down.clear()
    world.pipeline.retry_parked(KEY)
    assert links(world) == {"person:lucia", "person:sara"}  # linked against today's store


def test_a_relink_that_fails_stays_queued_and_the_page_stays_searchable(world: World) -> None:
    mentoring(world)
    hire_sara(world)
    world.frequent()
    world.embedder.down.add(KEY)

    assert world.pipeline.relink() == []

    assert world.last(KEY).status is TraceStatus.FAILED
    assert world.last(KEY).failed_step == "T7 embed"
    assert links(world) == {"person:lucia"}  # the old links, still searchable
    assert len(world.pipeline.relinks) == 1
    world.embedder.down.clear()
    assert [o.trace.status for o in world.pipeline.relink()] == [TraceStatus.RELINKED]
    assert links(world) == {"person:lucia", "person:sara"}


def test_a_deleted_page_is_dropped_from_the_queue(world: World) -> None:
    mentoring(world)
    hire_sara(world)
    world.frequent()
    world.docs.delete("/pages/HR-PL-11", headers=DANA)
    world.listing()

    assert world.pipeline.relink() == []
    assert len(world.pipeline.relinks) == 0


def test_failed_headers_are_retried_on_listing_rounds_only(world: World) -> None:
    """S8 left failed headers to "a reconciliation run": a page whose header model did not
    answer is processed again on a listing round, and gets its headers once one answers."""
    gateway = Scripted(error=GatewayError("no model answered"))
    steps = world.pipeline.steps
    world.pipeline.steps = replace(
        steps, t4=ChunkStep(steps.t4._chunker, ModelHeaderWriter(gateway))
    )
    world.load()
    key = "hr_docs:HR-PL-01"
    assert all(c.context_header == "" for c in world.stores.search.chunks(key))

    gateway.error = None
    gateway.text = '{"chunk_0": "From the annual leave policy."}'
    assert world.pipeline.relink() == []  # not a listing round
    outcomes = world.pipeline.relink(retry_headers=True)

    assert len(outcomes) == 12  # every page's headers had failed
    assert {o.trace.status for o in outcomes} == {TraceStatus.REBUILT}
    rebuilt = next(o.trace for o in outcomes if o.trace.document_key == key)
    assert rebuilt.steps[0].step == "rebuild"
    assert rebuilt.steps[0].detail == "its context headers failed"
    assert world.stores.search.chunks(key)[0].context_header == "From the annual leave policy."
    assert not any(
        o.trace.status is TraceStatus.REBUILT for o in world.pipeline.relink(retry_headers=True)
    )


def test_pages_without_headers_on_purpose_are_never_retried(world: World) -> None:
    world.load()  # the offline profile: t4_header none

    assert world.pipeline.relink(retry_headers=True) == []


def test_the_queue_adds_reasons_up_and_ignores_changes_no_link_can_see() -> None:
    queue = Relinks()
    queue.queue("hr_docs:A", "person:sara added")
    queue.queue("hr_docs:A", "person:lucia removed")
    queue.queue("hr_docs:A", "person:sara added", headers=True)
    queue.changed(EntityChange(None, None))

    (pending,) = queue.pages()
    assert pending.reasons == ("person:sara added", "person:lucia removed")
    assert pending.headers
    assert queue.take_changes() == []
    queue.done("hr_docs:A")
    assert len(queue) == 0
