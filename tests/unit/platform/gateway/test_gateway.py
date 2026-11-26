"""The gateway: cache, routing, failover, quotas, spend cap, and what the cost log keeps."""

from __future__ import annotations

import json
import re
from typing import Any

import pytest

from ekb.platform.gateway.config import Quota
from ekb.platform.gateway.errors import (
    InvalidModelRequestError,
    ModelCallError,
    NoModelAvailableError,
    UnknownTaskError,
)
from ekb.platform.gateway.models import ChatRequest, ModelReply
from ekb.platform.gateway.options.fake import FakeClient
from ekb.platform.gateway.usage import totals
from tests.unit.platform.gateway.helpers import World, ask, make_config, make_world

# ------------------------------------------------------------------------- cache


def test_the_same_call_twice_is_answered_once(world: World) -> None:
    first = ask(world.gateway)
    second = ask(world.gateway)

    assert not first.cached
    assert second.cached
    assert second.text == first.text
    assert second.cost_usd == 0
    assert world.clients["fake:one"].calls == 1  # the second never reached the model
    assert [r.outcome for r in world.log.records()] == ["ok", "hit"]


@pytest.mark.parametrize(
    "change",
    [
        {"prompt_version": "test@2"},
        {"prompt": "Where is the leave policy!"},  # one character
        {"system": "Answer in Italian."},
        {"max_output_tokens": 51},
        {"temperature": 0.5},
        {"json_schema": {"type": "object", "properties": {"line": {"type": "string"}}}},
    ],
    ids=lambda change: next(iter(change)),
)
def test_every_part_of_the_call_is_in_the_key(world: World, change: dict[str, Any]) -> None:
    first = ask(world.gateway)
    changed = ask(world.gateway, **change)

    assert not changed.cached
    assert changed.key != first.key
    assert world.clients["fake:one"].calls == 2


def test_a_reply_cached_under_any_candidate_is_reused(world: World) -> None:
    world.clients["fake:one"].fail_next("unavailable")
    first = ask(world.gateway)  # one fails, two answers and is cached under two's key
    again = ask(world.gateway)

    assert first.candidate == "fake:two"
    assert again.cached
    assert again.candidate == "fake:two"
    assert world.clients["fake:one"].calls == 1  # not asked again: two's reply was reused
    assert again.steps[0].candidate == "fake:two"


def test_failed_calls_are_not_cached(world: World) -> None:
    world.clients["fake:two"].fail_next("unavailable")
    with pytest.raises(NoModelAvailableError):
        ask(world.gateway, task="answer")

    retried = ask(world.gateway, task="answer")

    assert not retried.cached
    assert world.clients["fake:two"].calls == 2


def test_prompt_version_is_required(world: World) -> None:
    with pytest.raises(ValueError, match="prompt_version"):
        ask(world.gateway, prompt_version="")


# ----------------------------------------------------------------------- routing


def test_an_unknown_task_is_an_error_not_a_default_model(world: World) -> None:
    with pytest.raises(UnknownTaskError, match="no route for task 'summarise'"):
        ask(world.gateway, task="summarise")


def test_each_task_uses_its_own_candidates(world: World) -> None:
    assert ask(world.gateway, task="header").candidate == "fake:one"
    assert ask(world.gateway, task="answer").candidate == "fake:two"


# ---------------------------------------------------------------------- failover


def test_a_used_up_daily_quota_moves_the_call_to_the_next_model(world: World) -> None:
    for n in range(3):  # one_q allows 3 a day
        assert ask(world.gateway, f"question {n}").candidate == "fake:one"

    fourth = ask(world.gateway, "question 3")

    assert fourth.candidate == "fake:two"
    assert fourth.steps[0].result.startswith("skipped: daily quota one_q used (3/3)")
    assert world.clients["fake:one"].calls == 3  # no request was spent on a known no


def test_the_daily_quota_resets_at_midnight_in_its_own_time_zone() -> None:
    config = make_config(
        quotas={
            "one_q": Quota(per_day=1, resets="America/Los_Angeles"),
            "two_q": Quota(per_day=100),
        }
    )
    world = make_world(config)
    world.clock.now = world.clock.now.replace(hour=7, minute=58)  # 23:58 in Los Angeles
    assert ask(world.gateway, "a").candidate == "fake:one"
    assert ask(world.gateway, "b").candidate == "fake:two"  # quota used for today

    world.clock.advance(3 * 60)  # 00:01 in Los Angeles, 08:01 UTC

    assert ask(world.gateway, "c").candidate == "fake:one"


