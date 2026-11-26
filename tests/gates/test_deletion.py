"""Deletion gate (deleted-content rate = 0, card 4.14): what a source deletes is never
returned again, by any path, and nothing of it is kept.

Three kinds of delete: a page the webhook (here the listing) reports, an HR record only
the full listing can see, and a delete still waiting in the change log. And three places
a deleted page could linger: the search store, the answer cache and the model cache.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import pytest

from ekb.access.answer.port import Draft
from ekb.app.lifecycle import (
    HOTEL_QUESTION,
    MENTORING_KEY,
    MENTORING_QUESTION,
    NEW_TRAVEL_KEY,
    OLD_TRAVEL_KEY,
)
from ekb.contracts import AccessVerdict, Passage
from tests.gates.story import Story, built, pages

pytestmark = pytest.mark.filterwarnings(
    "ignore:You should not use the 'timeout' argument with the TestClient"
)

OLD_TRAVEL_TEXT = ("€120", "Business travel policy (2024", OLD_TRAVEL_KEY)
"""What only HR-PL-99 says (HR-PL-06 names "HR-PL-99" in its own text, not its key)."""


def test_a_deleted_page_is_never_cited_again_fresh_or_cached(story: Story) -> None:
    warm = story.ask("alice", HOTEL_QUESTION)
    assert OLD_TRAVEL_KEY in pages(warm.reply)
    assert warm.kept

    story.apply("delete_page")

    for _ in range(2):  # fresh, then whatever was kept from that
        reply = story.ask("alice", HOTEL_QUESTION).reply
        assert OLD_TRAVEL_KEY not in pages(reply)
        assert NEW_TRAVEL_KEY in pages(reply)  # the policy that replaced it
    assert OLD_TRAVEL_KEY not in pages(story.search("bob", HOTEL_QUESTION))
    assert not story.told(*OLD_TRAVEL_TEXT, since=1)


def test_nothing_of_a_deleted_page_stays_in_any_store(story: Story) -> None:
    system = story.system
    story.ask("alice", HOTEL_QUESTION)
    story.ask("carla", HOTEL_QUESTION)
    cached = system.assistant.cache.size()

    story.apply("delete_page")

    assert story.chunks(OLD_TRAVEL_KEY) == ()
    assert system.stores.permissions.readers(OLD_TRAVEL_KEY) is None
    assert system.changelog.rawstore.versions(OLD_TRAVEL_KEY) == []  # originals purged
    record = system.changelog.registry.get(OLD_TRAVEL_KEY)
    assert record is not None
    assert record.deleted  # a tombstone, holding no content
    # The answer cache: every answer that searched, checked or cited it went at the purge.
    assert system.forgotten_answers == cached == 2
    assert system.assistant.cache.size() == 0


def test_a_model_reply_that_quoted_a_deleted_page_is_dropped_from_the_model_cache(
    tmp_path: Path,
) -> None:
    """S15 left this to S16: an answer reply quotes several pages, so it is forgotten as
    soon as any one of them is deleted, even while the others are alive."""
    with built(tmp_path, "ci", writer="model") as story:
        reply = story.ask("alice", HOTEL_QUESTION).reply
        assert not reply.cached
        size = story.gateway.cache.size()
        assert size >= 1

        story.apply("delete_page")

        assert story.gateway.cache.size() == size - 1
        again = story.ask("alice", HOTEL_QUESTION)
        assert not again.reply.cached  # neither from the answer cache nor the model cache
        log = story.gateway.log.records()
        assert log[-1].outcome == "ok"  # the model was asked again: no hit
        assert story.gateway.cache.size() == size


def test_a_delete_still_waiting_in_the_change_log_is_held_back_at_once(story: Story) -> None:
    """The worker has not run: the index still holds the page. Q4 asks the source, which
    says it is gone, and the cached answer is not served."""
    story.ask("alice", HOTEL_QUESTION)
    since = len(story.replies)

    story.apply("delete_page", sync=False)
    outcome = story.ask("alice", HOTEL_QUESTION)

    assert story.chunks(OLD_TRAVEL_KEY) != ()  # stale index
    assert not outcome.reply.cached
    assert OLD_TRAVEL_KEY not in pages(outcome.reply)
    assert outcome.result is not None
    verdicts = {
        w.access.verdict
        for w in outcome.result.released.withheld
        if w.access.document_key == OLD_TRAVEL_KEY
    }
    assert verdicts == {AccessVerdict.DELETED}
    assert not story.told(*OLD_TRAVEL_TEXT, since=since)


def test_an_hr_record_deleted_without_a_trace_is_found_by_the_full_listing(story: Story) -> None:
    story.apply("add_page")
    assert "person:lucia" in story.links(MENTORING_KEY)
    system = story.system

    story.apply("leaver", sync=False)
    system.sync(listings=False)  # the delta: it never reports a delete
    assert system.stores.entities.get("person:lucia") is not None
    system.sync(listings=True)  # the full listing: the record is gone, so is the entity

    assert system.stores.entities.get("person:lucia") is None
    assert "person:lucia" not in story.links(MENTORING_KEY)  # re-linked, not edited
    assert len(system.pipeline.dead_letters) == 0
    assert MENTORING_KEY in pages(story.ask("alice", MENTORING_QUESTION).reply)


def test_a_deleted_page_comes_back_only_as_a_new_page(story: Story) -> None:
    """A page recreated under the same ID after a delete is new content, processed from
    scratch: nothing of the deleted version comes back with it."""
    story.apply("add_page")
    story.sources.docs.delete("/pages/HR-PL-11", headers={"X-Actor": "dana"}).raise_for_status()
    story.sync()
    assert story.chunks(MENTORING_KEY) == ()

    story.sources.docs.post(
        "/pages",
        json={
            "id": "HR-PL-11",
            "title": "Mentoring",
            "language": "en",
            "department": "people",
            "acl": {"groups": ["all-staff"]},
            "body": "# Mentoring\n\nThe programme is paused until March.",
        },
        headers={"X-Actor": "dana"},
    ).raise_for_status()
    story.sync()

    texts = " ".join(chunk.text for chunk in story.chunks(MENTORING_KEY))
    assert "paused until March" in texts
    assert "times a month" not in texts


def test_purge_waits_for_an_answer_that_already_read_the_deleted_page(tmp_path: Path) -> None:
    with built(tmp_path, "ci", writer="model") as story, ThreadPoolExecutor(max_workers=1) as pool:
        assistant = story.system.assistant
        entered = threading.Event()
        resume = threading.Event()

        class DelayedWriter:
            writer_id = assistant.writer.writer_id

            def write(self, question: str, passages: Sequence[Passage]) -> Draft:
                assert OLD_TRAVEL_KEY in {p.citation.document_key for p in passages}
                entered.set()
                assert resume.wait(10)
                return assistant.writer.write(question, passages)

        @contextmanager
        def observing_purge() -> Iterator[None]:
            guard = assistant.release_guard
            if guard.acquire(blocking=False):
                guard.release()
                pytest.fail("purge could pass an ask still writing from the old page")
            resume.set()
            with guard:
                yield

        story.system.pipeline.purge_guard = observing_purge()
        story.system.assistant = replace(assistant, writer=DelayedWriter())
        initial = story.gateway.cache.size()
        pending = pool.submit(story.ask, "alice", HOTEL_QUESTION)
        try:
            assert entered.wait(10)
            story.apply("delete_page")
        finally:
            resume.set()
        outcome = pending.result(timeout=10)
        assert not outcome.reply.citations
        assert not outcome.kept
        assert story.gateway.cache.size() == initial
        assert assistant.cache.size() == 0
