"""The worker pipeline (S12): T2 → T8 on the bus, end to end, against the four mocks.

Everything here runs offline: the local profile's options with no model (no header,
the rules classifier, fake vectors), the mocks in-process, the change log in memory.
T7's embedder can be told to fail for some pages, to stand for a model that is down.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sources.mock_docs.app import create_app as docs_app
from sources.mock_hr_api.app import create_app as hr_api_app
from sources.mock_hr_db.db import create_database
from sources.mock_idp.app import create_app as idp_app

from ekb.app.changelog import ChangeLog
from ekb.app.choices import choose
from ekb.app.connectors import Connectors, build_connectors
from ekb.app.pipeline import (
    WORKER_GROUP,
    EventFailedError,
    Outcome,
    Pipeline,
    Stores,
    build_changelog,
    build_steps,
    build_stores,
)
from ekb.contracts import (
    STEP_ORDER,
    Acl,
    ChangeEvent,
    ChangeOp,
    DiffAction,
    StepOutcome,
    Trace,
    TraceStatus,
)
from ekb.platform.config import load_settings
from ekb.platform.gateway.errors import GatewayError
from ekb.process.t7_embed.options.fake import FakeEmbedder
from ekb.process.t7_embed.port import Vectors
from ekb.process.t7_embed.step import EmbedStep
from ekb.process.t8_permissions.step import PermissionResult
from tests.http import as_httpx

DOCUMENT_STEPS = [
    "T2 diff",
    "T8 readers first",
    "T3 parse",
    "T4 chunk",
    "T5 classify",
    "T6 entities",
    "T7 embed",
    "T8 publish",
]


class FlakyEmbedder:
    """The fake embedder, except for the pages in ``down``: their model is down."""

    def __init__(self) -> None:
        self._inner = FakeEmbedder()
        self.down: set[str] = set()

    @property
    def model_id(self) -> str:
        return self._inner.model_id

    @property
    def dimensions(self) -> int:
        return self._inner.dimensions

    def embed(self, texts: Sequence[str], *, document_key: str | None = None) -> Vectors:
        if document_key in self.down:
            raise GatewayError("ollama:bge-m3: connection refused")
        return self._inner.embed(texts, document_key=document_key)


@dataclass
class World:
    docs: TestClient
    hr: TestClient
    idp: TestClient
    changelog: ChangeLog
    stores: Stores
    connectors: Connectors
    pipeline: Pipeline
    embedder: FlakyEmbedder
    traces: list[Trace] = field(default_factory=list)
    outcomes: list[Outcome] = field(default_factory=list)

    def load(self) -> None:
        """Round zero: records first (so T6 and T8 know the people), then the pages."""
        for connector in self.connectors.frequent:
            connector.sync()
        self.pipeline.run()
        self.listing()

    def listing(self) -> int:
        """The full listings find what changed in the sources; the worker drains."""
        for connector in self.connectors.listings:
            connector.sync()
        return self.pipeline.run()

    def frequent(self) -> int:
        for connector in self.connectors.frequent:
            connector.sync()
        return self.pipeline.run()

    def last(self, key: str) -> Trace:
        return [t for t in self.traces if t.document_key == key][-1]

    def pages(self, user: str) -> set[str]:
        principals = self.stores.permissions.principals(user)
        return {c.document_key for c in self.stores.search.readable_by(principals)}

    def lag(self) -> int:
        groups = {g.group: g.lag for g in self.changelog.bus.stats().groups}
        return groups[WORKER_GROUP]


@pytest.fixture
def world(tmp_path: Path) -> Iterator[World]:
    with (
        TestClient(docs_app(post=lambda url, payload: None)) as docs,
        TestClient(hr_api_app()) as hr,
        TestClient(idp_app()) as idp,
    ):
        create_database(tmp_path / "hr.sqlite")
        choices = choose(load_settings(profile="local"), offline=True)
        changelog = build_changelog(choices)
        stores = build_stores(choices, changelog)
        embedder = FlakyEmbedder()
        steps = replace(build_steps(choices, changelog, stores), t7=EmbedStep(embedder))
        traces: list[Trace] = []
        outcomes: list[Outcome] = []
        pipeline = Pipeline(
            steps, changelog, stores, observers=[outcomes.append], on_trace=traces.append
        )
        connectors = build_connectors(
            as_httpx(docs), as_httpx(hr), as_httpx(idp), tmp_path / "hr.sqlite", changelog
        )
        world = World(docs, hr, idp, changelog, stores, connectors, pipeline, embedder)
        world.traces, world.outcomes = traces, outcomes
        yield world


# ------------------------------------------------------------- the write path


def test_worker_releases_recovered_page_when_it_overtakes_acceptance(world: World) -> None:
    world.load()
    key = "hr_docs:HR-PL-04"
    previous = next(
        o.delivery for o in world.outcomes if o.delivery and o.delivery.event.document_key == key
    )
    assert previous is not None
    world.stores.search.set_visible(key, visible=False)
    pending = world.changelog.bus.publish(previous.event)
    assert world.pipeline.run() == 1
    world.changelog.acceptance.accept(pending)
    assert world.lag() == 0
    assert key in world.pages("alice")


def test_one_group_takes_every_source_through_the_write_path(world: World) -> None:
    world.load()

    statuses = {(source, status) for source, status in ((t.source, t.status) for t in world.traces)}
    assert statuses == {
        ("hr_docs", TraceStatus.PUBLISHED),
        ("hr_api", TraceStatus.RECORD),
        ("hr_db", TraceStatus.RECORD),
        ("identity", TraceStatus.RECORD),
    }
    assert len(world.traces) == 12 + 17 + 16 + 12  # one attempt per event, none failed
    chunks = world.stores.search.chunks()
    assert len(chunks) == 48
    assert all(chunk.publishable for chunk in chunks)
    assert len(world.stores.entities.entities()) == 17  # 12 people, 5 teams
    assert len(world.stores.permissions.memberships()) == 12
    assert world.lag() == 0
    assert [g.group for g in world.changelog.bus.stats().groups] == [WORKER_GROUP]


def test_a_page_is_traced_step_by_step_from_t2_to_t8(world: World) -> None:
    world.load()

    trace = world.last("hr_docs:HR-PL-04")
    assert [step.step for step in trace.steps] == DOCUMENT_STEPS
    assert all(step.outcome is StepOutcome.DONE for step in trace.steps)
    assert (trace.action, trace.status, trace.attempt) == (
        DiffAction.PROCESS,
        TraceStatus.PUBLISHED,
        1,
    )
    assert trace.partition is not None
    assert trace.offset is not None
    details = {step.step: step.detail for step in trace.steps}
    assert details["T2 diff"] == "process: new: never processed"
    assert details["T8 readers first"].startswith("a new document")
    assert "structure_aware@1(max_tokens=400)" in details["T4 chunk"]  # from config
    assert "team:T-PPL x2" in details["T6 entities"]
    assert details["T8 publish"].startswith("published 4 chunks of v1.1, readers all-staff")
    assert trace.ms >= sum(step.ms for step in trace.steps) * 0.5


def test_records_go_to_their_stores_and_are_never_chunked(world: World) -> None:
    world.load()

    user = world.last("identity:users/alice")
    assert [s.step for s in user.steps] == ["T2 diff", "T3 parse", "permission store"]
    assert user.steps[1].detail.startswith("not a document")
    employee = world.last("hr_api:employees/E-1001")
    assert [s.step for s in employee.steps] == ["T2 diff", "T3 parse", "entity store"]
    assert employee.steps[2].detail == "person:alice added"  # an ID, not a name
    row = next(t for t in world.traces if t.source == "hr_db")
    assert [s.step for s in row.steps] == ["T2 diff", "T3 parse", "no store yet"]
    assert "S14b" in row.steps[2].detail
    assert {c.document_key.partition(":")[0] for c in world.stores.search.chunks()} == {"hr_docs"}


def test_each_kind_of_change_takes_its_own_route(world: World) -> None:
    world.load()
    before = {c.chunk_id: c.vector for c in world.stores.search.chunks("hr_docs:HR-PL-05")}
    page = world.docs.get("/pages/HR-PL-01").json()
    world.docs.put("/pages/HR-PL-01", json={"body": page["body"]})  # same text
    world.docs.put("/pages/HR-PL-04", json={"body": "# Remote work\n\n## Rule\nFriday too.\n"})
    world.docs.put("/pages/HR-PL-05/acl", json={"groups": ["es-employees"]})
    world.docs.delete("/pages/HR-PL-99")
    world.listing()

    skipped = world.last("hr_docs:HR-PL-01")
    assert (skipped.action, skipped.status) == (DiffAction.SKIP, TraceStatus.SKIPPED)
    assert [s.step for s in skipped.steps] == ["T2 diff"]

    edited = world.last("hr_docs:HR-PL-04")
    assert [s.step for s in edited.steps] == DOCUMENT_STEPS
    assert "readers unchanged" in edited.steps[1].detail
    assert "(replaced 4)" in edited.steps[-1].detail
    assert {c.document_version for c in world.stores.search.chunks("hr_docs:HR-PL-04")} == {"2.1"}

    revoked = world.last("hr_docs:HR-PL-05")
    assert (revoked.action, revoked.status) == (DiffAction.ACL_ONLY, TraceStatus.READERS)
    assert [s.step for s in revoked.steps] == ["T2 diff", "T8 fast lane"]
    after = {c.chunk_id: c.vector for c in world.stores.search.chunks("hr_docs:HR-PL-05")}
    assert after == before  # nothing parsed or embedded again
    assert "hr_docs:HR-PL-05" not in world.pages("dana")  # hr-team lost it

    deleted = world.last("hr_docs:HR-PL-99")
    assert (deleted.action, deleted.status) == (DiffAction.PURGE, TraceStatus.PURGED)
    assert [s.step for s in deleted.steps] == ["T2 diff", "purge search", "purge entity"]
    assert deleted.steps[1].detail.startswith("4 chunks out of the search store")
    # Every step name the worker wrote is one the demo and the UI know how to order.
    assert {s.step for t in world.traces for s in t.steps} <= set(STEP_ORDER)


def test_deletion_gate_a_purge_leaves_nothing_in_any_store(world: World) -> None:
    world.load()
    world.docs.delete("/pages/HR-PL-99")
    world.hr.delete("/employees/E-1012")
    world.idp.delete("/scim/v2/Users/olivia")
    world.frequent()
    world.listing()

    assert world.stores.search.chunks("hr_docs:HR-PL-99") == ()
    assert world.stores.permissions.readers("hr_docs:HR-PL-99") is None
    assert all("hr_docs:HR-PL-99" not in world.pages(u) for u in ("alice", "bob", "emma"))
    assert world.stores.entities.get("person:olivia") is None
    leaver = world.last("hr_api:employees/E-1012")
    assert leaver.status is TraceStatus.PURGED
    assert leaver.steps[2].detail == "person:olivia removed"


def test_a_records_readers_change_publishes_nothing(world: World) -> None:
    world.load()
    world.hr.delete("/employees/E-1012")  # olivia's leave rows lose their reader (S2a)
    world.frequent()
    world.listing()

    revocation = next(t for t in world.traces if t.action is DiffAction.ACL_ONLY)
    assert revocation.document_key == "hr_db:leave_balances/E-1012/2026"
    assert revocation.status is TraceStatus.RECORD
    assert revocation.steps[-1].detail.startswith("a record: nothing of it is published")


def test_traces_hold_no_content_even_after_a_delete(world: World) -> None:
    world.load()
    texts = {c.text for c in world.stores.search.chunks()}
    titles = {p["title"] for p in world.docs.get("/pages").json()}
    names = {e.name for e in world.stores.entities.entities()}
    world.docs.delete("/pages/HR-PL-99")
    world.listing()

    dumped = "\n".join(t.model_dump_json() for t in world.traces)
    assert not [text for text in texts if text in dumped]
    assert not [title for title in titles if title in dumped]
    assert not [name for name in names if name in dumped]


# ----------------------------------------------------------- failures and gates


def test_a_failed_step_publishes_nothing_records_nothing_and_the_event_comes_back(
    world: World,
) -> None:
    world.load()
    key = "hr_docs:HR-PL-04"
    world.embedder.down.add(key)
    world.docs.put("/pages/HR-PL-04", json={"body": "# Remote work\n\n## Rule\nFriday too.\n"})

    with pytest.raises(EventFailedError, match="attempt 1 of 3 failed at T7 embed"):
        world.listing()

    failed = world.last(key)
    assert (failed.status, failed.attempt, failed.action) == (TraceStatus.FAILED, 1, None)
    assert failed.failed_step == "T7 embed"
    assert failed.error == "GatewayError"
    assert "connection refused" not in failed.model_dump_json()
    assert [s.step for s in failed.steps] == DOCUMENT_STEPS[:-1]  # nothing after T7
    record = world.changelog.registry.get(key)
    assert record is not None
    assert record.version == "1.1"  # T2 recorded nothing
    assert {c.document_version for c in world.stores.search.chunks(key)} == {"1.1"}
    assert world.lag() == 1  # still pending

    world.embedder.down.clear()
    assert world.pipeline.run() == 1
    retried = world.last(key)
    assert (retried.status, retried.attempt) == (TraceStatus.PUBLISHED, 2)
    assert {c.document_version for c in world.stores.search.chunks(key)} == {"2.1"}
    assert world.lag() == 0


def test_a_revocation_in_the_same_save_does_not_wait_for_a_model(world: World) -> None:
    world.load()
    key = "hr_docs:HR-PL-05"
    assert key in world.pages("dana")
    world.embedder.down.add(key)
    world.docs.put("/pages/HR-PL-05", json={"body": "# Teletrabajo\n\nDos días.\n"})
    world.docs.put("/pages/HR-PL-05/acl", json={"groups": ["es-employees"]})

    with pytest.raises(EventFailedError):
        world.listing()  # one upsert: new text and new readers

    assert key not in world.pages("dana")  # revoked before T3 ran
    assert key in world.pages("lucia")  # old text, narrowed readers
    assert world.last(key).steps[1].step == "T8 readers first"


def test_an_observer_that_fails_leaves_the_event_unrecorded(world: World) -> None:
    def broken(outcome: Outcome) -> None:
        raise ValueError("the inspector's copy failed")

    pipeline = Pipeline(
        world.pipeline.steps,
        world.changelog,
        world.stores,
        observers=[broken],
        on_trace=world.traces.append,
    )
    world.connectors.listings[0].sync()

    with pytest.raises(EventFailedError, match="failed at finish"):
        pipeline.run()
    assert world.changelog.registry.records() == []


# -------------------------------------------------------------- dead letters


def _park(world: World, key: str) -> None:
    """Fail ``key``'s next version three times: the third attempt parks it."""
    world.embedder.down.add(key)
    page = key.partition(":")[2]
    world.docs.put(f"/pages/{page}", json={"body": "# Expenses\n\n## Rule\nKeep receipts.\n"})
    for attempt in (1, 2):
        with pytest.raises(EventFailedError, match=f"attempt {attempt} of 3"):
            world.listing() if attempt == 1 else world.pipeline.run()
    world.pipeline.run()  # the third attempt parks it and moves on


