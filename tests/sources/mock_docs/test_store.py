"""What "a change" means in the source. S2-S4 are built on these rules."""

from __future__ import annotations

import pytest
from sources.common.directory import UnknownPrincipalError
from sources.mock_docs.models import ChangeKind, PageAcl, PageCreate, PageEdit
from sources.mock_docs.store import DocsStore, PageExistsError, PageNotFoundError, content_hash


def test_seed_pages_start_at_revision_one_and_are_in_the_feed(store: DocsStore) -> None:
    pages = store.list_pages()
    assert {(p.rev, p.acl_rev) for p in pages} == {(1, 1)}
    feed = store.changes()
    assert [c.event for c in feed] == [ChangeKind.CREATED] * 12
    assert store.cursor == 12


def test_listing_is_sorted_and_carries_no_body(store: DocsStore) -> None:
    pages = store.list_pages()
    assert [p.id for p in pages] == sorted(p.id for p in pages)
    assert "body" not in pages[0].model_dump()


def test_edit_raises_revision_and_changes_hash(store: DocsStore) -> None:
    before = store.get("HR-PL-04")
    after = store.edit("HR-PL-04", PageEdit(body=before.body + "\nNew rule.\n"))
    assert after.rev == before.rev + 1
    assert after.acl_rev == before.acl_rev
    assert after.content_hash != before.content_hash
    assert after.updated_at > before.updated_at
    assert after.title == before.title  # no title given: kept


def test_saving_identical_text_is_still_a_change_with_the_same_hash(store: DocsStore) -> None:
    before = store.get("HR-PL-04")
    after = store.edit("HR-PL-04", PageEdit(body=before.body))
    assert after.rev == before.rev + 1
    assert after.content_hash == before.content_hash
    assert store.changes(since=12)[0].event is ChangeKind.UPDATED


def test_title_change_alone_changes_the_hash(store: DocsStore) -> None:
    before = store.get("HR-PL-04")
    after = store.edit("HR-PL-04", PageEdit(body=before.body, title="Remote work - Milan (2027)"))
    assert after.content_hash != before.content_hash


def test_permission_change_raises_acl_rev_only(store: DocsStore) -> None:
    before = store.get("HR-PL-05")
    after = store.set_acl("HR-PL-05", PageAcl(groups=("es-employees", "hr-team", "it-employees")))
    assert after.acl_rev == before.acl_rev + 1
    assert after.rev == before.rev
    assert after.content_hash == before.content_hash
    assert after.body == before.body
    assert store.changes(since=12)[0].event is ChangeKind.PERMISSIONS_CHANGED


def test_permissions_are_normalised(store: DocsStore) -> None:
    page = store.set_acl("HR-PL-05", PageAcl(groups=("hr-team", "es-employees", "hr-team")))
    assert page.acl.groups == ("es-employees", "hr-team")


def test_unknown_principal_is_rejected_and_nothing_changes(store: DocsStore) -> None:
    with pytest.raises(UnknownPrincipalError, match="mallory"):
        store.set_acl("HR-CMP-01", PageAcl(users=("mallory",)))
    assert store.get("HR-CMP-01").acl.groups == ("hr-compensation",)
    assert store.cursor == 12


def test_delete_removes_the_page_and_leaves_a_tombstone(store: DocsStore) -> None:
    change = store.delete("HR-PL-99")
    assert change.event is ChangeKind.DELETED
    assert change.page_id == "HR-PL-99"
    with pytest.raises(PageNotFoundError):
        store.get("HR-PL-99")
    assert "HR-PL-99" not in {p.id for p in store.list_pages()}
    assert store.changes(since=12) == [change]


def test_deleted_content_cannot_be_edited_or_deleted_again(store: DocsStore) -> None:
    store.delete("HR-PL-99")
    with pytest.raises(PageNotFoundError):
        store.edit("HR-PL-99", PageEdit(body="back from the dead"))
    with pytest.raises(PageNotFoundError):
        store.set_acl("HR-PL-99", PageAcl(groups=("all-staff",)))
    with pytest.raises(PageNotFoundError):
        store.delete("HR-PL-99")
    assert store.cursor == 13  # only the one delete was recorded


def test_create_rejects_a_duplicate_id(store: DocsStore) -> None:
    existing = store.get("HR-PL-01")
    request = PageCreate(
        **existing.model_dump(include={"id", "title", "language", "department", "body", "acl"})
    )
    with pytest.raises(PageExistsError):
        store.create(request)


def test_change_feed_is_ordered_and_resumable(store: DocsStore) -> None:
    cursor = store.cursor
    store.edit("HR-PL-01", PageEdit(body="v2\n"))
    store.set_acl("HR-PL-01", PageAcl(groups=("all-staff", "hr-team")))
    store.delete("HR-PL-99")
    feed = store.changes(since=cursor)
    assert [c.seq for c in feed] == [13, 14, 15]
    assert [c.event for c in feed] == [
        ChangeKind.UPDATED,
        ChangeKind.PERMISSIONS_CHANGED,
        ChangeKind.DELETED,
    ]
    assert [c.at for c in feed] == sorted(c.at for c in feed)
    assert store.changes(since=14) == feed[2:]
    assert store.changes(since=store.cursor) == []


def test_content_hash_is_stable() -> None:
    assert content_hash("T", "body") == content_hash("T", "body")
    assert content_hash("T", "body").startswith("sha256:")
    assert content_hash("T", "body") != content_hash("T", "body ")