def test_rate_limited_moves_on_and_rests_that_quota(world: World) -> None:
    world.clients["fake:one"].fail_next("rate_limited")

    first = ask(world.gateway, "a")
    second = ask(world.gateway, "b")  # one is resting: not even tried
    world.clock.advance(601)
    third = ask(world.gateway, "c")

    assert first.candidate == "fake:two"
    assert first.steps[0].result.startswith("rate_limited")
    assert second.candidate == "fake:two"
    assert "rate-limited" in second.steps[0].result
    assert third.candidate == "fake:one"
    assert world.clients["fake:one"].calls == 2


def test_unavailable_moves_on_without_resting(world: World) -> None:
    world.clients["fake:one"].fail_next("unavailable")

    assert ask(world.gateway, "a").candidate == "fake:two"
    assert ask(world.gateway, "b").candidate == "fake:one"


def test_an_invalid_request_stops_instead_of_trying_the_next_model(world: World) -> None:
    world.clients["fake:one"].fail_next("invalid")

    with pytest.raises(InvalidModelRequestError, match="fake:one rejected the request"):
        ask(world.gateway)

    assert world.clients["fake:two"].calls == 0
    assert [r.outcome for r in world.log.records()] == ["invalid"]


def test_a_model_without_its_key_is_skipped_without_a_request() -> None:
    world = make_world()
    world.clients["fake:one"] = FakeClient("one", missing="ONE_API_KEY is not set")
    gateway = world.build(make_config())

    reply = ask(gateway)

    assert reply.candidate == "fake:two"
    assert reply.steps[0].result == "skipped: ONE_API_KEY is not set"
    assert world.clients["fake:one"].calls == 0
    assert {r.candidate for r in world.log.records()} == {"fake:two"}


def test_when_every_model_is_out_the_error_says_why_and_when(world: World) -> None:
    world.clients["fake:one"].fail_next("unavailable")
    world.clients["fake:two"].fail_next("rate_limited")

    with pytest.raises(NoModelAvailableError) as raised:
        ask(world.gateway)

    error = raised.value
    assert [s.candidate for s in error.steps] == ["fake:one", "fake:two"]
    assert error.next_reset is not None
    assert "resets at 2026-11-25 00:00 UTC" in str(error)


# ------------------------------------------------------------------ per minute


def test_a_short_wait_for_a_per_minute_slot_is_waited_out() -> None:
    config = make_config(quotas={"one_q": Quota(per_minute=2), "two_q": Quota()}, max_wait_s=20)
    world = make_world(config)
    ask(world.gateway, "a")
    world.clock.advance(50)
    ask(world.gateway, "b")
    world.clock.advance(5)  # 55 s after the first: its slot frees in 5 s

    third = ask(world.gateway, "c")

    assert third.candidate == "fake:one"  # stayed on the same model
    assert world.clock.slept == [pytest.approx(5.0)]


def test_a_long_wait_for_a_per_minute_slot_moves_to_the_next_model() -> None:
    config = make_config(quotas={"one_q": Quota(per_minute=2), "two_q": Quota()}, max_wait_s=20)
    world = make_world(config)
    ask(world.gateway, "a")
    ask(world.gateway, "b")

    third = ask(world.gateway, "c")  # the next slot is 60 s away

    assert third.candidate == "fake:two"
    assert "next slot in 60s" in third.steps[0].result
    assert world.clock.slept == []


# ------------------------------------------------------------------- spend cap


