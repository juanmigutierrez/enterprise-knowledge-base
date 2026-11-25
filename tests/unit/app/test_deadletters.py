"""The dead letters (S12): parked events per item, in bus order."""

from __future__ import annotations

from ekb.app.deadletters import DeadLetters
from ekb.changelog.bus.port import Delivery
from tests.unit.changelog.factories import upsert


def _delivery(source_id: str, version: str, offset: int) -> Delivery:
    return Delivery(0, offset, upsert(source_id, version, raw_pointer=f"mem://{source_id}"))


def test_events_are_parked_per_item_in_order() -> None:
    letters = DeadLetters()
    assert letters.park(_delivery("HR-PL-01", "2.1", 4), "boom") == 1
    assert letters.park(_delivery("HR-PL-01", "2.2", 7)) == 2
    letters.park(_delivery("HR-PL-02", "1.2", 5), "bang")

    assert len(letters) == 3
    assert letters.holds("hr_docs:HR-PL-01")
    first = letters.first("hr_docs:HR-PL-01")
    assert first is not None
    assert (first.delivery.offset, first.error) == (4, "boom")
    assert list(letters.items()) == ["hr_docs:HR-PL-01", "hr_docs:HR-PL-02"]


def test_take_empties_an_item_and_put_back_goes_ahead_of_newer_ones() -> None:
    letters = DeadLetters()
    letters.park(_delivery("HR-PL-01", "2.1", 4), "boom")
    letters.park(_delivery("HR-PL-01", "2.2", 7))

    taken = letters.take("hr_docs:HR-PL-01")
    assert [p.delivery.offset for p in taken] == [4, 7]
    assert not letters.holds("hr_docs:HR-PL-01")
    assert letters.first("hr_docs:HR-PL-01") is None

    letters.park(_delivery("HR-PL-01", "2.3", 9))  # arrived during the retry
    letters.put_back("hr_docs:HR-PL-01", taken[1:])
    parked = letters.items()["hr_docs:HR-PL-01"]
    assert [p.delivery.offset for p in parked] == [7, 9]


def test_withdrawn_items_are_tracked_until_restored() -> None:
    letters = DeadLetters()
    letters.withdraw("hr_docs:HR-PL-01")
    assert letters.withdrawn("hr_docs:HR-PL-01")
    letters.restored("hr_docs:HR-PL-01")
    letters.restored("hr_docs:HR-PL-02")  # harmless
    assert not letters.withdrawn("hr_docs:HR-PL-01")
