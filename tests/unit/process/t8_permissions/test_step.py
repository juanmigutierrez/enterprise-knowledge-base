"""The T8 step (S11): readers on every chunk, the publication gate, the fast lane."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from ekb.contracts import Acl, ChangeOp, Chunk, Principals
from ekb.process.t8_permissions.options.source_acl import SourceAclResolver
from ekb.process.t8_permissions.step import (
    PermissionStep,
    T8Action,
    T8Error,
    describe,
    readers_diff,
)
from ekb.stores.permissions.options.in_memory import InMemoryPermissionStore
from ekb.stores.search.options.in_memory import InMemorySearchStore
from ekb.stores.search.port import Published
from tests.unit.process.t8_permissions.conftest import (
    ALL_STAFF,
    HR_ONLY,
    acl_change,
    loaded_store,
    ready,
    ready_body,
    upsert,
)

HR_ES = Acl(groups=frozenset({"es-employees", "hr-team"}))
ES = Acl(groups=frozenset({"es-employees"}))


def setup() -> tuple[PermissionStep, InMemoryPermissionStore, InMemorySearchStore]:
    permissions, index = loaded_store(), InMemorySearchStore()
    return PermissionStep(SourceAclResolver(), permissions, index), permissions, index


def visible(index: InMemorySearchStore, permissions: InMemoryPermissionStore, user: str) -> int:
    return len(index.readable_by(permissions.principals(user)))


def test_every_chunk_gets_the_readers_and_is_published() -> None:
    step, permissions, index = setup()
    document, chunks = ready("HR-PL-01")
    assert not any(c.publishable for c in chunks)

    event = upsert(acl=ALL_STAFF).model_copy(update={"original_lifetime": 1})
    result = step.publish(event, document, chunks)

    assert result.action is T8Action.PUBLISH
    assert index.chunks("hr_docs:HR-PL-01") == result.chunks
    for before, after in zip(chunks, result.chunks, strict=True):
        assert (after.acl, after.acl_version, after.resolver_id) == (
            ALL_STAFF,
            "1.1",
            "source_acl@1",
        )
        assert after.publishable
        assert after == before.model_copy(
            update={
                "acl": ALL_STAFF,
                "acl_version": "1.1",
                "resolver_id": "source_acl@1",
                "source_content_hash": event.content_hash,
                "original_lifetime": 1,
            }
        )
    assert permissions.readers("hr_docs:HR-PL-01") == result.record
    assert len(result.readers) == 12
    assert result.reason == (
        f"published {len(chunks)} chunks of v1.1, readers all-staff: "
        "readers copied from the source: 12 people can read it now"
    )


def test_a_chunk_is_invisible_until_t8_has_run_then_visible_only_to_its_readers() -> None:
    """The publication test (card 4.14): paused after T7, nothing is searchable; resumed,
    only an authorized user finds it."""
    step, permissions, index = setup()
    document, chunks = ready("HR-CMP-01")
    assert all(c.vector is not None and c.sensitivity is not None for c in chunks)

    assert index.chunks() == ()
    assert visible(index, permissions, "bob") == 0  # bob may read it, but T8 hasn't run

    step.publish(
        upsert("hr_docs:HR-CMP-01", Acl(groups=frozenset({"hr-compensation"}))), document, chunks
    )

    assert visible(index, permissions, "bob") == len(chunks)
    for user in ("alice", "dana", "lucia", "olivia"):
        assert visible(index, permissions, user) == 0


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("sensitivity", "no sensitivity: T5 runs before T8"),
        ("vector", "no vector: T7 runs before T8"),
        ("acl", "already has readers"),
    ],
)
def test_a_chunk_not_ready_for_t8_is_refused_and_nothing_is_published(
    field: str, message: str
) -> None:
    step, permissions, index = setup()
    document, chunks = ready("HR-PL-01")
    value = ALL_STAFF if field == "acl" else None
    broken = (*chunks[:-1], chunks[-1].model_copy(update={field: value}))

    with pytest.raises(T8Error, match=message):
        step.publish(upsert(), document, broken)

    assert index.chunks() == ()
    assert permissions.readers("hr_docs:HR-PL-01") is None


def test_chunks_of_another_version_or_document_or_a_non_upsert_are_refused() -> None:
    step, _, index = setup()
    document, chunks = ready("HR-PL-01")
    with pytest.raises(T8Error, match=r"the document is hr_docs:HR-PL-01 v1\.1"):
        step.publish(upsert(version="2.0"), document, chunks)
    other, _ = ready("HR-PL-02")
    with pytest.raises(T8Error, match="is from hr_docs:HR-PL-01"):
        step.publish(upsert("hr_docs:HR-PL-02"), other, chunks)
    with pytest.raises(T8Error, match="only an upsert publishes"):
        step.publish(acl_change("hr_docs:HR-PL-01", ALL_STAFF, "1.1"), document, chunks)
    assert index.chunks() == ()


class Failing(InMemorySearchStore):
    def publish(self, document_key: str, chunks: Sequence[Chunk]) -> Published:
        raise OSError("search store down")


def test_if_the_search_store_fails_no_readers_are_recorded() -> None:
    permissions = loaded_store()
    step = PermissionStep(SourceAclResolver(), permissions, Failing())
    document, chunks = ready("HR-PL-01")
    with pytest.raises(OSError, match="down"):
        step.publish(upsert(), document, chunks)
    assert permissions.readers("hr_docs:HR-PL-01") is None


def test_a_new_version_replaces_the_published_one() -> None:
    step, _, index = setup()
    document, chunks = ready_body("# Leave\n\nOne.\n\n## More\n\nTwo.\n", key="hr_docs:X")
    step.publish(upsert("hr_docs:X"), document, chunks)
    shorter, short_chunks = ready_body("# Leave\n\nOne.\n", key="hr_docs:X", version="2.0")
    v2 = upsert("hr_docs:X", version="2.0")
    result = step.publish(v2, shorter, short_chunks)
    assert len(chunks) > len(short_chunks)
    assert result.replaced == len(chunks)
    assert index.chunks("hr_docs:X") == result.chunks


def test_the_fast_lane_changes_the_readers_of_published_chunks_and_nothing_else() -> None:
    step, permissions, index = setup()
    document, chunks = ready("HR-PL-05")
    published = step.publish(upsert("hr_docs:HR-PL-05", HR_ES), document, chunks).chunks
    assert visible(index, permissions, "dana") == len(chunks)  # dana is in hr-team

    result = step.apply_readers(acl_change("hr_docs:HR-PL-05", ES, "1.2"))

    assert result.action is T8Action.READERS
    assert result.replaced == len(chunks)
    after = index.chunks("hr_docs:HR-PL-05")
    for old, new in zip(published, after, strict=True):
        assert (new.acl, new.acl_version) == (ES, "1.2")
        assert (new.text, new.vector, new.document_version) == (old.text, old.vector, "1.1")
    assert visible(index, permissions, "dana") == 0
    assert visible(index, permissions, "carla") == len(chunks)
    assert permissions.readers("hr_docs:HR-PL-05") == result.record
    assert result.previous is not None
    assert result.reason.startswith(f"readers -hr-team on {len(chunks)} published chunks: ")


def test_the_fast_lane_on_a_document_with_nothing_published_only_records_its_readers() -> None:
    step, permissions, index = setup()
    result = step.apply_readers(acl_change("hr_docs:NEW", HR_ONLY, "0.1"))
    assert result.action is T8Action.READERS
    assert result.replaced == 0
    assert "recorded (nothing published yet)" in result.reason
    assert index.chunks() == ()
    assert permissions.readers("hr_docs:NEW") == result.record


def test_readers_first_on_an_edit_does_nothing_when_they_did_not_change() -> None:
    step, permissions, index = setup()
    document, chunks = ready("HR-PL-01")
    step.publish(upsert(), document, chunks)
    before = index.chunks("hr_docs:HR-PL-01")

    result = step.apply_readers(upsert(version="2.0"))

    assert result.action is T8Action.UNCHANGED
    assert result.reason == "readers unchanged (all-staff)"
    assert index.chunks("hr_docs:HR-PL-01") == before
    assert permissions.readers("hr_docs:HR-PL-01") == result.record


def test_readers_first_on_an_edit_that_also_revokes_closes_the_old_chunks_at_once() -> None:
    """If T7's model is down, the new text waits, the revocation does not."""
    step, permissions, index = setup()
    document, chunks = ready("HR-PL-05")
    step.publish(upsert("hr_docs:HR-PL-05", HR_ES), document, chunks)

    step.apply_readers(upsert("hr_docs:HR-PL-05", ES, version="2.1"))  # T3-T7 not run yet

    assert visible(index, permissions, "dana") == 0
    assert {c.document_version for c in index.chunks("hr_docs:HR-PL-05")} == {"1.1"}


