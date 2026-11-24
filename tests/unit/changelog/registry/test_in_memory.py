"""The in-memory registry: one record per item, and what the full listings compare against."""

from __future__ import annotations

from ekb.changelog.registry.options.in_memory import InMemoryRegistry
from ekb.changelog.registry.port import record_event
from tests.unit.changelog.factories import delete, upsert


def test_an_item_never_recorded_is_unknown() -> None:
    assert InMemoryRegistry().get("hr_docs:HR-PL-04") is None


def test_recording_events_keeps_the_latest_state_per_item() -> None:
    registry = InMemoryRegistry()
    record_event(registry, upsert("HR-PL-04", "1.1"))
    latest = record_event(registry, upsert("HR-PL-04", "2.1"))
    assert registry.get("hr_docs:HR-PL-04") == latest
    assert (latest.version, latest.events) == ("2.1", 2)


def test_known_lists_the_sources_live_items_and_leaves_out_tombstones() -> None:
    registry = InMemoryRegistry()
    for event in (
        upsert("HR-PL-04", "2.1"),
        upsert("HR-PL-99"),
        delete("HR-PL-99", "1.1"),
        upsert("employees/E-1001", "2026-11-24T09:00:00+00:00", source="hr_api"),
    ):
        record_event(registry, event)
    assert registry.known("hr_docs") == {"HR-PL-04": "2.1"}
    assert registry.known("hr_api") == {"employees/E-1001": "2026-11-24T09:00:00+00:00"}
    assert registry.known("identity") == {}


def test_records_include_tombstones_sorted_and_filtered_by_source() -> None:
    registry = InMemoryRegistry()
    for event in (
        upsert("HR-PL-99"),
        upsert("employees/E-1001", "t", source="hr_api"),
        upsert("HR-PL-04"),
        delete("HR-PL-99", "1.1"),
    ):
        record_event(registry, event)
    keys = [r.document_key for r in registry.records()]
    assert keys == ["hr_api:employees/E-1001", "hr_docs:HR-PL-04", "hr_docs:HR-PL-99"]
    docs = registry.records("hr_docs")
    assert [(r.source_id, r.deleted) for r in docs] == [("HR-PL-04", False), ("HR-PL-99", True)]
