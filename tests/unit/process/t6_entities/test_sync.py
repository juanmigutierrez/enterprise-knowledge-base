"""The entity store kept current from HR record events (S9): put, replace, remove."""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from ekb.changelog.rawstore.options.in_memory import InMemoryRawStore
from ekb.contracts import ChangeEvent, ChangeOp
from ekb.process.t6_entities.records import RecordError, hr_record
from ekb.process.t6_entities.sync import EntitySync
from ekb.stores.entities.options.in_memory import InMemoryEntityStore
from tests.unit.process.t6_entities.test_records import EMPLOYEE, NOW, event, stored


def setup() -> tuple[EntitySync, InMemoryEntityStore, InMemoryRawStore]:
    store, raw = InMemoryEntityStore(), InMemoryRawStore()
    return EntitySync(store, raw, {"hr_api": hr_record}), store, raw


def upsert(
    raw: InMemoryRawStore, record: Mapping[str, object], *, person_id: str | None
) -> ChangeEvent:
    base = event(f"employees/{record['id']}", person_id=person_id)
    version = str(record.get("updated_at", base.version))
    pointer = raw.put(base.document_key, version, stored(record))
    return base.model_copy(update={"raw_pointer": pointer, "version": version})


def test_an_employee_record_puts_its_person_and_a_purge_removes_them() -> None:
    sync, store, raw = setup()
    result = sync.handle(upsert(raw, EMPLOYEE, person_id="lucia"))
    assert result.reason == "person:lucia added: Lucía Navarro"
    assert store.get("person:lucia") == result.entity
    removed = sync.forget("hr_api:employees/E-1009")
    assert removed is not None
    assert removed.entity_id == "person:lucia"
    assert store.get("person:lucia") is None
    assert sync.forget("hr_api:employees/E-1009") is None  # a repeated purge is harmless


def test_a_rename_replaces_the_entity_and_reads_the_version_the_event_names() -> None:
    sync, store, raw = setup()
    sync.handle(upsert(raw, EMPLOYEE, person_id="lucia"))
    renamed = {**EMPLOYEE, "name": "Lucía Navarro Gil", "updated_at": "2026-11-26T00:00:00+00:00"}
    result = sync.handle(upsert(raw, renamed, person_id="lucia"))
    assert result.replaced is not None
    assert result.replaced.name == "Lucía Navarro"
    assert result.reason == "person:lucia updated: Lucía Navarro Gil"
    assert [e.name for e in store.entities()] == ["Lucía Navarro Gil"]


def test_a_record_that_no_longer_maps_to_a_person_drops_its_old_entity() -> None:
    sync, store, raw = setup()
    sync.handle(upsert(raw, EMPLOYEE, person_id="lucia"))
    later = {**EMPLOYEE, "updated_at": "2026-11-26T00:00:00+00:00"}
    result = sync.handle(upsert(raw, later, person_id=None))
    assert result.entity is None
    assert "fail closed" in result.reason
    assert "person:lucia removed" in result.reason
    assert store.entities() == ()


def test_a_source_that_defines_no_entities_or_an_event_that_is_not_an_upsert_is_refused() -> None:
    sync, _, raw = setup()
    assert sync.handles("hr_api")
    assert not sync.handles("hr_docs")
    docs = upsert(raw, EMPLOYEE, person_id="lucia").model_copy(update={"source": "hr_docs"})
    with pytest.raises(ValueError, match="defines no entities"):
        sync.handle(docs)
    delete = ChangeEvent(
        source="hr_api",
        source_id="employees/E-1009",
        op=ChangeOp.DELETE,
        version="2",
        occurred_at=NOW,
    )
    with pytest.raises(ValueError, match="only an upsert"):
        sync.handle(delete)


def test_a_broken_record_raises_and_leaves_the_store_as_it_was() -> None:
    sync, store, raw = setup()
    sync.handle(upsert(raw, EMPLOYEE, person_id="lucia"))
    broken = {"id": "E-1009", "updated_at": "2026-11-26T00:00:00+00:00"}  # no name
    with pytest.raises(RecordError):
        sync.handle(upsert(raw, broken, person_id="lucia"))
    assert [e.name for e in store.entities()] == ["Lucía Navarro"]
