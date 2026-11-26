"""Every registry, acceptance log and raw store option keeps its port's promises (S17).

Each test runs in memory, through moto, and (``-m integration``) against
DynamoDB Local and SeaweedFS. The durable options are also opened a second time,
as another process would, and must see the same state.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime

import pytest

from ekb.changelog.acceptance.options.dynamodb import DynamoAcceptanceLog
from ekb.changelog.acceptance.options.in_memory import InMemoryAcceptanceLog
from ekb.changelog.acceptance.port import AcceptanceLog
from ekb.changelog.bus.port import Delivery
from ekb.changelog.rawstore.options.in_memory import InMemoryRawStore
from ekb.changelog.rawstore.options.s3 import S3RawStore
from ekb.changelog.rawstore.port import (
    ConflictingOriginalError,
    OriginalNotFoundError,
    RawStore,
)
from ekb.changelog.registry.options.dynamodb import DynamoRegistry
from ekb.changelog.registry.options.in_memory import InMemoryRegistry
from ekb.changelog.registry.port import DocumentRegistry, record_event
from ekb.contracts import Acl, ChangeEvent
from ekb.contracts.listing import NeedsOriginal
from tests.contract.infra import Infra
from tests.unit.changelog.factories import acl_change, delete, upsert

AT = datetime(2026, 11, 24, 9, 0, tzinfo=UTC)
PAGE = b'{"id":"HR-PL-04","body":"# Remote work, Milan\\n"}'


def registry(infra: Infra) -> DocumentRegistry:
    if infra.where == "memory":
        return InMemoryRegistry()
    built = DynamoRegistry(**infra.aws_params("dynamodb"))
    infra.dropped_at_the_end(built.destroy)
    return built


def acceptance(infra: Infra) -> AcceptanceLog:
    if infra.where == "memory":
        return InMemoryAcceptanceLog()
    built = DynamoAcceptanceLog(**infra.aws_params("dynamodb"))
    infra.dropped_at_the_end(built.destroy)
    return built


def rawstore(infra: Infra) -> RawStore:
    if infra.where == "memory":
        return InMemoryRawStore(clock=lambda: AT)
    built = S3RawStore(clock=lambda: AT, **infra.aws_params("s3"))
    infra.dropped_at_the_end(built.destroy)
    return built


def again[T](infra: Infra, build: Callable[[Infra], T]) -> T:
    """The same store, opened by another process (durable options only)."""
    if not infra.durable:
        pytest.skip("an in-memory store does not outlive its process")
    return build(infra)


# ------------------------------------------------------------------ registry


def test_the_registry_keeps_the_latest_record_per_item(infra: Infra) -> None:
    store = registry(infra)
    assert store.get("hr_docs:HR-PL-04") is None
    record_event(store, upsert("HR-PL-04", "1.1"))
    latest = record_event(store, upsert("HR-PL-04", "2.1"))
    assert store.get("hr_docs:HR-PL-04") == latest
    assert (latest.version, latest.events) == ("2.1", 2)
    assert again(infra, registry).get("hr_docs:HR-PL-04") == latest


def test_the_registry_lists_live_items_and_keeps_tombstones_empty(infra: Infra) -> None:
    store = registry(infra)
    for event in (
        upsert("HR-PL-04", "2.1"),
        upsert("HR-PL-99"),
        delete("HR-PL-99", "1.1", changed_by="dana"),
        upsert("employees/E-1001", "2026-11-24T09:00:00+00:00", source="hr_api"),
        acl_change("HR-PL-05", "1.2", Acl(groups=frozenset({"es-employees"}))),
    ):
        record_event(store, event)
    assert store.known("hr_docs") == {"HR-PL-04": "2.1", "HR-PL-05": NeedsOriginal(version="1.2")}
    assert store.known("hr_api") == {"employees/E-1001": "2026-11-24T09:00:00+00:00"}
    assert store.known("identity") == {}
    tombstone = store.get("hr_docs:HR-PL-99")
    assert tombstone is not None
    assert (tombstone.deleted, tombstone.content_hash, tombstone.acl) == (True, None, Acl())
    keys = [r.document_key for r in store.records()]
    assert (
        keys
        == sorted(keys)
        == [
            "hr_api:employees/E-1001",
            "hr_docs:HR-PL-04",
            "hr_docs:HR-PL-05",
            "hr_docs:HR-PL-99",
        ]
    )
    assert [r.source_id for r in store.records("hr_api")] == ["employees/E-1001"]


# ------------------------------------------------------------------ acceptance log


def _delivered(event: ChangeEvent, offset: int, partition: int = 0) -> Delivery:
    return Delivery(partition, offset, event)


def test_a_delete_closes_the_lifetime_and_the_latest_delivery_moves(infra: Infra) -> None:
    log = acceptance(infra)
    assert log.get("hr_docs:HR-PL-99") is None
    first = log.accept(_delivered(upsert("HR-PL-99"), 0))
    assert (first.lifetime, first.latest, first.record.version) == (0, (0, 0), "1.1")
    gone = log.accept(_delivered(delete("HR-PL-99", "1.1"), 1))
    assert (gone.lifetime, gone.latest, gone.record.deleted) == (1, (0, 1), True)
    back = log.accept(_delivered(upsert("HR-PL-99", "2.1", original_lifetime=1), 2))
    assert (back.lifetime, back.record.deleted) == (1, False)  # a new lifetime, still open
    seen = again(infra, acceptance).get("hr_docs:HR-PL-99")
    assert seen == back


def test_new_readers_for_a_deleted_item_leave_it_deleted(infra: Infra) -> None:
    log = acceptance(infra)
    log.accept(_delivered(upsert("HR-PL-99"), 0))
    log.accept(_delivered(delete("HR-PL-99", "1.1"), 1))
    readers = acl_change("HR-PL-99", "1.2", Acl(groups=frozenset({"all-staff"})))
    entry = log.accept(_delivered(readers, 2))
    assert (entry.record.deleted, entry.lifetime) == (True, 1)


def test_the_acceptance_log_lists_records_by_source(infra: Infra) -> None:
    log = acceptance(infra)
    log.accept(_delivered(upsert("HR-PL-04"), 0))
    log.accept(_delivered(upsert("users/alice", "3", source="identity"), 0, partition=1))
    assert [r.document_key for r in log.records()] == ["hr_docs:HR-PL-04", "identity:users/alice"]
    assert [r.source_id for r in log.records("identity")] == ["users/alice"]


# ------------------------------------------------------------------ raw store


def test_an_original_comes_back_byte_for_byte_from_its_pointer(infra: Infra) -> None:
    store = rawstore(infra)
    pointer = store.put("hr_docs:HR-PL-04", "1.1", PAGE)
    assert pointer == store.pointer_for("hr_docs:HR-PL-04", "1.1")
    assert store.get(pointer) == PAGE
    assert again(infra, rawstore).get(pointer) == PAGE


def test_ids_and_versions_with_slashes_and_colons_never_collide(infra: Infra) -> None:
    store = rawstore(infra)
    stamp = store.put("hr_api:employees/E-1001", "2026-11-24T09:00:00+00:00", b"{}")
    a = store.put("hr_db:a/b", "c", b"first")
    b = store.put("hr_db:a", "b/c", b"second")
    c = store.put("hr_docs:HR-PL-1", "1", b"short")
    d = store.put("hr_docs:HR-PL-10", "1", b"long")
    assert len({stamp, a, b, c, d}) == 5
    assert [store.get(p) for p in (a, b, c, d)] == [b"first", b"second", b"short", b"long"]
    assert [o.version for o in store.versions("hr_docs:HR-PL-1")] == ["1"]


def test_an_original_cannot_change_once_stored(infra: Infra) -> None:
    store = rawstore(infra)
    first = store.put("hr_docs:HR-PL-04", "1.1", PAGE)
    assert store.put("hr_docs:HR-PL-04", "1.1", PAGE) == first  # same bytes: harmless
    with pytest.raises(ConflictingOriginalError, match="already stored with other bytes"):
        store.put("hr_docs:HR-PL-04", "1.1", b"something else")
    assert store.get(first) == PAGE
    assert len(store.versions("hr_docs:HR-PL-04")) == 1


def test_versions_are_listed_oldest_first_with_size_and_checksum(infra: Infra) -> None:
    store = rawstore(infra)
    store.put("hr_docs:HR-PL-04", "1.1", PAGE)
    store.put("hr_docs:HR-PL-04", "2.1", PAGE + b" ")
    first, second = store.versions("hr_docs:HR-PL-04")
    assert (first.version, second.version) == ("1.1", "2.1")
    assert (first.size, first.stored_at, first.document_key) == (len(PAGE), AT, "hr_docs:HR-PL-04")
    assert first.checksum == f"sha256:{hashlib.sha256(PAGE).hexdigest()}"
    assert store.versions("hr_docs:HR-PL-05") == []


def test_a_purge_removes_one_lifetime_or_all_and_nothing_comes_back(infra: Infra) -> None:
    store = rawstore(infra)
    old = store.put("hr_docs:HR-PL-99", "1.1", PAGE)
    new = store.put("hr_docs:HR-PL-99", "1.1", b"recreated", lifetime=1)
    assert old != new
    assert store.purge("hr_docs:HR-PL-99", lifetime=0) == 1
    assert store.purge("hr_docs:HR-PL-99", lifetime=0) == 0  # repeating is harmless
    with pytest.raises(OriginalNotFoundError):
        store.get(old)
    assert store.get(new) == b"recreated"
    assert store.purge("hr_docs:HR-PL-99") == 1
    assert store.versions("hr_docs:HR-PL-99") == []
    with pytest.raises(OriginalNotFoundError):
        store.get(new)


def test_an_unknown_pointer_is_not_found(infra: Infra) -> None:
    store = rawstore(infra)
    with pytest.raises(OriginalNotFoundError):
        store.get(store.pointer_for("hr_docs:HR-PL-77", "1.1"))
    with pytest.raises(OriginalNotFoundError):
        store.get("elsewhere://bucket/key")