def test_an_event_that_keeps_failing_is_parked_and_its_item_withdrawn(world: World) -> None:
    world.load()
    key = "hr_docs:HR-PL-06"
    published = len(world.stores.search.chunks(key))
    _park(world, key)

    parked = world.last(key)
    assert (parked.status, parked.attempt) == (TraceStatus.PARKED, 3)
    assert parked.steps[-1].step == "dead letters"
    assert "parked after 3 failed attempts" in parked.steps[-1].detail
    assert f"({published} chunks out of the search store)" in parked.steps[-1].detail
    # Fail closed: no one reads a version that may be out of date.
    assert world.stores.search.chunks(key) == ()
    assert world.stores.permissions.readers(key) is None
    assert world.pipeline.dead_letters.withdrawn(key)
    assert len(world.pipeline.dead_letters) == 1
    assert world.lag() == 0  # committed: the rest of the bus moves on


def test_other_items_move_on_and_the_items_later_events_wait_behind_it(world: World) -> None:
    world.load()
    key = "hr_docs:HR-PL-06"
    _park(world, key)
    world.docs.put("/pages/HR-PL-09", json={"body": "# Onboarding\n\nDay one.\n"})
    world.docs.put(f"/pages/{key.partition(':')[2]}/acl", json={"groups": ["hr-team"]})
    world.listing()

    assert world.last("hr_docs:HR-PL-09").status is TraceStatus.PUBLISHED
    behind = world.last(key)
    assert (behind.status, behind.action) == (TraceStatus.PARKED, None)
    assert [s.step for s in behind.steps] == ["dead letters"]
    assert behind.steps[0].detail.startswith("parked behind v2.1 (2 parked for this item)")
    assert world.stores.permissions.readers(key) is None  # the readers change waits too


