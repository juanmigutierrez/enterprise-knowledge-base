"""DiffStep: decide, purge on delete, hand on, then record. Crash-safe under at-least-once."""

from __future__ import annotations

import pytest

from ekb.changelog.bus.consume import drain
from ekb.changelog.bus.options.in_memory import InMemoryEventBus
from ekb.changelog.rawstore.options.in_memory import InMemoryRawStore
from ekb.changelog.rawstore.port import OriginalNotFoundError
from ekb.changelog.registry.options.in_memory import InMemoryRegistry
from ekb.changelog.registry.port import DocumentRecord, record_event
from ekb.contracts import Acl, ChangeEvent, DiffAction
from ekb.process.t2_diff.options.content_hash import ContentHashDiffer
from ekb.process.t2_diff.step import DiffResult, DiffStep
from tests.unit.changelog.factories import acl_change, delete, upsert

KEY = "hr_docs:HR-PL-99"


class World:
    """A registry, a raw store, a T2 step and what it handed downstream."""

    def __init__(self) -> None:
        self.registry = InMemoryRegistry()
        self.rawstore = InMemoryRawStore()
        self.handed: list[DiffResult] = []
        self.step = DiffStep(ContentHashDiffer(), self.registry, self.rawstore, self.handed.append)

    def stored(self, version: str, text: str = "travel", *, lifetime: int = 0) -> ChangeEvent:
        """An upsert whose original the connector stored, as connectors do before publishing."""
        pointer = self.rawstore.put(KEY, version, f"{text} {version}".encode(), lifetime=lifetime)
        return upsert("HR-PL-99", version, raw_pointer=pointer, content_hash=f"sha256:{text}")

    def record(self) -> DocumentRecord:
        record = self.registry.get(KEY)
        assert record is not None
        return record

    def actions(self) -> list[DiffAction]:
        return [r.decision.action for r in self.handed]


@pytest.fixture
def world() -> World:
    return World()


def test_a_decision_is_handed_on_and_then_recorded(world: World) -> None:
    result = world.step.handle(world.stored("1.1"))
    assert result.decision.action is DiffAction.PROCESS
    assert result.purged == 0
    assert world.handed == [result]
    assert world.record().version == "1.1"


def test_a_skipped_event_still_moves_the_registry_to_its_version(world: World) -> None:
    """Otherwise the full listing would keep reporting the new version as a change."""
    world.step.handle(world.stored("1.1"))
    result = world.step.handle(world.stored("2.1"))  # same text, new version
    assert result.decision.action is DiffAction.SKIP
    assert world.record().version == "2.1"


def test_if_the_next_step_fails_nothing_is_recorded(world: World) -> None:
    def failing(result: DiffResult) -> None:
        raise RuntimeError("T3 is down")

    step = DiffStep(ContentHashDiffer(), world.registry, world.rawstore, failing)
    with pytest.raises(RuntimeError, match="T3 is down"):
        step.handle(world.stored("1.1"))
    assert world.registry.get(KEY) is None


