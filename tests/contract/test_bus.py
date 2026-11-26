"""Every bus option keeps the port's three promises (S17): order per document, at
least once, no skipping, and the rest of what ``EventBus`` says.

The same tests run on ``in_memory``, on ``kafka`` over a fake log (every pull
request), and on ``kafka`` against the broker in ``docker compose``
(``-m integration``, after ``make up``). Only the durable options can be opened
again "from another process"; those tests skip the in-memory bus.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

import pytest

from ekb.changelog.bus.options.in_memory import InMemoryEventBus, partition_for
from ekb.changelog.bus.options.kafka import KafkaEventBus
from ekb.changelog.bus.port import (
    Delivery,
    EventBus,
    OutOfOrderCommitError,
    UnknownDeliveryError,
)
from ekb.contracts import Acl, ChangeEvent, ChangeOp
from ekb.platform.infra import new_namespace
from tests.fakes.kafka import FakeKafka
from tests.unit.changelog.factories import acl_change, delete, upsert

PAGES = ["HR-CMP-01", *(f"HR-PL-{i:02d}" for i in range(1, 11)), "HR-PL-99"]


@dataclass
class Buses:
    """Makes buses of one option; ``reopen`` is the same log seen from a new process."""

    option: str
    fake: FakeKafka = field(default_factory=FakeKafka)
    namespace: str = field(default_factory=lambda: new_namespace("test"))
    made: list[EventBus] = field(default_factory=list)

    @property
    def durable(self) -> bool:
        return self.option != "in_memory"

    def new(self, partitions: int = 3) -> EventBus:
        bus: EventBus
        if self.option == "in_memory":
            bus = InMemoryEventBus(partitions)
        elif self.option == "kafka_fake":
            bus = KafkaEventBus(partitions, namespace=self.namespace, log=self.fake)
        else:
            bus = KafkaEventBus(partitions, namespace=self.namespace)
        self.made.append(bus)
        return bus

    def reopen(self, bus: EventBus) -> EventBus:
        if not self.durable:
            pytest.skip("an in-memory bus does not outlive its process")
        return self.new(bus.partitions)

    def cleanup(self) -> None:
        if self.option == "kafka" and self.made:
            first = self.made[0]
            assert isinstance(first, KafkaEventBus)
            first.destroy()
            for bus in self.made[1:]:
                assert isinstance(bus, KafkaEventBus)
                bus.close()


@pytest.fixture(
    params=["in_memory", "kafka_fake", pytest.param("kafka", marks=pytest.mark.integration)]
)
def buses(request: pytest.FixtureRequest) -> Iterator[Buses]:
    made = Buses(str(request.param))
    try:
        yield made
    finally:
        made.cleanup()


def _keys(deliveries: list[Delivery]) -> list[tuple[str, str]]:
    return [(d.event.source_id, d.event.version) for d in deliveries]


def _per_document(deliveries: list[Delivery]) -> dict[str, list[str]]:
    order: dict[str, list[str]] = {}
    for d in deliveries:
        order.setdefault(d.event.source_id, []).append(d.event.version)
    return order


def test_a_document_always_lands_in_the_same_partition_in_publish_order(buses: Buses) -> None:
    bus = buses.new()
    landed = [bus.publish(upsert("HR-PL-04", f"{rev}.1")) for rev in (1, 2, 3)]
    assert {d.partition for d in landed} == {partition_for("hr_docs:HR-PL-04", 3)}
    assert [d.offset for d in landed] == [0, 1, 2]


def test_documents_spread_over_the_partitions(buses: Buses) -> None:
    bus = buses.new()
    for page in PAGES:
        bus.publish(upsert(page))
    ends = bus.stats().end_offsets
    assert sum(ends) == len(PAGES)
    assert all(end > 0 for end in ends)


def test_poll_returns_each_documents_events_in_order_without_moving_the_group(
    buses: Buses,
) -> None:
    bus = buses.new()
    for page, version in [("HR-PL-04", "1.1"), ("HR-PL-05", "1.1"), ("HR-PL-04", "2.1")]:
        bus.publish(upsert(page, version))
    first = bus.poll("worker")
    assert _per_document(first) == {"HR-PL-04": ["1.1", "2.1"], "HR-PL-05": ["1.1"]}
    assert bus.poll("worker") == first  # nothing committed, nothing moved


def test_uncommitted_events_are_delivered_again(buses: Buses) -> None:
    bus = buses.new(partitions=1)
    for page in ("HR-PL-04", "HR-PL-05"):
        bus.publish(upsert(page))
    first, _ = bus.poll("worker")
    bus.commit("worker", first)
    assert _keys(bus.poll("worker")) == [("HR-PL-05", "1.1")]


def test_a_restarted_process_resumes_where_its_group_committed(buses: Buses) -> None:
    bus = buses.new(partitions=1)
    for page in ("HR-PL-04", "HR-PL-05", "HR-PL-06"):
        bus.publish(upsert(page))
    first, *_ = bus.poll("worker")
    bus.commit("worker", first)
    # The worker crashes; a new process opens the same log.
    again = buses.reopen(bus)
    assert _keys(again.poll("worker")) == [("HR-PL-05", "1.1"), ("HR-PL-06", "1.1")]
    assert again.poll("fresh-group")[0].event == first.event  # a new group starts at 0


def test_committing_moves_the_group_forward_and_reduces_its_lag(buses: Buses) -> None:
    bus = buses.new(partitions=1)
    for page in PAGES:
        bus.publish(upsert(page))
    deliveries = bus.poll("worker")
    for delivery in deliveries[:5]:
        bus.commit("worker", delivery)
    (position,) = (g for g in bus.stats().groups if g.group == "worker")
    assert position.lag == len(PAGES) - 5
    assert sum(position.committed) == 5
    assert bus.poll("worker") == deliveries[5:]


def test_a_commit_that_skips_a_pending_event_is_refused(buses: Buses) -> None:
    bus = buses.new(partitions=1)
    bus.publish(upsert("HR-PL-99"))
    bus.publish(delete("HR-PL-99", "1.1"))
    bus.publish(upsert("HR-PL-04"))
    _, the_delete, later = bus.poll("worker")
    with pytest.raises(OutOfOrderCommitError, match="could lose a delete"):
        bus.commit("worker", later)
    with pytest.raises(OutOfOrderCommitError):
        bus.commit("worker", the_delete)
    assert len(bus.poll("worker")) == 3  # nothing moved


def test_committing_the_same_event_twice_is_harmless(buses: Buses) -> None:
    bus = buses.new()
    delivery = bus.publish(upsert("HR-PL-04"))
    bus.commit("worker", delivery)  # straight from publish: the bus checks it exists
    bus.commit("worker", delivery)
    assert bus.poll("worker") == []
    assert {g.group: g.lag for g in bus.stats().groups}["worker"] == 0


@pytest.mark.parametrize(("partition", "offset"), [(3, 0), (-1, 0), (0, 1), (0, -1)])
def test_a_delivery_the_bus_never_handed_out_cannot_be_committed(
    buses: Buses, partition: int, offset: int
) -> None:
    bus = buses.new(partitions=3)
    real = bus.publish(upsert("HR-PL-04"))  # partition 0, offset 0
    assert (real.partition, real.offset) == (0, 0)
    with pytest.raises(UnknownDeliveryError):
        bus.commit("worker", Delivery(partition, offset, real.event))


def test_a_commit_naming_another_event_is_refused(buses: Buses) -> None:
    bus = buses.new(partitions=1)
    real = bus.publish(upsert("HR-PL-04"))
    (polled,) = bus.poll("worker")
    with pytest.raises(UnknownDeliveryError, match="does not match"):
        bus.commit("worker", Delivery(real.partition, real.offset, upsert("HR-PL-05")))
    assert bus.poll("worker") == [polled]


def test_consumer_groups_are_independent(buses: Buses) -> None:
    bus = buses.new()
    for page in ("HR-PL-04", "HR-PL-05"):
        bus.publish(upsert(page))
    for delivery in bus.poll("registry"):
        bus.commit("registry", delivery)
    assert bus.poll("registry") == []
    assert len(bus.poll("inspector")) == 2
    lags = {g.group: g.lag for g in bus.stats().groups}
    assert (lags["inspector"], lags["registry"]) == (2, 0)


def test_a_new_group_replays_the_whole_history(buses: Buses) -> None:
    bus = buses.new(partitions=1)
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
    assert [d.event for d in bus.poll("reindex")] == history


def test_max_events_returns_a_prefix_that_keeps_each_documents_order(buses: Buses) -> None:
    bus = buses.new()
    for rev in range(1, 6):
        for page in ("HR-PL-04", "HR-PL-05"):
            bus.publish(upsert(page, f"{rev}.1"))
    batch = bus.poll("worker", max_events=3)
    assert len(batch) == 3
    for versions in _per_document(batch).values():
        assert versions == [f"{rev}.1" for rev in range(1, len(versions) + 1)]


def test_events_come_back_equal_and_a_delete_stays_empty(buses: Buses) -> None:
    bus = buses.new(partitions=1)
    revoke = acl_change("HR-PL-05", "1.2", Acl(groups=frozenset({"es-employees"})))
    gone = delete("HR-PL-99", "1.1", changed_by="dana")
    record = upsert("employees/E-1002", "2026-11-24T09:00:00Z", source="hr_api", person_id="lucia")
    for event in (revoke, gone, record):
        bus.publish(event)
    delivered = [d.event for d in bus.poll("worker")]
    assert delivered == [revoke, gone, record]
    assert all(isinstance(e, ChangeEvent) for e in delivered)
    assert delivered[1].op is ChangeOp.DELETE
    assert (delivered[1].raw_pointer, delivered[1].content_hash, delivered[1].acl) == (
        None,
        None,
        Acl(),
    )


def test_stats_show_partition_sizes_and_the_groups_that_read(buses: Buses) -> None:
    bus = buses.new(partitions=2)
    assert (bus.stats().end_offsets, bus.stats().events) == ((0, 0), 0)
    for page in PAGES:
        bus.publish(upsert(page))
    bus.poll("inspector")  # a group appears once it has read
    stats = bus.stats()
    assert stats.events == len(PAGES)
    (inspector,) = (g for g in stats.groups if g.group == "inspector")
    assert (inspector.committed, inspector.lag) == ((0, 0), len(PAGES))


def test_partitions_and_max_events_must_be_positive(buses: Buses) -> None:
    with pytest.raises(ValueError, match="positive"):
        buses.new(partitions=0)
    with pytest.raises(ValueError, match="positive"):
        buses.new().poll("worker", max_events=0)
