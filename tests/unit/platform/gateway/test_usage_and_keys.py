"""Quota windows, totals and the cache key: the arithmetic the gateway relies on."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ekb.platform.gateway.config import Quota
from ekb.platform.gateway.keys import cache_key
from ekb.platform.gateway.models import CallRecord, ChatRequest, Outcome
from ekb.platform.gateway.usage import day_start, next_reset, quota_usage, spent_usd, totals

NOON = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)


def rec(
    outcome: Outcome = "ok",
    *,
    at: datetime = NOON,
    quota: str | None = "q",
    task: str = "header",
    candidate: str = "fake:one",
    tokens: tuple[int, int] = (10, 2),
    cost: float = 0.0,
) -> CallRecord:
    return CallRecord(
        at=at,
        task=task,
        candidate=candidate,
        prompt_version="v1",
        key="0" * 64,
        outcome=outcome,
        quota=quota,
        input_tokens=tokens[0],
        output_tokens=tokens[1],
        cost_usd=cost,
    )


# ------------------------------------------------------------------- the day


def test_google_s_day_starts_at_midnight_pacific() -> None:
    # 24 November is standard time in Los Angeles: UTC-8.
    assert day_start(NOON, "America/Los_Angeles") == datetime(2026, 11, 24, 8, 0, tzinfo=UTC)
    assert next_reset(NOON, "America/Los_Angeles") == datetime(2026, 11, 25, 8, 0, tzinfo=UTC)


def test_before_pacific_midnight_it_is_still_yesterday_there() -> None:
    early = datetime(2026, 11, 24, 7, 30, tzinfo=UTC)  # 23:30 on the 23rd in Los Angeles

    assert day_start(early, "America/Los_Angeles") == datetime(2026, 11, 23, 8, 0, tzinfo=UTC)


def test_openrouter_s_day_starts_at_midnight_utc() -> None:
    assert day_start(NOON, "UTC") == datetime(2026, 11, 24, 0, 0, tzinfo=UTC)
    assert next_reset(NOON, "UTC") == datetime(2026, 11, 25, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------- the counts


def test_quota_usage_counts_requests_of_that_quota_today_hits_excluded() -> None:
    records = [
        rec(at=NOON - timedelta(seconds=30)),
        rec("rate_limited", at=NOON - timedelta(minutes=5)),  # failed attempts count
        rec("hit"),  # no request
        rec(quota="other"),
        rec(at=NOON - timedelta(days=1)),  # yesterday
    ]

    usage = quota_usage("q", Quota(per_minute=5, per_day=10), records, NOON)

    assert usage.used_last_minute == 1
    assert usage.used_today == 2
    assert usage.day_left == 8
    assert usage.resets_at == datetime(2026, 11, 25, 0, 0, tzinfo=UTC)


def test_totals_per_task_and_model() -> None:
    records = [
        rec(tokens=(10, 2), cost=0.001),
        rec("hit", tokens=(10, 2)),
        rec("unavailable", tokens=(0, 0)),
        rec(task="answer", candidate="fake:two", tokens=(30, 5)),
    ]

    rows = {(t.task, t.candidate): t for t in totals(records)}

    header = rows[("header", "fake:one")]
    assert (header.requests, header.hits, header.failures) == (2, 1, 1)
    assert (header.input_tokens, header.output_tokens) == (10, 2)  # sent to the provider only
    assert header.cost_usd == pytest.approx(0.001)
    assert rows[("answer", "fake:two")].input_tokens == 30
    assert spent_usd(records) == pytest.approx(0.001)


# ------------------------------------------------------------------ the key


def test_the_key_is_stable_and_depends_on_model_params_and_version() -> None:
    request = ChatRequest(prompt="Carry up to 5 days", max_output_tokens=60)
    base = cache_key("groq:openai/gpt-oss-20b", {"reasoning_effort": "low"}, "header@1", request)

    assert base == cache_key(
        "groq:openai/gpt-oss-20b", {"reasoning_effort": "low"}, "header@1", request
    )
    assert len(base) == 64
    assert base != cache_key(
        "groq:openai/gpt-oss-120b", {"reasoning_effort": "low"}, "header@1", request
    )
    assert base != cache_key(
        "groq:openai/gpt-oss-20b", {"reasoning_effort": "high"}, "header@1", request
    )
    assert base != cache_key(
        "groq:openai/gpt-oss-20b", {"reasoning_effort": "low"}, "header@2", request
    )


def test_the_key_does_not_depend_on_the_order_of_params() -> None:
    request = ChatRequest(prompt="x", max_output_tokens=5)

    assert cache_key("p:m", {"a": 1, "b": 2}, "v", request) == cache_key(
        "p:m", {"b": 2, "a": 1}, "v", request
    )
