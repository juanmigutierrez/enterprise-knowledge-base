"""The in-memory search store (S11): the publication gate at the door, one version at a time,
readers changed in place, deleted is gone, and a filter by who is asking."""

from __future__ import annotations

import re

import pytest

from ekb.contracts import Acl, Chunk, DocumentAcl, Principals
from ekb.stores.search.options.in_memory import InMemorySearchStore
from ekb.stores.search.port import UnpublishableError
from tests.unit.process.t8_permissions.conftest import ALL_STAFF, HR_ONLY, NOW, ready

ALICE = Principals(user_id="alice", groups=frozenset({"all-staff", "it-employees"}))
BOB = Principals(user_id="bob", groups=frozenset({"all-staff", "hr-team"}))


def published(page_id: str = "HR-PL-01", acl: Acl = ALL_STAFF) -> tuple[Chunk, ...]:
    _, chunks = ready(page_id)
    return tuple(
        c.model_copy(update={"acl": acl, "acl_version": "1.1", "resolver_id": "test@1"})
        for c in chunks
    )


def record(key: str, acl: Acl, version: str = "1.2") -> DocumentAcl:
    return DocumentAcl(
        document_key=key, acl=acl, acl_version=version, occurred_at=NOW, resolver_id="test@1"
    )


def test_publishing_a_document_stores_its_chunks_in_order() -> None:
    store = InMemorySearchStore()
    chunks = published()
    result = store.publish("hr_docs:HR-PL-01", tuple(reversed(chunks)))
    assert (result.published, result.replaced) == (len(chunks), 0)
    assert store.chunks("hr_docs:HR-PL-01") == chunks
    assert store.documents() == ("hr_docs:HR-PL-01",)


@pytest.mark.parametrize(
    ("field", "missing"),
    [("acl", "readers (T8)"), ("sensitivity", "a sensitivity (T5)"), ("vector", "a vector (T7)")],
)
def test_the_store_refuses_a_batch_with_an_unpublishable_chunk_and_keeps_nothing_of_it(
    field: str, missing: str
) -> None:
    """The publication gate, at the door (card 4.14): one bad chunk refuses the whole batch."""
    store = InMemorySearchStore()
    store.publish("hr_docs:HR-PL-01", published())
    good, *rest = published()
    bad = rest[0].model_copy(update={field: None})
    with pytest.raises(UnpublishableError, match=re.escape(f"not publishable: missing {missing}")):
        store.publish("hr_docs:HR-PL-01", (good, bad))
    assert store.chunks("hr_docs:HR-PL-01") == published()  # the old version is untouched


def test_the_store_refuses_chunks_of_another_document_or_two_versions_or_one_id_twice() -> None:
    store = InMemorySearchStore()
    chunks = published()
    with pytest.raises(UnpublishableError, match="belongs to hr_docs:HR-PL-01"):
        store.publish("hr_docs:HR-PL-02", chunks)
    mixed = (chunks[0], chunks[1].model_copy(update={"document_version": "2.0"}))
    with pytest.raises(UnpublishableError, match="one version at a time"):
        store.publish("hr_docs:HR-PL-01", mixed)
    other_lifetime = chunks[1].model_copy(update={"original_lifetime": 1})
    with pytest.raises(UnpublishableError, match="different lifetimes"):
        store.publish("hr_docs:HR-PL-01", (chunks[0], other_lifetime))
    with pytest.raises(UnpublishableError, match="the same chunk ID twice"):
        store.publish("hr_docs:HR-PL-01", (chunks[0], chunks[0]))
    assert store.chunks() == ()


def test_a_new_version_replaces_every_chunk_of_the_old_one() -> None:
    store = InMemorySearchStore()
    old = published()
    store.publish("hr_docs:HR-PL-01", old)
    shorter = tuple(c.model_copy(update={"document_version": "2.0"}) for c in old[:1])
    result = store.publish("hr_docs:HR-PL-01", shorter)
    assert (result.published, result.replaced) == (1, len(old))
    assert store.chunks("hr_docs:HR-PL-01") == shorter
    assert store.publish("hr_docs:HR-PL-01", ()).replaced == 1  # nothing left to publish
    assert store.documents() == ()


def test_new_readers_change_only_the_readers_of_every_chunk() -> None:
    store = InMemorySearchStore()
    before = published()
    store.publish("hr_docs:HR-PL-01", before)
    assert store.set_readers(record("hr_docs:HR-PL-01", HR_ONLY)) == len(before)
    after = store.chunks("hr_docs:HR-PL-01")
    for old, new in zip(before, after, strict=True):
        assert (new.acl, new.acl_version) == (HR_ONLY, "1.2")
        assert new.document_version == old.document_version == "1.1"
        assert new.model_copy(update={"acl": old.acl, "acl_version": "1.1"}) == old
    assert store.set_readers(record("hr_docs:NOT-THERE", HR_ONLY)) == 0
    assert store.documents() == ("hr_docs:HR-PL-01",)


def test_a_removed_document_is_never_returned() -> None:
    """The deletion gate: nothing of a removed document is listed or found by anyone."""
    store = InMemorySearchStore()
    store.publish("hr_docs:HR-PL-01", published())
    store.publish("hr_docs:HR-PL-99", published("HR-PL-99"))
    assert store.remove("hr_docs:HR-PL-99") == len(published("HR-PL-99"))
    assert store.remove("hr_docs:HR-PL-99") == 0  # a repeated purge is harmless
    assert all(c.document_key != "hr_docs:HR-PL-99" for c in store.chunks())
    assert all(c.document_key != "hr_docs:HR-PL-99" for c in store.readable_by(ALICE))


def test_readable_by_returns_only_what_the_person_s_principals_admit() -> None:
    store = InMemorySearchStore()
    store.publish("hr_docs:HR-PL-01", published())
    store.publish("hr_docs:HR-CMP-01", published("HR-CMP-01", HR_ONLY))
    alice = {c.document_key for c in store.readable_by(ALICE)}
    bob = {c.document_key for c in store.readable_by(BOB)}
    assert alice == {"hr_docs:HR-PL-01"}
    assert bob == {"hr_docs:HR-PL-01", "hr_docs:HR-CMP-01"}
    assert store.readable_by(None) == ()  # unknown or inactive: nothing