def test_a_retry_replays_the_parked_events_in_order(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.load()
    key = "hr_docs:HR-PL-06"
    _park(world, key)
    world.docs.put(f"/pages/{key.partition(':')[2]}/acl", json={"groups": ["hr-team"]})
    world.listing()
    world.embedder.down.clear()  # the model is back

    apply_readers = world.pipeline.steps.t8.apply_readers
    fail_once = True

    def check_hidden(event: ChangeEvent, *, processing_content: bool = False) -> PermissionResult:
        nonlocal fail_once
        assert not world.stores.search.chunks(key)
        if fail_once:
            fail_once = False
            raise ValueError("permission store unavailable")
        return apply_readers(event, processing_content=processing_content)

    monkeypatch.setattr(world.pipeline.steps.t8, "apply_readers", check_hidden)

    outcomes = world.pipeline.retry_parked(key)
    assert [o.trace.status for o in outcomes] == [TraceStatus.PUBLISHED]
    assert world.stores.search.chunks(key) == ()
    assert world.pipeline.dead_letters.holds(key)
    outcomes += world.pipeline.retry_parked(key)
    assert [o.trace.status for o in outcomes] == [TraceStatus.PUBLISHED, TraceStatus.READERS]
    chunks = world.stores.search.chunks(key)
    assert {c.document_version for c in chunks} == {"2.1"}
    assert {c.acl for c in chunks} == {Acl(groups=frozenset({"hr-team"}))}
    assert len(world.pipeline.dead_letters) == 0
    assert not world.pipeline.dead_letters.withdrawn(key)


def test_a_retry_that_fails_again_puts_everything_back_in_order(world: World) -> None:
    world.load()
    key = "hr_docs:HR-PL-06"
    _park(world, key)
    world.docs.put(f"/pages/{key.partition(':')[2]}/acl", json={"groups": ["hr-team"]})
    world.listing()

    assert world.pipeline.retry_parked() == []  # the model is still down

    parked = world.pipeline.dead_letters.items()[key]
    assert [p.delivery.event.op for p in parked] == [ChangeOp.UPSERT, ChangeOp.ACL_CHANGE]
    assert parked[0].error == "GatewayError"
    assert world.pipeline.dead_letters.withdrawn(key)
    assert world.stores.search.chunks(key) == ()


def test_a_delete_is_never_parked_and_discards_what_was(world: World) -> None:
    world.load()
    key = "hr_docs:HR-PL-06"
    _park(world, key)
    world.docs.delete(f"/pages/{key.partition(':')[2]}")
    world.listing()

    purged = world.last(key)
    assert purged.status is TraceStatus.PURGED
    assert purged.steps[0].detail == "1 parked events discarded: the delete supersedes them"
    assert len(world.pipeline.dead_letters) == 0
    assert not world.pipeline.dead_letters.withdrawn(key)


def test_a_delete_that_keeps_failing_stays_pending_and_blocks(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.load()

    def broken(key: str) -> None:
        raise ValueError("the entity store is down")

    monkeypatch.setattr(world.pipeline.steps.entities, "forget", broken)
    world.docs.delete("/pages/HR-PL-99")
    with pytest.raises(EventFailedError, match="attempt 1 of 3"):
        world.listing()
    for attempt in (2, 3, 4):
        with pytest.raises(EventFailedError, match=f"attempt {attempt} of 3"):
            world.pipeline.run()
    assert len(world.pipeline.dead_letters) == 0
    assert world.lag() == 1
    monkeypatch.undo()
    world.pipeline.run()
    assert world.last("hr_docs:HR-PL-99").status is TraceStatus.PURGED


@pytest.mark.parametrize("recovery", ["normal", "rebuild_fails", "delete_pending"])
def test_a_parked_readers_change_is_rebuilt_from_the_stored_original(
    world: World, monkeypatch: pytest.MonkeyPatch, recovery: str
) -> None:
    world.load()
    key = "hr_docs:HR-PL-06"
    published = len(world.stores.search.chunks(key))
    t8 = world.pipeline.steps.t8

    def down(*args: object, **kwargs: object) -> None:
        raise ValueError("the permission store is down")

    monkeypatch.setattr(t8, "apply_readers", down)
    world.docs.put("/pages/HR-PL-06/acl", json={"groups": ["hr-team"]})
    with pytest.raises(EventFailedError):
        world.listing()
    assert world.stores.search.chunks(key) == ()  # fail closed on the first ACL error
    for _ in (2, 3):
        with contextlib.suppress(EventFailedError):
            world.pipeline.run()
    assert world.last(key).status is TraceStatus.PARKED
    assert world.stores.search.chunks(key) == ()
    monkeypatch.undo()

    if recovery == "delete_pending":
        world.docs.delete("/pages/HR-PL-06")
        for connector in world.connectors.listings:
            connector.sync()
        world.pipeline.retry_parked()
        assert world.stores.search.chunks(key) == ()
        assert all(trace.status is not TraceStatus.REBUILT for trace in world.traces)
        return
    if recovery == "rebuild_fails":
        world.embedder.down.add(key)
        world.pipeline.retry_parked()
        assert world.last(key).failed_step == "T7 embed"
        assert key in world.pipeline.dead_letters.items()
        assert world.stores.search.chunks(key) == ()
        world.embedder.down.clear()
    outcomes = world.pipeline.retry_parked(key)

    # The readers change alone republishes nothing, so the item is rebuilt.
    first = TraceStatus.SKIPPED if recovery == "rebuild_fails" else TraceStatus.READERS
    assert [o.trace.status for o in outcomes] == [first, TraceStatus.REBUILT]
    rebuilt = outcomes[-1].trace
    assert (rebuilt.partition, rebuilt.offset, rebuilt.action) == (None, None, None)
    assert rebuilt.steps[0].step == "rebuild"
    chunks = world.stores.search.chunks(key)
    assert len(chunks) == published
    assert {c.acl for c in chunks} == {Acl(groups=frozenset({"hr-team"}))}
    assert not world.pipeline.dead_letters.withdrawn(key)


def test_max_attempts_must_be_positive(world: World) -> None:
    with pytest.raises(ValueError, match="at least 1"):
        Pipeline(world.pipeline.steps, world.changelog, world.stores, max_attempts=0)


def test_a_model_option_without_the_gateway_is_refused() -> None:
    choices = choose(load_settings(profile="local"))  # t4_header model, t7_embed model
    changelog = build_changelog(choices)
    with pytest.raises(ValueError, match="needs the model gateway"):
        build_steps(choices, changelog, build_stores(choices, changelog))


def test_listing_catches_silent_delete_of_parked_first_version(world: World) -> None:
    key = "hr_docs:HR-PL-06"
    world.embedder.down.add(key)
    with pytest.raises(EventFailedError):
        world.load()
    with pytest.raises(EventFailedError):
        world.pipeline.run()
    world.pipeline.run()
    assert world.changelog.registry.get(key) is None
    assert world.pipeline.dead_letters.holds(key)
    assert world.listing() == 0
    world.docs.delete("/pages/HR-PL-06")
    world.listing()
    assert world.last(key).status is TraceStatus.PURGED
    assert not world.pipeline.dead_letters.holds(key)
    assert world.pipeline.retry_parked() == []
    assert not world.stores.search.chunks(key)


def test_failed_membership_update_hides_principals_until_retry(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.load()
    membership = world.stores.permissions.membership("alice")
    assert membership is not None

    def fail(event: ChangeEvent) -> None:
        raise ValueError("private source content")

    monkeypatch.setattr(world.pipeline.steps.memberships, "handle", fail)
    world.idp.delete("/scim/v2/Groups/it-employees/members/alice")
    with pytest.raises(EventFailedError):
        world.frequent()
    assert world.stores.permissions.principals("alice") is None
    assert "private source content" not in world.last(membership.source_key).model_dump_json()
    monkeypatch.undo()
    world.pipeline.run()
    updated = world.stores.permissions.membership("alice")
    assert updated is not None
    assert "it-employees" not in updated.groups


def test_failed_withdrawal_keeps_the_event_pending_and_hidden(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.load()
    key = "hr_docs:HR-PL-06"

    def unavailable(document_key: str) -> int:
        raise RuntimeError("store unavailable")

    monkeypatch.setattr(world.stores.search, "remove", unavailable)
    with pytest.raises(EventFailedError):
        _park(world, key)
    assert world.last(key).steps[-1].step == "dead letters"
    assert world.last(key).steps[-1].detail == "RuntimeError"
    assert world.lag() == 1
    assert world.stores.search.chunks(key) == ()
    assert not world.pipeline.dead_letters.holds(key)
    monkeypatch.undo()
    world.pipeline.run()
    assert world.pipeline.dead_letters.holds(key)
    assert world.lag() == 0
