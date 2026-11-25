"""The ``exact_match`` option (S9) on the synthetic pages and on a page naming people."""

from __future__ import annotations

from ekb.process.t6_entities.options.exact_match import ExactMatchLinker
from ekb.process.t6_entities.port import words
from tests.unit.process.t6_entities.conftest import ENTITIES, chunks_of, person, synthetic

LINKER = ExactMatchLinker()


def linked_ids(page_id: str) -> list[tuple[str, ...]]:
    document, chunks = synthetic(page_id)
    linking = LINKER.link(document, chunks, ENTITIES)
    return [tuple(m.entity_id for m in mentions) for mentions in linking.mentions]


def test_the_id_names_the_option_and_the_rules_version() -> None:
    assert LINKER.linker_id == "exact_match@2"


def test_the_remote_work_policy_names_the_people_team_twice() -> None:
    assert linked_ids("HR-PL-04") == [(), ("team:T-PPL",), (), ("team:T-PPL",)]


def test_the_salary_bands_name_the_head_of_people_but_not_engineering() -> None:
    document, chunks = synthetic("HR-CMP-01")
    linking = LINKER.link(document, chunks, ENTITIES)
    found = [(m.entity_id, m.text) for mentions in linking.mentions for m in mentions]
    assert found == [("team:T-PPL", "Head of People")]  # "Engineering - Italy" is no mention


def test_the_spanish_birth_leave_names_the_people_team_in_spanish() -> None:
    document, chunks = synthetic("HR-PL-07")
    linking = LINKER.link(document, chunks, ENTITIES)
    texts = [m.text for mentions in linking.mentions for m in mentions]
    assert texts == ["equipo de People"]


def test_no_seed_page_names_a_person() -> None:
    from tests.unit.process.t4_chunk.conftest import PAGES

    for found in PAGES:
        for mentions in LINKER.link(*synthetic(found.id), ENTITIES).mentions:
            assert all(m.entity_id.startswith("team:") for m in mentions), found.id


def test_a_contacts_page_links_names_and_emails_but_not_first_names() -> None:
    document, chunks = chunks_of(
        "# HR contacts\n\n## Milan\nBruno Moretti leads the People team. For payroll, write "
        "to daniela.ferri@example.com.\n\n## Madrid\nLucia Navarro. Marco covers when she "
        "is away.\n"
    )
    linking = LINKER.link(document, chunks, ENTITIES)
    assert [[(m.entity_id, m.text) for m in mentions] for mentions in linking.mentions] == [
        [
            ("person:bob", "Bruno Moretti"),
            ("team:T-PPL", "People team"),
            ("person:dana", "daniela.ferri@example.com"),
        ],
        [("person:lucia", "Lucia Navarro")],
    ]


def test_the_section_trail_is_read_but_the_context_header_is_not() -> None:
    document, chunks = chunks_of("# Ask the People team\n\n## How\nWrite a ticket.\n")
    headed = tuple(
        chunk.model_copy(update={"context_header": "Bruno Moretti explains how to ask."})
        for chunk in chunks
    )
    assert all("Bruno" not in passage for passage in words(headed[0]))
    (mentions,) = LINKER.link(document, headed, ENTITIES).mentions
    assert [m.entity_id for m in mentions] == ["team:T-PPL"]  # from the title, not the header


def test_the_note_says_what_was_known_and_what_was_skipped() -> None:
    document, chunks = chunks_of("# Contacts\n\nMarco Bianchi and Alice Rossi.\n")
    assert LINKER.link(document, chunks, ENTITIES).note == "matched against 12 people and 5 teams"
    twins = (*ENTITIES, person("marco2", "Marco Bianchi"))
    linking = LINKER.link(document, chunks, twins)
    assert [m.entity_id for m in linking.mentions[0]] == ["person:alice"]
    assert "not linked, ambiguous: 'Marco Bianchi' names 2 entities" in linking.note


def test_a_name_cannot_start_in_a_heading_and_finish_in_the_body() -> None:
    document, chunks = chunks_of("# Contacts\n\n## Bruno\nMoretti handles payroll.\n")
    assert LINKER.link(document, chunks, ENTITIES).mentions == ((),)


def test_with_no_entities_nothing_is_linked_and_the_note_says_why() -> None:
    document, chunks = synthetic("HR-PL-04")
    linking = LINKER.link(document, chunks, ())
    assert all(mentions == () for mentions in linking.mentions)
    assert linking.note == "no entities known yet: nothing to link to"
