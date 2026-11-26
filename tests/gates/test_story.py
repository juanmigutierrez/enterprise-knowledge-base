"""The whole self-maintenance story (S16), step by step: each scripted change in a source
reaches the answers and the stores as its ``expect`` says, with no manual step.

The other gate modules prove one property each; this one follows the story as the demo
and the Lifecycle screen tell it.
"""

from __future__ import annotations

import pytest

from ekb.app.lifecycle import (
    ACTIONS,
    BY_ID,
    HOTEL_QUESTION,
    MENTORING_KEY,
    MENTORING_QUESTION,
    NEW_TRAVEL_KEY,
    OLD_TRAVEL_KEY,
    ActionRefusedError,
    check,
)
from ekb.contracts import TraceStatus
from tests.gates.story import Story, pages

pytestmark = pytest.mark.filterwarnings(
    "ignore:You should not use the 'timeout' argument with the TestClient"
)


def mentoring(story: Story, user: str) -> set[str]:
    return pages(story.ask(user, MENTORING_QUESTION).reply)


def test_each_step_has_the_effect_it_promises(story: Story) -> None:
    system = story.system

    story.apply("add_page")
    reply = story.ask("alice", MENTORING_QUESTION).reply
    assert MENTORING_KEY in pages(reply)
    assert "2 times a month" in reply.answer
    assert story.links(MENTORING_KEY) == {"person:lucia", "team:T-PPL"}

    story.apply("edit_page")
    reply = story.ask("alice", MENTORING_QUESTION).reply
    assert "3 times a month" in reply.answer
    assert "2 times a month" not in reply.model_dump_json()

    story.apply("revoke_page")
    assert MENTORING_KEY not in mentoring(story, "alice")
    assert MENTORING_KEY in mentoring(story, "lucia")

    story.apply("remove_from_group")
    assert MENTORING_KEY not in mentoring(story, "lucia")
    assert MENTORING_KEY in mentoring(story, "bob")

    story.apply("hire_record")
    assert system.stores.entities.get("person:sara") is None  # no account: fail closed
    assert "person:sara" not in story.links(MENTORING_KEY)

    story.apply("hire_account")
    assert system.stores.entities.get("person:sara") is not None
    assert story.links(MENTORING_KEY) == {"person:lucia", "person:sara", "team:T-PPL"}

    story.apply("leaver")
    assert system.stores.entities.get("person:lucia") is None
    assert story.links(MENTORING_KEY) == {"person:sara", "team:T-PPL"}

    story.apply("delete_page")
    hotel = pages(story.ask("alice", HOTEL_QUESTION).reply)
    assert OLD_TRAVEL_KEY not in hotel
    assert NEW_TRAVEL_KEY in hotel

    assert len(system.pipeline.dead_letters) == 0
    assert len(system.pipeline.relinks) == 0


def test_the_page_was_relinked_twice_and_never_edited_for_it(story: Story) -> None:
    story.apply("add_page")
    seen = len(story.system.traces)
    version = story.chunks(MENTORING_KEY)[0].document_version

    for action in ("hire_record", "hire_account", "leaver"):
        story.apply(action)

    statuses = [t.status for t in list(story.system.traces)[seen:]]
    assert statuses.count(TraceStatus.RELINKED) == 2  # the hire, then the leaver
    assert story.chunks(MENTORING_KEY)[0].document_version == version


def test_the_script_keeps_its_order() -> None:
    assert [a.id for a in ACTIONS] == [
        "add_page",
        "edit_page",
        "revoke_page",
        "remove_from_group",
        "hire_record",
        "hire_account",
        "leaver",
        "delete_page",
    ]
    applied: list[str] = []
    for action in ACTIONS:
        assert check(action.id, applied) is action
        applied.append(action.id)
    with pytest.raises(ActionRefusedError, match="already applied"):
        check("add_page", applied)
    with pytest.raises(ActionRefusedError, match="needs add_page first"):
        check("edit_page", [])
    with pytest.raises(ActionRefusedError, match="no scripted action"):
        check("drop_tables", [])
    assert {a.id for a in ACTIONS if a.listing} == {"hire_account", "leaver"}
    assert BY_ID["leaver"].caught_by.startswith("the HR full listing only")
