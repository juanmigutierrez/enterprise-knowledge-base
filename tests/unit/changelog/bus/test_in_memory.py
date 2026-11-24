"""The in-memory bus keeps the three promises of the port: order per document,
at least once, and no skipping."""

from __future__ import annotations

import threading
from unittest.mock import patch

import pytest

from ekb.changelog.bus.options.in_memory import InMemoryEventBus, partition_for
from ekb.changelog.bus.port import (
    Delivery,
    OutOfOrderCommitError,
    UnknownDeliveryError,
)
from ekb.contracts import Acl, ChangeOp
from tests.unit.changelog.factories import acl_change, delete, upsert

PAGES = ["HR-CMP-01", *(f"HR-PL-{i:02d}" for i in range(1, 11)), "HR-PL-99"]


def _keys(deliveries: list[Delivery]) -> list[tuple[str, str]]:
    return [(d.event.source_id, d.event.version) for d in deliveries]


def test_a_document_always_lands_in_the_same_partition_in_publish_order() -> None:
    bus = InMemoryEventBus(partitions=3)
    landed = [bus.publish(upsert("HR-PL-04", f"{rev}.1")) for rev in (1, 2, 3)]
    assert {d.partition for d in landed} == {partition_for("hr_docs:HR-PL-04", 3)}
    assert [d.offset for d in landed] == [0, 1, 2]


def test_the_partition_is_stable_across_processes() -> None:
    # Python's hash() is salted per process; a fixed hash keeps a document in one
    # partition forever. These values must never change.
    assert partition_for("hr_docs:HR-PL-04", 3) == 0
    assert partition_for("hr_docs:HR-PL-05", 3) == 1
    assert partition_for("hr_docs:HR-PL-01", 3) == 2


def test_documents_spread_over_the_partitions() -> None:
    bus = InMemoryEventBus(partitions=3)
    for page in PAGES:
        bus.publish(upsert(page))
    ends = bus.stats().end_offsets
    assert sum(ends) == len(PAGES)
    assert all(end > 0 for end in ends)


def test_poll_returns_pending_events_oldest_first_without_moving_the_group() -> None:
    bus = InMemoryEventBus()
    for page, version in [("HR-PL-04", "1.1"), ("HR-PL-05", "1.1"), ("HR-PL-01", "1.1")]:
        bus.publish(upsert(page, version))
    bus.publish(upsert("HR-PL-04", "2.1"))
    first = bus.poll("worker")
    assert _keys(first) == [
        ("HR-PL-04", "1.1"),
        ("HR-PL-05", "1.1"),
        ("HR-PL-01", "1.1"),
        ("HR-PL-04", "2.1"),
    ]
    assert bus.poll("worker") == first  # nothing committed, nothing moved


def test_uncommitted_events_are_delivered_again_after_a_crash() -> None:
    bus = InMemoryEventBus()
    for page in ("HR-PL-04", "HR-PL-05"):
        bus.publish(upsert(page))
    first, _ = bus.poll("worker")
    bus.commit("worker", first)
    # The worker crashes before committing the second event: it is still pending.
    assert _keys(bus.poll("worker")) == [("HR-PL-05", "1.1")]


def test_committing_moves_the_group_forward_and_reduces_its_lag() -> None:
    bus = InMemoryEventBus()
    for page in PAGES:
        bus.publish(upsert(page))
    deliveries = bus.poll("worker")
    for delivery in deliveries[:5]:
        bus.commit("worker", delivery)
    (position,) = bus.stats().groups
    assert (position.group, position.lag) == ("worker", len(PAGES) - 5)
    assert sum(position.committed) == 5
    assert bus.poll("worker") == deliveries[5:]


def test_a_commit_that_skips_a_pending_event_is_refused() -> None:
    bus = InMemoryEventBus(partitions=1)
    bus.publish(upsert("HR-PL-99"))
    bus.publish(delete("HR-PL-99", "1.1"))
    bus.publish(upsert("HR-PL-04"))
    _, the_delete, later = bus.poll("worker")
    with pytest.raises(OutOfOrderCommitError, match="could lose a delete"):
        bus.commit("worker", later)
    with pytest.raises(OutOfOrderCommitError):
        bus.commit("worker", the_delete)
    assert len(bus.poll("worker")) == 3  # nothing moved


def test_committing_the_same_event_twice_is_harmless() -> None:
    bus = InMemoryEventBus()
    delivery = bus.publish(upsert("HR-PL-04"))
    bus.commit("worker", delivery)
    bus.commit("worker", delivery)
    assert bus.poll("worker") == []
    assert bus.stats().groups[0].lag == 0


@pytest.mark.parametrize(("partition", "offset"), [(3, 0), (-1, 0), (0, 1), (0, -1)])
def test_a_delivery_the_bus_never_handed_out_cannot_be_committed(
    partition: int, offset: int
) -> None:
    bus = InMemoryEventBus(partitions=3)
    real = bus.publish(upsert("HR-PL-04"))  # partition 0, offset 0
    with pytest.raises(UnknownDeliveryError):
        bus.commit("worker", Delivery(partition, offset, real.event))