def test_a_crash_before_recording_means_the_event_is_processed_again_not_skipped(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the record were written before the hand-on, this redelivery would be skipped."""
    event = world.stored("1.1")

    def crash(record: DocumentRecord) -> None:
        raise RuntimeError("worker died")

    monkeypatch.setattr(world.registry, "put", crash)
    with pytest.raises(RuntimeError):
        world.step.handle(event)
    monkeypatch.undo()
    world.step.handle(event)  # redelivered
    assert world.actions() == [DiffAction.PROCESS, DiffAction.PROCESS]


def test_a_crash_after_recording_makes_the_redelivery_a_skipped_repeat(world: World) -> None:
    event = world.stored("1.1")
    world.step.handle(event)
    world.step.handle(event)  # not committed before the crash, so delivered again
    assert world.actions() == [DiffAction.PROCESS, DiffAction.SKIP]


# ----------------------------------------------------------------------- deletes


def test_a_delete_purges_the_originals_and_leaves_a_tombstone(world: World) -> None:
    """Deletion gate: the content is gone from the raw store, the history stays."""
    first, second = world.stored("1.1", "old"), world.stored("2.1", "new")
    world.step.handle(first)
    world.step.handle(second)
    result = world.step.handle(delete("HR-PL-99", "2.1"))
    assert (result.decision.action, result.purged) == (DiffAction.PURGE, 2)
    assert world.rawstore.versions(KEY) == []
    for event in (first, second):
        assert event.raw_pointer is not None
        with pytest.raises(OriginalNotFoundError):
            world.rawstore.get(event.raw_pointer)
    record = world.record()
    assert record.deleted
    assert (record.raw_pointer, record.content_hash, record.acl) == (None, None, Acl())


def test_a_re_creation_queued_behind_the_delete_keeps_its_original(world: World) -> None:
    """Delete, then re-create, both before T2 runs: the connector already stored the new
    original. Purging every version would leave the re-creation pointing at nothing."""
    world.step.handle(world.stored("1.1", "old"))
    queued = [delete("HR-PL-99", "1.1"), world.stored("2.1", "new", lifetime=1)]
    results = [world.step.handle(event) for event in queued]
    assert [(r.decision.action, r.purged) for r in results] == [
        (DiffAction.PURGE, 1),
        (DiffAction.PROCESS, 0),
    ]
    assert queued[1].raw_pointer is not None
    assert world.rawstore.get(queued[1].raw_pointer) == b"new 2.1"
    assert not world.record().deleted


def test_a_second_delete_is_purged_downstream_but_spares_a_queued_re_creation(
    world: World,
) -> None:
    world.step.handle(world.stored("1.1"))
    world.step.handle(delete("HR-PL-99", "1.1"))
    recreated = world.stored("2.1", "new", lifetime=1)  # stored, its event still queued
    result = world.step.handle(delete("HR-PL-99", "1.1"))  # the full listing's copy
    assert (result.decision.action, result.purged) == (DiffAction.PURGE, 0)
    assert recreated.raw_pointer is not None
    assert world.rawstore.get(recreated.raw_pointer) == b"new 2.1"


def test_a_delete_of_an_item_never_processed_keeps_a_queued_original(world: World) -> None:
    queued = world.stored("1.1", lifetime=1)  # its upsert is behind the delete in the queue
    result = world.step.handle(delete("HR-PL-99", "0.1"))
    assert (result.decision.action, result.purged) == (DiffAction.PURGE, 0)
    assert world.record().deleted
    assert queued.raw_pointer is not None
    assert world.rawstore.get(queued.raw_pointer) == b"travel 1.1"


def test_a_delete_after_an_acl_change_cleared_the_pointer_purges_only_its_lifetime(
    world: World,
) -> None:
    """The delete carries its own boundary even after the registry loses its pointer."""
    world.step.handle(world.stored("1.1"))
    world.step.handle(acl_change("HR-PL-99", "2.2", Acl(), content_hash="sha256:other"))
    assert world.record().raw_pointer is None
    later = world.stored("3.1", "later", lifetime=1)
    result = world.step.handle(delete("HR-PL-99", "2.2"))
    assert result.purged == 1
    assert later.raw_pointer is not None
    assert world.rawstore.get(later.raw_pointer) == b"later 3.1"


# ------------------------------------------------------------------ on the bus


def test_on_the_bus_the_same_change_reported_twice_is_processed_once(world: World) -> None:
    """The webhook and the full listing both report an edit before T2 runs."""
    bus = InMemoryEventBus(partitions=3)
    edit = world.stored("2.1", "edited")
    for event in (world.stored("1.1"), edit, edit):
        bus.publish(event)
    assert drain(bus, "t2_diff", lambda d: world.step.handle(d.event)) == 3
    assert world.actions() == [DiffAction.PROCESS, DiffAction.PROCESS, DiffAction.SKIP]
    assert bus.stats().groups[0].lag == 0


def test_t2_records_exactly_what_the_s3_stand_in_recorded(world: World) -> None:
    """T2 adds decisions and purges; the registry's meaning is unchanged."""
    events = [
        world.stored("1.1"),
        acl_change("HR-PL-99", "1.2", Acl(groups=frozenset({"hr-team"}))),
        world.stored("2.2", "edited"),
        delete("HR-PL-99", "2.2"),
        acl_change("HR-PL-99", "2.3", Acl(groups=frozenset({"it"}))),
    ]
    stand_in = InMemoryRegistry()
    for event in events:
        world.step.handle(event)
        record_event(stand_in, event)
    assert world.registry.records() == stand_in.records()
