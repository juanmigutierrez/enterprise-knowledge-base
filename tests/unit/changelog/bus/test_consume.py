"""``drain``: handle, commit, next. A failure stops the drain and loses nothing."""

from __future__ import annotations

import pytest

from ekb.changelog.bus.consume import drain
from ekb.changelog.bus.options.in_memory import InMemoryEventBus
from ekb.changelog.bus.port import Delivery
from tests.unit.changelog.factories import delete, upsert


def _bus(*source_ids: str) -> InMemoryEventBus:
    bus = InMemoryEventBus(partitions=1)
    for source_id in source_ids:
        bus.publish(upsert(source_id))
    return bus


def test_drain_hands_over_every_pending_event_in_order_and_commits_it() -> None:
    bus = _bus("HR-PL-01", "HR-PL-02", "HR-PL-03")
    seen: list[str] = []
    assert drain(bus, "worker", lambda d: seen.append(d.event.source_id)) == 3
    assert seen == ["HR-PL-01", "HR-PL-02", "HR-PL-03"]
    assert bus.stats().groups[0].lag == 0
    assert drain(bus, "worker", lambda d: seen.append(d.event.source_id)) == 0


def test_drain_works_through_more_events_than_one_batch() -> None:
    bus = _bus(*(f"HR-PL-{i:02d}" for i in range(1, 8)))
    assert drain(bus, "worker", lambda d: None, batch=2) == 7


def test_a_failing_event_stops_the_drain_and_it_and_everything_after_stay_pending() -> None:
    bus = _bus("HR-PL-01", "HR-PL-02")
    bus.publish(delete("HR-PL-99", "1.1"))
    handled: list[str] = []

    def handle(delivery: Delivery) -> None:
        if delivery.event.source_id == "HR-PL-02":
            raise RuntimeError("the worker crashed")
        handled.append(delivery.event.source_id)

    with pytest.raises(RuntimeError, match="crashed"):
        drain(bus, "worker", handle)
    assert handled == ["HR-PL-01"]
    pending = [d.event.source_id for d in bus.poll("worker")]
    assert pending == ["HR-PL-02", "HR-PL-99"]  # the delete after it was not skipped

    assert drain(bus, "worker", lambda d: handled.append(d.event.source_id)) == 2
    assert handled == ["HR-PL-01", "HR-PL-02", "HR-PL-99"]