def test_consumer_groups_are_independent() -> None:
    bus = InMemoryEventBus()
    for page in ("HR-PL-04", "HR-PL-05"):
        bus.publish(upsert(page))
    for delivery in bus.poll("registry"):
        bus.commit("registry", delivery)
    assert bus.poll("registry") == []
    assert len(bus.poll("inspector")) == 2
    lags = {g.group: g.lag for g in bus.stats().groups}
    assert lags == {"inspector": 2, "registry": 0}


def test_a_new_group_replays_the_whole_history() -> None:
    bus = InMemoryEventBus()
    history = [
        upsert("HR-PL-99"),
        upsert("HR-PL-04"),
        delete("HR-PL-99", "1.1"),
        upsert("HR-PL-04", "2.1"),
    ]
    for event in history:
        bus.publish(event)
    for delivery in bus.poll("worker"):
        bus.commit("worker", delivery)
    replay = bus.poll("reindex")
    assert [d.event for d in replay] == history


def test_max_events_returns_a_prefix_that_keeps_each_documents_order() -> None:
    bus = InMemoryEventBus()
    for rev in range(1, 6):
        for page in ("HR-PL-04", "HR-PL-05"):
            bus.publish(upsert(page, f"{rev}.1"))
    batch = bus.poll("worker", max_events=3)
    assert _keys(batch) == [("HR-PL-04", "1.1"), ("HR-PL-05", "1.1"), ("HR-PL-04", "2.1")]


def test_events_pass_through_unchanged_and_a_delete_stays_empty() -> None:
    bus = InMemoryEventBus()
    revoke = acl_change("HR-PL-05", "1.2", Acl(groups=frozenset({"es-employees"})))
    gone = delete("HR-PL-99", "1.1", changed_by="dana")
    for event in (revoke, gone):
        bus.publish(event)
    delivered = [d.event for d in bus.poll("worker")]
    assert delivered == [revoke, gone]
    assert delivered[1].op is ChangeOp.DELETE
    assert (delivered[1].raw_pointer, delivered[1].content_hash, delivered[1].acl) == (
        None,
        None,
        Acl(),
    )


def test_stats_show_partition_sizes_and_each_groups_position() -> None:
    bus = InMemoryEventBus(partitions=2)
    assert (bus.stats().end_offsets, bus.stats().groups, bus.stats().events) == ((0, 0), (), 0)
    for page in PAGES:
        bus.publish(upsert(page))
    bus.poll("inspector")  # a group appears once it has read
    stats = bus.stats()
    assert stats.events == len(PAGES)
    (inspector,) = stats.groups
    assert (inspector.committed, inspector.lag) == ((0, 0), len(PAGES))


@pytest.mark.parametrize("partitions", [0, -1])
def test_partitions_must_be_positive(partitions: int) -> None:
    with pytest.raises(ValueError, match="positive"):
        InMemoryEventBus(partitions=partitions)


def test_max_events_must_be_positive() -> None:
    with pytest.raises(ValueError, match="positive"):
        InMemoryEventBus().poll("worker", max_events=0)


def test_concurrent_publishers_lose_nothing_and_keep_each_documents_order() -> None:
    bus = InMemoryEventBus(partitions=3)

    def publish(page: str) -> None:
        for rev in range(1, 101):
            bus.publish(upsert(page, f"{rev}.1"))

    threads = [threading.Thread(target=publish, args=(page,)) for page in PAGES[:4]]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    deliveries = bus.poll("worker", max_events=10_000)
    assert len(deliveries) == 400
    for page in PAGES[:4]:
        revs = [d.event.version for d in deliveries if d.event.source_id == page]
        assert revs == [f"{rev}.1" for rev in range(1, 101)]
    for partition in range(3):
        offsets = [d.offset for d in deliveries if d.partition == partition]
        assert offsets == list(range(len(offsets)))


def test_wrong_event_cannot_acknowledge_a_pending_delete() -> None:
    bus = InMemoryEventBus(partitions=1)
    pending = bus.publish(delete("removed", "1.1"))
    with pytest.raises(UnknownDeliveryError):
        bus.commit("worker", Delivery(pending.partition, pending.offset, upsert("other")))
    assert bus.poll("worker") == [pending]


def test_small_poll_materializes_only_a_bounded_prefix() -> None:
    bus = InMemoryEventBus(partitions=1)
    history = [bus.publish(upsert("page", str(i))) for i in range(100)]
    with patch("ekb.changelog.bus.options.in_memory.Delivery", wraps=Delivery) as construct:
        assert bus.poll("worker", max_events=2) == history[:2]
        assert construct.call_count <= 2