def test_a_delete_has_no_readers() -> None:
    step, _, _ = setup()
    delete = acl_change("hr_docs:X", ALL_STAFF).model_copy(
        update={"op": ChangeOp.DELETE, "acl": Acl(), "content_hash": None}
    )
    with pytest.raises(T8Error, match="a delete has no readers"):
        step.apply_readers(delete)


def test_a_group_change_needs_no_chunk_rewritten() -> None:
    """Who is in a group is looked up when someone asks: the chunks keep the group."""
    step, permissions, index = setup()
    document, chunks = ready("HR-PL-02")
    step.publish(
        upsert("hr_docs:HR-PL-02", Acl(groups=frozenset({"it-employees", "hr-team"}))),
        document,
        chunks,
    )
    before = index.chunks()
    assert visible(index, permissions, "alice") == len(chunks)

    alice = permissions.membership("alice")
    assert alice is not None
    permissions.put_membership(alice.model_copy(update={"groups": frozenset({"all-staff"})}))

    assert visible(index, permissions, "alice") == 0
    assert index.chunks() == before


def test_describe_and_readers_diff_name_groups_and_users() -> None:
    acl = Acl(groups=frozenset({"hr-team"}), users=frozenset({"bob"}))
    assert describe(acl) == "hr-team, user:bob"
    assert describe(Acl()) == "nobody"
    assert readers_diff(None, ALL_STAFF) == "+all-staff"
    assert readers_diff(HR_ES, ES) == "-hr-team"
    assert readers_diff(ES, ES) == "no change"


