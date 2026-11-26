"""Every entity store and permission store option keeps its port's promises (S17).

Each test runs in memory, through moto, and (``-m integration``) against
DynamoDB Local. The permission store's tests include the gates' part: an
inactive, unknown, hidden or purged user reads nothing, and a membership from a
closed change-log lifetime does not count.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from ekb.contracts import DocumentAcl, EntityKind, Principals
from ekb.stores.entities.options.dynamodb import DynamoEntityStore
from ekb.stores.entities.options.in_memory import InMemoryEntityStore
from ekb.stores.entities.port import EntityStore
from ekb.stores.permissions.options.dynamodb import DynamoPermissionStore
from ekb.stores.permissions.options.in_memory import InMemoryPermissionStore
from ekb.stores.permissions.port import PermissionStore
from tests.contract.infra import Infra
from tests.unit.process.t6_entities.conftest import person, team
from tests.unit.process.t8_permissions.conftest import ALL_STAFF, HR_ONLY, NOW, membership


def entity_store(infra: Infra) -> EntityStore:
    if infra.where == "memory":
        return InMemoryEntityStore()
    built = DynamoEntityStore(**infra.aws_params("dynamodb"))
    infra.dropped_at_the_end(built.destroy)
    return built


def permission_store(
    infra: Infra, current_lifetime: Callable[[str], int] | None = None
) -> PermissionStore:
    if infra.where == "memory":
        return InMemoryPermissionStore(current_lifetime)
    built = DynamoPermissionStore(current_lifetime, **infra.aws_params("dynamodb"))
    infra.dropped_at_the_end(built.destroy)
    return built


def again[T](infra: Infra, build: Callable[[Infra], T]) -> T:
    if not infra.durable:
        pytest.skip("an in-memory store does not outlive its process")
    return build(infra)


# ------------------------------------------------------------------ entity store


def test_entities_are_kept_by_id_and_listed_by_kind(infra: Infra) -> None:
    store = entity_store(infra)
    for entity in (team("T-PPL", "People"), person("bob", "Bruno Moretti"), person("alice", "A")):
        assert store.put(entity) is None
    assert [e.entity_id for e in store.entities()] == ["person:alice", "person:bob", "team:T-PPL"]
    assert [e.entity_id for e in store.entities(EntityKind.TEAM)] == ["team:T-PPL"]
    assert store.get("person:bob") == person("bob", "Bruno Moretti")
    assert store.get("person:nobody") is None
    assert again(infra, entity_store).get("person:bob") == person("bob", "Bruno Moretti")


def test_a_record_defines_one_entity_and_takes_it_when_purged(infra: Infra) -> None:
    store = entity_store(infra)
    old = person("bob", "Bruno Moretti")
    store.put(old)
    assert store.put(person("bob", "Bruno Moretti Gil")) == old  # the same record again
    renamed = old.model_copy(update={"entity_id": "person:bruno"})  # a new identity user
    assert store.put(renamed) == person("bob", "Bruno Moretti Gil")
    assert [e.entity_id for e in store.entities()] == ["person:bruno"]
    assert store.remove("hr_api:employees/bob") == renamed
    assert store.remove("hr_api:employees/bob") is None
    assert store.entities() == ()


def test_an_id_taken_over_by_another_record_goes_only_with_that_record(infra: Infra) -> None:
    store = entity_store(infra)
    first = person("bob", "Bruno Moretti")
    second = first.model_copy(update={"source_key": "hr_api:employees/E-2002"})
    store.put(first)
    store.put(second)
    assert store.remove(first.source_key) is None
    assert store.get("person:bob") == second
    assert store.remove(second.source_key) == second


# ------------------------------------------------------------------ permission store


def test_memberships_are_kept_per_user_and_replaced_by_a_new_record_version(infra: Infra) -> None:
    store = permission_store(infra)
    assert store.put_membership(membership("bob", "hr-team")) is None
    assert store.put_membership(membership("alice", "all-staff", "it-employees")) is None
    before = store.put_membership(membership("alice", "all-staff", version="7"))
    assert before == membership("alice", "all-staff", "it-employees")
    assert [m.user_id for m in store.memberships()] == ["alice", "bob"]
    assert store.principals("alice") == Principals(user_id="alice", groups=frozenset({"all-staff"}))
    assert again(infra, permission_store).principals("bob") == Principals(
        user_id="bob", groups=frozenset({"hr-team"})
    )


def test_an_inactive_unknown_or_purged_user_reads_nothing(infra: Infra) -> None:
    store = permission_store(infra)
    store.put_membership(membership("olivia", "all-staff", active=False))
    store.put_membership(membership("alice", "all-staff"))
    assert store.principals("olivia") is None
    assert store.principals("nobody") is None
    assert store.remove_membership("identity:users/alice") == membership("alice", "all-staff")
    assert store.principals("alice") is None
    assert store.remove_membership("identity:users/alice") is None


def test_a_user_another_record_now_defines_leaves_no_stale_line(infra: Infra) -> None:
    store = permission_store(infra)
    store.put_membership(membership("alice", "all-staff"))
    moved = membership("alice", "all-staff").model_copy(update={"source_key": "idp2:users/alice"})
    assert store.put_membership(moved) == membership("alice", "all-staff")
    assert store.remove_membership("identity:users/alice") is None
    assert store.membership("alice") == moved


def test_a_hidden_record_or_a_closed_lifetime_hides_the_membership(infra: Infra) -> None:
    lifetimes = {"identity:users/alice": 0}
    store = permission_store(infra, lambda key: lifetimes.get(key, 0))
    store.put_membership(membership("alice", "all-staff"))
    store.put_membership(membership("bob", "hr-team"))
    store.set_visible("identity:users/alice", visible=False)
    assert store.principals("alice") is None
    assert [m.user_id for m in store.memberships()] == ["bob"]
    store.set_visible("identity:users/alice", visible=True)
    assert store.principals("alice") is not None
    lifetimes["identity:users/alice"] = 1  # a delete was accepted: the old record is gone
    assert store.membership("alice") is None
    assert [m.user_id for m in store.memberships()] == ["bob"]


def test_document_readers_are_recorded_replaced_and_forgotten(infra: Infra) -> None:
    store = permission_store(infra)
    first = DocumentAcl(
        document_key="hr_docs:HR-PL-05",
        acl=HR_ONLY,
        acl_version="1.0",
        occurred_at=NOW,
        resolver_id="source_acl@1",
    )
    second = first.model_copy(update={"acl": ALL_STAFF, "acl_version": "1.1"})
    other = first.model_copy(update={"document_key": "hr_docs:HR-PL-01"})
    assert store.put_readers(first) is None
    assert store.put_readers(second) == first
    store.put_readers(other)
    assert store.readers("hr_docs:HR-PL-05") == second
    assert store.all_readers() == (other, second)
    assert store.forget_readers("hr_docs:HR-PL-05") == second
    assert store.readers("hr_docs:HR-PL-05") is None
    assert store.forget_readers("hr_docs:HR-PL-05") is None
