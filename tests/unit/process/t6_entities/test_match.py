"""The exact-match rules (S9): full names and emails for people, team words for teams."""

from __future__ import annotations

import pytest

from ekb.contracts import EntityMention
from ekb.process.t6_entities.match import EMAIL, NAME, TEAM, Index, fold
from tests.unit.process.t6_entities.conftest import ENTITIES, person, team

INDEX = Index(ENTITIES)


def ids(text: str, index: Index = INDEX) -> list[str]:
    return [m.entity_id for m in index.find(text).mentions]


def test_the_synthetic_records_give_twelve_people_and_five_teams_none_ambiguous() -> None:
    assert (INDEX.people, INDEX.teams) == (12, 5)
    assert INDEX.ambiguous == ()


@pytest.mark.parametrize(
    "text",
    [
        "Ask Bruno Moretti before you book.",
        "ask bruno moretti before you book.",
        "Ask BRUNO MORETTI.",
        "Bruno\nMoretti approves it.",
        "Bruno Moretti's approval",
        "(Bruno Moretti)",
    ],
)
def test_a_full_name_is_found_whatever_its_case_or_spacing(text: str) -> None:
    assert ids(text) == ["person:bob"]


def test_accents_are_ignored_both_ways_and_the_words_are_shown_as_written() -> None:
    (mention,) = INDEX.find("Write to Lucia Navarro in Madrid.").mentions
    assert mention == EntityMention(entity_id="person:lucia", text="Lucia Navarro", rule=NAME)
    assert ids("Carla Gomez, Carla Gómez and CARLA GÓMEZ") == ["person:carla"]


def test_a_work_email_names_its_person_but_not_a_longer_address() -> None:
    (mention,) = INDEX.find("Mail daniela.ferri@example.com.").mentions
    assert (mention.entity_id, mention.text, mention.rule) == (
        "person:dana",
        "daniela.ferri@example.com",
        EMAIL,
    )
    assert ids("DANIELA.FERRI@EXAMPLE.COM") == ["person:dana"]
    assert ids("daniela.ferri@example.com.au or old.daniela.ferri@example.com") == []


@pytest.mark.parametrize(
    "text",
    [
        "Ask Bruno before you book.",  # a first name alone
        "Ask Moretti before you book.",  # a surname alone
        "Marco and Bianchi",  # both, but not together
        "Noah Martinez joined in May.",  # inside a longer word
        "Noah Martin-Leroy joined in May.",  # inside a hyphenated surname
        "Lucía Navarrete",
    ],
)
def test_part_of_a_name_is_not_a_mention(text: str) -> None:
    assert ids(text) == []


@pytest.mark.parametrize(
    ("text", "said"),
    [
        ("Tell the People team a week before.", "People team"),
        ("Approval from the Head of People and the CFO.", "Head of People"),
        ("Comunica las fechas al equipo de People.", "equipo de People"),
        ("Scrivi al team People.", "team People"),
        ("Ask the Finance\nteam.", "Finance\nteam"),
        ("the Engineering Team", "Engineering Team"),
    ],
)
def test_a_team_is_found_next_to_a_team_word(text: str, said: str) -> None:
    (mention,) = INDEX.find(text).mentions
    assert (mention.text, mention.rule) == (said, TEAM)


@pytest.mark.parametrize(
    "text",
    [
        "People who join mid-year get leave pro rata.",  # a bare team name
        "Engineering \u2013 Italy",  # a heading: left to the model tier
        "Tell the people team.",  # not written as the HR system writes it
        "Restricted to the compensation team.",  # no such team
        "Ask your manager.",
        "The Peoples team",  # a longer word
    ],
)
def test_a_team_name_alone_or_an_unknown_team_is_not_a_mention(text: str) -> None:
    assert ids(text) == []


def test_each_entity_is_reported_once_in_the_order_the_text_names_it() -> None:
    text = (
        "The People team (Daniela Ferri, daniela.ferri@example.com) and Bruno Moretti. "
        "Again: the People team."
    )
    found = INDEX.find(text).mentions
    assert [(m.entity_id, m.rule) for m in found] == [
        ("team:T-PPL", TEAM),
        ("person:dana", NAME),
        ("person:bob", NAME),
    ]


def test_a_name_two_entities_share_links_to_neither_and_is_reported() -> None:
    index = Index([person("marco", "Marco Bianchi"), person("marco2", "Marco Bianchi")])
    found = index.find("Ask Marco Bianchi.")
    assert found.mentions == ()
    assert found.ambiguous == ("'Marco Bianchi' names 2 entities (person:marco, person:marco2)",)
    assert index.ambiguous == found.ambiguous


def test_the_longest_name_wins_where_two_overlap() -> None:
    index = Index([person("ana", "Ana Ruiz"), person("anam", "Ana Ruiz Mora"), team("T-X", "Ruiz")])
    assert ids("Ask Ana Ruiz Mora.", index) == ["person:anam"]
    assert ids("Ask Ana Ruiz.", index) == ["person:ana"]


def test_an_ambiguous_longer_name_still_blocks_the_shorter_one_inside_it() -> None:
    index = Index(
        [person("a", "Ana Ruiz"), person("b", "Ana Ruiz Mora"), person("c", "Ana Ruiz Mora")]
    )
    found = index.find("Ask Ana Ruiz Mora.")
    assert found.mentions == ()  # not "Ana Ruiz": she is not the one named
    assert len(found.ambiguous) == 1


def test_no_entities_find_nothing() -> None:
    assert Index([]).find("Bruno Moretti and the People team").mentions == ()


def test_single_word_names_and_aliases_only_link_by_work_email() -> None:
    entity = person("bob", "Bruno").model_copy(update={"aliases": ("Moretti", "bob@example.com")})
    index = Index([entity])
    assert ids("Contact Bruno or Moretti", index) == []
    assert ids("Contact bob@example.com", index) == ["person:bob"]


def test_folding_keeps_where_each_character_came_from() -> None:
    folded = fold("Lucía  NAVARRO")
    assert folded.text == "lucia navarro"
    assert folded.span(0, len(folded.text)) == (0, len("Lucía  NAVARRO"))
    assert fold("Luci\u0301a").text == "lucia"  # a decomposed accent is dropped too
