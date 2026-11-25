"""Q1 · Plan (S14): one search for a simple question, one per part for a question with several."""

from __future__ import annotations

import pytest

from ekb.access.query.q1_plan.options.none import NoPlanner
from ekb.access.query.q1_plan.options.rules import RulesPlanner

LEAVE = "How many days of annual leave do I get?"
REMOTE = "How many days can I work remotely?"


def test_none_searches_the_question_as_it_is() -> None:
    plan = NoPlanner().plan(f"{LEAVE} {REMOTE}")
    assert plan.searches == (f"{LEAVE} {REMOTE}",)
    assert plan.notes == ()
    assert plan.planner_id == "none@1"


def test_rules_make_one_search_per_question_mark_and_say_so() -> None:
    plan = RulesPlanner().plan(f"{LEAVE}  {REMOTE}")
    assert plan.searches == (LEAVE, REMOTE)
    assert plan.notes == ("split into 2 searches, one per part of the question",)
    assert plan.question == f"{LEAVE}  {REMOTE}"
    assert plan.planner_id == "rules@1(max_searches=3)"


@pytest.mark.parametrize(
    ("question", "searches"),
    [
        ("___; leave\rremote", ("leave", "remote")),
        ("annual leave", ("annual leave",)),
        ("  annual   leave  ", ("annual leave",)),  # trimmed, spaces collapsed
        (LEAVE, (LEAVE,)),  # one question mark at the end: nothing to split
        ("leave; remote work", ("leave", "remote work")),
        ("leave\nremote work\n", ("leave", "remote work")),
        ("¿Cuántos días? ¿Puedo teletrabajar?", ("¿Cuántos días?", "¿Puedo teletrabajar?")),
        ("Leave? leave? LEAVE?", ("Leave?",)),  # a repeated part is searched once
        ("Leave? ?? ; ...", ("Leave?",)),  # parts with no letter or digit are dropped
        ("", ("",)),  # nothing at all: the question itself, which Q3 reports as empty
        ("???", ("???",)),
    ],
)
def test_rules_split_on_question_marks_semicolons_and_line_breaks(
    question: str, searches: tuple[str, ...]
) -> None:
    assert RulesPlanner().plan(question).searches == searches


def test_parts_beyond_the_last_search_are_searched_together_so_no_word_is_lost() -> None:
    plan = RulesPlanner(max_searches=2).plan("Leave? Remote work? Expenses? Meal vouchers?")
    assert plan.searches == ("Leave?", "Remote work? Expenses? Meal vouchers?")
    assert plan.notes == (
        "split into 2 searches, one per part of the question",
        "the question has 4 parts: parts 2 to 4 are searched together",
    )


def test_rules_refuse_a_plan_with_no_search() -> None:
    with pytest.raises(ValueError, match="max_searches"):
        RulesPlanner(max_searches=0)
    assert RulesPlanner(max_searches=1).plan("Leave? Remote?").searches == ("Leave? Remote?",)