def test_principals_from_the_store_filter_the_published_chunks() -> None:
    step, permissions, index = setup()
    document, chunks = ready("HR-PL-01")
    step.publish(upsert(acl=Acl(users=frozenset({"alice"}))), document, chunks)
    assert visible(index, permissions, "alice") == len(chunks)
    assert visible(index, permissions, "bob") == 0
    assert index.readable_by(Principals(user_id="ghost")) == ()


def test_readers_first_skips_a_document_with_nothing_recorded_yet() -> None:
    step, permissions, index = setup()
    assert step.readers_first(upsert()) is None
    assert permissions.readers("hr_docs:HR-PL-01") is None
    document, chunks = ready("HR-PL-01")
    step.publish(upsert(), document, chunks)
    result = step.readers_first(upsert(acl=HR_ONLY, version="2.0"))
    assert result is not None
    assert result.action is T8Action.READERS
    # A new group grant hides the previous text until the edited version is ready.
    assert index.chunks("hr_docs:HR-PL-01") == ()


def test_a_version_with_no_chunks_unpublishes_the_old_one_and_records_its_readers() -> None:
    step, permissions, index = setup()
    document, chunks = ready("HR-PL-01")
    step.publish(upsert(), document, chunks)
    empty, _ = ready_body("", key="hr_docs:HR-PL-01", version="2.0")

    result = step.publish(upsert(version="2.0"), empty, ())

    assert result.replaced == len(chunks)
    assert index.chunks("hr_docs:HR-PL-01") == ()
    record = permissions.readers("hr_docs:HR-PL-01")
    assert record is not None
    assert record.acl_version == "2.0"
