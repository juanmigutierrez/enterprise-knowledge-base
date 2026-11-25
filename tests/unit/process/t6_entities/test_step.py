"""The T6 step (S9): IDs go on the chunks, only known ones, and nothing else changes."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from ekb.contracts import Acl, Chunk, Entity, EntityMention, ParsedDocument
from ekb.process.t6_entities.options.exact_match import ExactMatchLinker
from ekb.process.t6_entities.port import Linking
from ekb.process.t6_entities.step import LinkError, LinkStep, summary
from ekb.stores.entities.options.in_memory import InMemoryEntityStore
from tests.unit.process.t6_entities.conftest import ENTITIES, chunks_of, synthetic


class Fixed:
    """A linker that returns whatever it is given."""

    linker_id = "fixed@1"

    def __init__(self, *mentions: tuple[EntityMention, ...]) -> None:
        self.mentions = mentions

    def link(
        self, document: ParsedDocument, chunks: tuple[Chunk, ...], entities: Sequence[Entity]
    ) -> Linking:
        return Linking(self.mentions, self.linker_id, "fixed")


def store_with(*entities: Entity) -> InMemoryEntityStore:
    store = InMemoryEntityStore()
    for entity in entities:
        store.put(entity)
    return store


def mention(entity_id: str) -> EntityMention:
    return EntityMention(entity_id=entity_id, text="x", rule="name")


def test_ids_go_on_the_chunks_and_nothing_else_changes() -> None:
    document, chunks = synthetic("HR-PL-04")
    result = LinkStep(ExactMatchLinker(), store_with(*ENTITIES)).handle(document, chunks)
    assert [c.entity_ids for c in result.chunks] == [(), ("team:T-PPL",), (), ("team:T-PPL",)]
    for before, after in zip(chunks, result.chunks, strict=True):
        assert after.linker_id == "exact_match@2"
        assert after.model_copy(update={"entity_ids": (), "linker_id": ""}) == before
        assert not after.publishable  # links open no gate: no vector, no ACL
    assert result.reason == (
        "linked with exact_match@2: 2 of 4 chunks linked: team:T-PPL x2; "
        "matched against 12 people and 5 teams"
    )


def test_ids_are_sorted_and_kept_once() -> None:
    document, chunks = chunks_of("# Contacts\n\nThe text.\n")
    linker = Fixed((mention("team:T-PPL"), mention("person:bob"), mention("team:T-PPL")))
    result = LinkStep(linker, store_with(*ENTITIES)).handle(document, chunks)
    assert result.chunks[0].entity_ids == ("person:bob", "team:T-PPL")


def test_an_id_the_directory_does_not_hold_is_refused() -> None:
    document, chunks = chunks_of("# Contacts\n\nThe text.\n")
    step = LinkStep(Fixed((mention("person:ghost"),)), store_with(*ENTITIES))
    with pytest.raises(LinkError, match="unknown entities"):
        step.handle(document, chunks)


def test_a_removed_entity_is_not_linked_by_the_next_document() -> None:
    store = store_with(*ENTITIES)
    step = LinkStep(ExactMatchLinker(), store)
    document, chunks = chunks_of("# Contacts\n\nWrite to Lucía Navarro.\n")
    assert step.handle(document, chunks).chunks[0].entity_ids == ("person:lucia",)
    store.remove("hr_api:employees/lucia")
    assert step.handle(document, chunks).chunks[0].entity_ids == ()


def test_the_wrong_number_of_mention_sets_is_refused() -> None:
    document, chunks = synthetic("HR-PL-04")
    with pytest.raises(LinkError, match="4 chunks but 1 sets"):
        LinkStep(Fixed(()), store_with(*ENTITIES)).handle(document, chunks)


@pytest.mark.parametrize(
    "update", [{"acl": Acl(groups=frozenset({"all-staff"}))}, {"vector": (0.1, 0.2)}]
)
def test_chunks_that_already_have_an_acl_or_a_vector_are_refused(
    update: dict[str, object],
) -> None:
    document, chunks = synthetic("HR-PL-04")
    late = (chunks[0].model_copy(update=update), *chunks[1:])
    with pytest.raises(LinkError, match="T6 runs before T7 and T8"):
        LinkStep(ExactMatchLinker(), store_with(*ENTITIES)).handle(document, late)


def test_chunks_of_another_document_or_version_are_refused() -> None:
    document, chunks = synthetic("HR-PL-04")
    other = document.model_copy(update={"version": "9.9"})
    with pytest.raises(LinkError, match="is from"):
        LinkStep(ExactMatchLinker(), store_with(*ENTITIES)).handle(other, chunks)


def test_summary_counts_linked_chunks_and_entities() -> None:
    document, chunks = synthetic("HR-PL-04")
    assert summary(chunks) == "0 of 4 chunks linked"
    linked = LinkStep(ExactMatchLinker(), store_with(*ENTITIES)).handle(document, chunks)
    assert summary(linked.chunks) == "2 of 4 chunks linked: team:T-PPL x2"