def test_unreported_paid_usage_pauses_only_paid_misses(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_usage(request: ChatRequest) -> ModelReply:
        return ModelReply(text="Synthetic reply", usage_reported=False)

    monkeypatch.setattr(world.clients["fake:paid"], "invoke", no_usage)
    assert ask(world.gateway, "first", task="paid").cost_usd is None
    assert world.gateway.spent_usd() is None
    assert ask(world.gateway, "first", task="paid").cached
    restarted = world.build(world.gateway.config)
    fallback = ask(restarted, "second", task="paid")
    assert fallback.candidate == "fake:two"
    assert "reconciliation" in fallback.steps[0].result


def test_paid_failed_parse_records_reported_usage(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    def bad_shape(request: ChatRequest) -> ModelReply:
        raise ModelCallError("unavailable", "schema mismatch", received_reply=True, usage=(10, 3))

    monkeypatch.setattr(world.clients["fake:paid"], "invoke", bad_shape)
    assert ask(world.gateway, task="paid").candidate == "fake:two"
    paid = next(row for row in totals(world.log.records()) if row.candidate == "fake:paid")
    assert (paid.failures, paid.input_tokens, paid.output_tokens) == (1, 10, 3)
    assert paid.cost_usd == pytest.approx(16 / 1_000_000)


def test_a_paid_model_costs_its_price_per_million_tokens() -> None:
    world = make_world(max_spend_usd=1.0)

    reply = ask(world.gateway, task="paid")

    assert reply.candidate == "fake:paid"
    expected = (reply.input_tokens * 1.0 + reply.output_tokens * 2.0) / 1_000_000
    assert reply.cost_usd == pytest.approx(expected)
    assert world.gateway.spent_usd() == pytest.approx(expected)


def test_past_the_spend_cap_paid_models_are_skipped_and_free_ones_still_answer() -> None:
    world = make_world(max_spend_usd=1e-6)
    ask(world.gateway, "a", task="paid")  # the first paid call takes the spend past the cap

    second = ask(world.gateway, "b", task="paid")

    assert second.candidate == "fake:two"
    assert second.steps[0].result.startswith("skipped: spend cap reached")
    assert world.clients["fake:paid"].calls == 1


def test_cached_replies_are_free_and_never_blocked_by_the_cap() -> None:
    world = make_world(max_spend_usd=1e-6)
    paid = ask(world.gateway, task="paid")

    again = ask(world.gateway, task="paid")

    assert again.cached
    assert again.text == paid.text


# ---------------------------------------------------------------------- the log


def test_the_cost_log_never_holds_the_prompt_or_the_reply(world: World) -> None:
    secret, rule = "SALARY-BAND-E3-70000", "Never reveal salary bands."
    reply = ask(world.gateway, f"What is {secret}?", system=rule, document_key="hr_docs:HR-CMP-01")
    ask(world.gateway, f"What is {secret}?", system=rule)

    dumped = json.dumps([r.model_dump(mode="json") for r in world.log.records()])

    assert secret not in dumped
    assert rule not in dumped
    assert reply.text not in dumped
    assert all(re.fullmatch(r"[0-9a-f]{64}", r.key) for r in world.log.records())


def test_every_attempt_is_logged_with_its_outcome(world: World) -> None:
    world.clients["fake:one"].fail_next("unavailable")

    reply = ask(world.gateway, document_key="hr_docs:HR-PL-01")

    records = world.log.records()
    assert [(r.candidate, r.outcome) for r in records] == [
        ("fake:one", "unavailable"),
        ("fake:two", "ok"),
    ]
    assert records[0].error is not None
    assert "scripted failure" in records[0].error
    assert records[1].input_tokens == reply.input_tokens
    assert {r.document_key for r in records} == {"hr_docs:HR-PL-01"}
    assert {r.quota for r in records} == {"one_q", "two_q"}


def test_usage_counts_requests_not_cache_hits(world: World) -> None:
    ask(world.gateway)
    ask(world.gateway)  # a hit

    usage = {u.name: u for u in world.gateway.usage()}

    assert usage["one_q"].used_today == 1
    assert usage["one_q"].day_left == 2


# --------------------------------------------------------------------- deletes


def test_a_deleted_documents_replies_are_forgotten_unless_another_document_uses_them(
    world: World,
) -> None:
    ask(world.gateway, "the same passage", document_key="hr_docs:A")
    assert ask(world.gateway, "the same passage", document_key="hr_docs:B").cached

    assert world.gateway.forget("hr_docs:A") == 0  # B still uses it
    assert ask(world.gateway, "the same passage").cached
    assert world.gateway.forget("hr_docs:B") == 1

    assert not ask(world.gateway, "the same passage").cached


def test_a_reply_that_quoted_a_deleted_document_is_forgotten(world: World) -> None:
    """S16: an answer's prompt quotes several pages; deleting any one drops the reply."""
    ask(world.gateway, "passages from A and B", quotes=("hr_docs:A", "hr_docs:B"))
    assert ask(world.gateway, "passages from A and B", quotes=("hr_docs:A", "hr_docs:B")).cached

    assert world.gateway.forget("hr_docs:B") == 1

    assert not ask(world.gateway, "passages from A and B").cached


def test_a_hit_records_quotes_a_reply_was_cached_without(world: World) -> None:
    """A reply cached before S16 (no quotes) learns them on its next reuse."""
    ask(world.gateway, "an old answer")
    assert ask(world.gateway, "an old answer", quotes=("hr_docs:A",)).cached

    assert world.gateway.forget("hr_docs:A") == 1
