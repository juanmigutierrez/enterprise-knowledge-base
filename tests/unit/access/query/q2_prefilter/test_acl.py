"""``q2_prefilter: acl``: who is asking, as a search filters them, from the permission store."""

from __future__ import annotations

from ekb.access.query.q2_prefilter.options.acl import AclPreFilter
from ekb.contracts import Principals
from ekb.stores.permissions.options.in_memory import InMemoryPermissionStore
from tests.unit.process.t8_permissions.conftest import loaded_store, membership


def test_an_active_person_reads_with_their_user_id_and_current_groups() -> None:
    scope = AclPreFilter(loaded_store()).scope("alice")
    assert scope.principals == Principals(
        user_id="alice", groups=frozenset({"all-staff", "it-employees"})
    )
    assert scope.reason == "reads as alice + all-staff, it-employees"
    assert (scope.prefilter_id, scope.reads_nothing) == ("acl@1", False)


def test_a_group_change_is_in_the_next_scope() -> None:
    store = loaded_store()
    q2 = AclPreFilter(store)
    store.put_membership(membership("bob", "all-staff", "it-employees", "hr-team", version="1"))
    principals = q2.scope("bob").principals
    assert principals is not None
    assert "hr-compensation" not in principals.groups


def test_a_leaver_or_an_unknown_person_reads_nothing() -> None:
    """The leaver gate (S1c): inactive keeps their groups on record, and still reads nothing."""
    store = InMemoryPermissionStore()
    store.put_membership(membership("olivia", "all-staff", "ca-employees", active=False))
    q2 = AclPreFilter(store)
    leaver = q2.scope("olivia")
    assert leaver.principals is None
    assert leaver.reads_nothing
    assert leaver.reason.startswith("inactive (has left): reads nothing")
    unknown = q2.scope("mallory")
    assert unknown.principals is None
    assert unknown.reason == "unknown to the identity provider: reads nothing"
