"""``answer_cache: in_memory`` (S15): keeps and forgets, oldest first; never decides reuse."""

from __future__ import annotations

import pytest

from ekb.access.answer.options.cache_memory import InMemoryAnswerCache
from ekb.access.answer.port import CachedAnswer, Draft
from ekb.contracts import Scope

SCOPE = Scope(user_id="alice", principals=None, reason="", prefilter_id="acl@1")


def entry(question: str) -> CachedAnswer:
    draft = Draft(text="t", cited=(), writer_id="w")
    return CachedAnswer(
        question=question, scope=SCOPE, hits=(), passages=(), checked=(), draft=draft
    )


def test_it_keeps_and_drops_by_key() -> None:
    cache = InMemoryAnswerCache()
    cache.put("a", entry("one"))
    got = cache.get("a")
    assert got is not None
    assert got.question == "one"
    cache.drop("a")
    cache.drop("missing")
    assert cache.get("a") is None
    assert cache.size() == 0
    assert cache.cache_id == "in_memory@1(max=1000)"


def test_the_least_recently_used_answer_goes_first() -> None:
    cache = InMemoryAnswerCache(max_entries=2)
    cache.put("a", entry("a"))
    cache.put("b", entry("b"))
    cache.get("a")  # a is used again: b is now the oldest
    cache.put("c", entry("c"))
    assert cache.get("b") is None
    assert cache.get("a") is not None
    assert cache.size() == 2


def test_an_entry_says_it_is_stale_unless_told_otherwise() -> None:
    assert entry("q").still_current() is False
    with pytest.raises(ValueError, match="at least 1"):
        InMemoryAnswerCache(max_entries=0)
