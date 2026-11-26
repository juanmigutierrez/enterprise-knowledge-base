"""Which pages an entity change affects, and whether their links are stale (S16)."""

from __future__ import annotations

from ekb.contracts import Chunk, Entity
from ekb.process.t6_entities.options.exact_match import ExactMatchLinker
from ekb.process.t6_entities.relink import EXACT_MATCH, EntityChange, candidates, stale
from ekb.process.t6_entities.step import LinkStep
from tests.unit.process.t6_entities.conftest import ENTITIES, chunks_of, person, team

LUCIA = next(e for e in ENTITIES if e.entity_id == "person:lucia")
PEOPLE = next(e for e in ENTITIES if e.entity_id == "team:T-PPL")
SARA = person("sara", "Sara Esposito", email="sara.esposito@example.com", team="T-PPL")


class Directory:
    def __init__(self, entities: tuple[Entity, ...]) -> None:
        self._entities = entities

    def entities(self) -> tuple[Entity, ...]:
        return self._entities


def linked(
    body: str, entities: tuple[Entity, ...], *, key: str = "hr_docs:HR-PL-11"
) -> tuple[Chunk, ...]:
    """A page's chunks as T6 links them against ``entities``."""
    document, chunks = chunks_of(body, key=key, title="Mentoring programme")
    return LinkStep(ExactMatchLinker(), Directory(entities)).handle(document, chunks).chunks


MENTORING = (
    "# Mentoring programme\n\nAsk Lucía Navarro to join.\n\nSara Esposito coordinates the sessions."
)


def test_a_hire_makes_the_pages_that_name_them_candidates_and_their_links_stale() -> None:
    chunks = linked(MENTORING, ENTITIES)  # Sara is not hired yet
    assert {e for c in chunks for e in c.entity_ids} == {"person:lucia"}

    hire = EntityChange(before=None, after=SARA)
    found = candidates(chunks, [hire])

    assert found == {"hr_docs:HR-PL-11": ["person:sara added"]}
    assert stale(chunks, (*ENTITIES, SARA))
    assert not stale(chunks, ENTITIES)  # nothing changed for the store it was linked with


def test_a_leaver_makes_the_pages_that_carry_their_id_candidates() -> None:
    chunks = linked(MENTORING, ENTITIES)
    others = tuple(e for e in ENTITIES if e is not LUCIA)

    found = candidates(chunks, [EntityChange(before=LUCIA, after=None)])

    assert found == {"hr_docs:HR-PL-11": ["person:lucia removed"]}
    assert stale(chunks, others)


def test_a_renamed_team_affects_pages_naming_the_old_or_the_new_name() -> None:
    old = linked("# Team\n\nWrite to the People team.", ENTITIES, key="hr_docs:A")
    new = linked("# Team\n\nWrite to the Talent team.", ENTITIES, key="hr_docs:B")
    other = linked("# Other\n\nNothing to see.", ENTITIES, key="hr_docs:C")
    talent = team("T-PPL", "Talent")

    found = candidates((*old, *new, *other), [EntityChange(before=PEOPLE, after=talent)])

    assert set(found) == {"hr_docs:A", "hr_docs:B"}
    assert found["hr_docs:A"] == ["team:T-PPL renamed"]


def test_a_namesake_hire_makes_an_existing_link_ambiguous_and_so_stale() -> None:
    chunks = linked(MENTORING, ENTITIES)
    namesake = person("lucia2", "Lucía Navarro", team="T-SAL")

    assert candidates(chunks, [EntityChange(None, namesake)]) == {
        "hr_docs:HR-PL-11": ["person:lucia2 added"]
    }
    assert stale(chunks, (*ENTITIES, namesake))  # the name now links to neither


def test_a_change_no_link_can_see_queues_nothing() -> None:
    chunks = linked(MENTORING, ENTITIES)
    moved = LUCIA.model_copy(update={"team_id": "team:T-SAL", "source_version": "2"})

    change = EntityChange(before=LUCIA, after=moved)

    assert not change.matters
    assert candidates(chunks, [change]) == {}


def test_pages_that_do_not_name_the_entity_are_not_candidates() -> None:
    chunks = linked("# Leave\n\nYou get 25 days.", ENTITIES, key="hr_docs:HR-PL-01")

    assert candidates(chunks, [EntityChange(None, SARA)]) == {}


def test_links_written_by_another_linker_are_always_treated_as_stale() -> None:
    chunks = tuple(
        c.model_copy(update={"linker_id": "cascade@1"}) for c in linked(MENTORING, ENTITIES)
    )
    assert EXACT_MATCH.startswith("exact_match@")
    assert stale(chunks, ENTITIES)


def test_a_change_is_described_by_ids_only() -> None:
    assert EntityChange(None, SARA).describe() == "person:sara added"
    assert EntityChange(LUCIA, None).describe() == "person:lucia removed"
    renamed = LUCIA.model_copy(update={"entity_id": "person:lucia.n"})
    assert EntityChange(LUCIA, renamed).describe() == "person:lucia is now person:lucia.n"
    assert "Navarro" not in EntityChange(LUCIA, None).describe()
