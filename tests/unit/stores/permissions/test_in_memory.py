"""The in-memory permission store (S11): one membership per user record, readers per document."""

from __future__ import annotations

from ekb.contracts import DocumentAcl, Principals
from ekb.stores.permissions.options.in_memory import InMemoryPermissionStore
from tests.unit.process.t8_permissions.conftest import ALL_STAFF, HR_ONLY, NOW, membership


def test_put_get_and_list_memberships_sorted_by_user() -> None:
    store = InMemoryPermissionStore()
    assert store.put_membership(membership("bob", "hr-team")) is None
    assert store.put_membership(membership("alice", "all-staff")) is None
    assert [m.user_id for m in store.memberships()] == ["alice", "bob"]
    assert store.membership("bob") == membership("bob", "hr-team")
    assert store.membership("nobody") is None


def test_a_new_record_version_replaces_the_membership() -> None:
    store = InMemoryPermissionStore()
    store.put_membership(membership("alice", "all-staff", "it-employees"))
    before = store.put_membership(membership("alice", "all-staff", version="7"))
    assert before == membership("alice", "all-staff", "it-employees")
    assert store.principals("alice") == Principals(user_id="alice", groups=frozenset({"all-staff"}))


def test_principals_are_none_for_an_inactive_or_unknown_user() -> None:
    store = InMemoryPermissionStore()
    store.put_membership(membership("olivia", "all-staff", active=False))
    assert store.principals("olivia") is None
    assert store.principals("nobody") is None


def test_removing_a_record_removes_the_user_it_defined() -> None:
    store = InMemoryPermissionStore()
    store.put_membership(membership("alice", "all-staff"))
    removed = store.remove_membership("identity:users/alice")
    assert removed == membership("alice", "all-staff")
    assert store.membership("alice") is None
    assert store.remove_membership("identity:users/alice") is None


def test_a_user_defined_by_another_record_now_leaves_no_stale_mapping() -> None:
    store = InMemoryPermissionStore()
    store.put_membership(membership("alice", "all-staff"))
    moved = membership("alice", "all-staff").model_copy(update={"source_key": "idp2:users/alice"})
    assert store.put_membership(moved) == membership("alice", "all-staff")
    assert store.remove_membership("identity:users/alice") is None
    assert store.membership("alice") == moved


def test_document_readers_are_recorded_replaced_and_forgotten() -> None:
    store = InMemoryPermissionStore()
    first = DocumentAcl(
        document_key="hr_docs:HR-PL-05",
        acl=HR_ONLY,
        acl_version="1.0",
        occurred_at=NOW,
        resolver_id="source_acl@1",
    )
    second = first.model_copy(update={"acl": ALL_STAFF, "acl_version": "1.1"})
    assert store.put_readers(first) is None
    assert store.put_readers(second) == first
    assert store.readers("hr_docs:HR-PL-05") == second
    assert store.all_readers() == (second,)
    assert store.forget_readers("hr_docs:HR-PL-05") == second
    assert store.readers("hr_docs:HR-PL-05") is None
    assert store.forget_readers("hr_docs:HR-PL-05") is None
