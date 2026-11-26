"""Revocation gate (card 4.13's acceptance test, cached path included): a reader removed
in the source loses the page on the very next request, whether the index is stale or
not, whether the answer was cached or not, and whether the source answers or not.

Two kinds of revocation: on the page itself (the docs tool's readers), and by removing
someone from a group (the identity provider), which changes no page at all.
"""

from __future__ import annotations

import pytest

from ekb.app.lifecycle import MENTORING_KEY, MENTORING_QUESTION
from ekb.contracts import AccessVerdict
from tests.gates.story import Story, caller, pages

pytestmark = pytest.mark.filterwarnings(
    "ignore:You should not use the 'timeout' argument with the TestClient"
)

MENTORING_TEXT = ("Mentoring programme", "times a month", MENTORING_KEY)
"""Its title, its text and its key: none may reach someone who lost it."""


def test_a_page_revoked_in_the_source_is_withheld_on_the_next_request(story: Story) -> None:
    story.apply("add_page")
    warm = story.ask("alice", MENTORING_QUESTION)
    assert MENTORING_KEY in pages(warm.reply)
    assert warm.kept  # the cache is warm
    lucia = story.ask("lucia", MENTORING_QUESTION)
    assert MENTORING_KEY in pages(lucia.reply)

    story.apply("revoke_page", sync=False)  # the index still says all-staff
    since = len(story.replies)
    assert {c.acl.groups for c in story.chunks(MENTORING_KEY) if c.acl} == {
        frozenset({"all-staff"})
    }

    cached_path = story.ask("alice", MENTORING_QUESTION)
    assert not cached_path.reply.cached
    assert cached_path.discarded  # Q4 asked the docs tool, which no longer admits alice
    fresh_path = story.ask("alice", MENTORING_QUESTION)
    agent_writes_its_own = story.search("alice", MENTORING_QUESTION)
    for reply in (cached_path.reply, fresh_path.reply, agent_writes_its_own):
        assert MENTORING_KEY not in pages(reply)
    assert not story.told(*MENTORING_TEXT, users=("alice",), since=since)

    # Someone who still may read it gets it, cited, from the same stale index.
    still = story.ask("lucia", MENTORING_QUESTION)
    assert MENTORING_KEY in pages(still.reply)


def test_a_source_that_does_not_answer_releases_nothing_and_serves_no_cached_answer(
    story: Story,
) -> None:
    story.apply("add_page")
    story.apply("revoke_page", sync=False)
    assert MENTORING_KEY in pages(story.ask("lucia", MENTORING_QUESTION).reply)  # warm, allowed
    since = len(story.replies)

    story.outage.down = True
    try:
        for user in ("alice", "lucia"):
            outcome = story.ask(user, MENTORING_QUESTION)
            assert not outcome.reply.cached  # unknown is no: not even for lucia
            assert MENTORING_KEY not in pages(outcome.reply)
            assert any("could not be checked" in note for note in outcome.reply.notes)
            assert not outcome.kept
    finally:
        story.outage.down = False
    assert not story.told(*MENTORING_TEXT, since=since)

    # The source answers again: lucia gets the cited page; alice still does not.
    assert MENTORING_KEY in pages(story.ask("lucia", MENTORING_QUESTION).reply)
    assert MENTORING_KEY not in pages(story.ask("alice", MENTORING_QUESTION).reply)


def test_after_the_sync_the_index_agrees_and_no_vector_was_recomputed(story: Story) -> None:
    story.apply("add_page")
    before = {c.chunk_id: c.vector for c in story.chunks(MENTORING_KEY)}

    story.apply("revoke_page")  # the fast lane

    after = story.chunks(MENTORING_KEY)
    assert {c.acl.groups for c in after if c.acl} == {frozenset({"hr-team"})}
    assert {c.chunk_id: c.vector for c in after} == before
    principals = story.system.stores.permissions.principals("alice")
    assert MENTORING_KEY not in {
        c.document_key for c in story.system.stores.search.readable_by(principals)
    }


def test_removing_someone_from_a_group_revokes_without_changing_any_page(
    story: Story,
) -> None:
    story.apply("add_page")
    story.apply("revoke_page")  # hr-team only now
    warm = story.ask("lucia", MENTORING_QUESTION)
    assert MENTORING_KEY in pages(warm.reply)
    chunks = story.system.stores.search.chunks()

    story.apply("remove_from_group", sync=False)  # before the identity sync
    since = len(story.replies)
    stale = story.ask("lucia", MENTORING_QUESTION)
    assert not stale.reply.cached
    assert MENTORING_KEY not in pages(stale.reply)
    assert stale.result is not None
    denied = {
        w.access.verdict
        for w in stale.result.released.withheld
        if w.access.document_key == MENTORING_KEY
    }
    assert denied == {AccessVerdict.DENIED}  # ranked from the stale groups, held back by Q4

    story.sync()  # the identity sync: lucia's groups change, no page does
    assert story.system.stores.search.chunks() == chunks  # no chunk rewritten
    who = story.system.assistant.whoami(caller("lucia"))
    assert "hr-team" not in who.groups
    assert MENTORING_KEY not in pages(story.ask("lucia", MENTORING_QUESTION).reply)
    assert MENTORING_KEY not in pages(story.search("lucia", MENTORING_QUESTION))
    assert not story.told(*MENTORING_TEXT, users=("lucia",), since=since)
    assert MENTORING_KEY in pages(story.ask("bob", MENTORING_QUESTION).reply)  # still hr-team
