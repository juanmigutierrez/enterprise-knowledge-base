"""The in-memory entity store (S9): one entity per record, removed with its record."""

from __future__ import annotations

from ekb.contracts import EntityKind
from ekb.stores.entities.options.in_memory import InMemoryEntityStore
from tests.unit.process.t6_entities.conftest import person, team


def test_put_get_and_list_sorted_by_id_and_by_kind() -> None:
    store = InMemoryEntityStore()
    for entity in (team("T-PPL", "People"), person("bob", "Bruno Moretti"), person("alice", "A")):
        assert store.put(entity) is None
    assert [e.entity_id for e in store.entities()] == [
        "person:alice",
        "person:bob",
        "team:T-PPL",
    ]
    assert [e.entity_id for e in store.entities(EntityKind.TEAM)] == ["team:T-PPL"]
    assert store.get("person:bob") == person("bob", "Bruno Moretti")
    assert store.get("person:nobody") is None


def test_putting_the_same_record_again_replaces_what_it_defined() -> None:
    store = InMemoryEntityStore()
    store.put(person("bob", "Bruno Moretti"))
    before = store.put(person("bob", "Bruno Moretti Gil"))
    assert before == person("bob", "Bruno Moretti")
    assert [e.name for e in store.entities()] == ["Bruno Moretti Gil"]


def test_a_record_whose_entity_changed_leaves_no_stale_entity_behind() -> None:
    store = InMemoryEntityStore()
    old = person("bob", "Bruno Moretti")
    new = old.model_copy(update={"entity_id": "person:bruno"})  # same record, new identity user
    store.put(old)
    assert store.put(new) == old
    assert [e.entity_id for e in store.entities()] == ["person:bruno"]


def test_remove_takes_the_records_key_and_a_second_remove_is_harmless() -> None:
    store = InMemoryEntityStore()
    store.put(person("bob", "Bruno Moretti"))
    removed = store.remove("hr_api:employees/bob")
    assert removed is not None
    assert removed.entity_id == "person:bob"
    assert store.remove("hr_api:employees/bob") is None
    assert store.entities() == ()


def test_an_id_taken_over_by_another_record_is_removed_only_with_that_record() -> None:
    store = InMemoryEntityStore()
    first = person("bob", "Bruno Moretti")
    second = first.model_copy(update={"source_key": "hr_api:employees/E-2002"})
    store.put(first)
    store.put(second)
    assert store.remove(first.source_key) is None  # the first record no longer defines it
    assert store.remove(second.source_key) == second
