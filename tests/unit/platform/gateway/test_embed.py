"""The gateway's embedding path (S10): per-text cache, one model, no failover, forget on delete."""

from __future__ import annotations

import json
import math
from unittest.mock import patch

import pytest

from ekb.platform.gateway.config import GatewayConfig, ModelSpec, Quota
from ekb.platform.gateway.errors import (
    InvalidModelRequestError,
    NoModelAvailableError,
    TaskKindError,
    UnknownTaskError,
)
from ekb.platform.gateway.gateway import ModelGateway
from ekb.platform.gateway.models import EmbeddingReply, EmbedReply
from ekb.platform.gateway.options.fake import FakeEmbeddingClient
from tests.unit.platform.gateway.helpers import World, ask, make_config, make_world

LEAVE = "You may carry up to 5 unused days into the next year."
EXPENSES = "Expense claims must be filed within 30 days."
OFFICE = "The Milan office closes at 18:00 on Fridays."


def embed(
    gateway: ModelGateway,
    *texts: str,
    task: str = "embed",
    input_version: str = "input@1",
    document_key: str | None = "hr_docs:HR-PL-01",
) -> EmbedReply:
    return gateway.embed(task, texts, input_version=input_version, document_key=document_key)


def vec(world: World) -> FakeEmbeddingClient:
    return world.embedders["fake:vec"]


def quota_world(per_day: int | None = None) -> GatewayConfig:
    """Only the embedding model, counted against a quota of its own."""
    return make_config(
        quotas={"vec_q": Quota(per_minute=10, per_day=per_day)},
        models={"fake:vec": ModelSpec(quota="vec_q", kind="embedding", dimensions=8)},
        tasks={"embed": ("fake:vec",)},
    )


# ------------------------------------------------------------------------- reuse


def test_each_text_is_embedded_once_then_comes_from_the_cache(world: World) -> None:
    first = embed(world.gateway, LEAVE, EXPENSES)
    second = embed(world.gateway, LEAVE, EXPENSES)

    assert (first.sent, first.cached) == (2, 0)
    assert (second.sent, second.cached) == (0, 2)
    assert second.vectors == first.vectors
    assert vec(world).calls == 1  # the second call made no request
    assert [(r.outcome, r.items) for r in world.log.records()] == [("ok", 2), ("hit", 2)]


def test_an_edit_sends_only_the_texts_that_changed(world: World) -> None:
    embed(world.gateway, LEAVE, EXPENSES, OFFICE)
    edited = embed(world.gateway, LEAVE, "Expense claims must be filed within 60 days.", OFFICE)

    assert (edited.sent, edited.cached) == (1, 2)
    assert vec(world).sent[-1] == ["Expense claims must be filed within 60 days."]


def test_the_same_text_twice_in_one_call_is_sent_once(world: World) -> None:
    reply = embed(world.gateway, LEAVE, LEAVE, EXPENSES)

    assert reply.sent == 2
    assert vec(world).sent == [[LEAVE, EXPENSES]]
    assert reply.vectors[0] == reply.vectors[1]


def test_one_vector_per_text_in_order_of_the_declared_size(world: World) -> None:
    reply = embed(world.gateway, OFFICE, LEAVE)

    assert reply.candidate == "fake:vec"
    assert reply.dimensions == 8
    assert len(reply.vectors) == 2
    assert all(len(v) == 8 for v in reply.vectors)
    assert reply.vectors[0] != reply.vectors[1]
    assert reply.keys[0] != reply.keys[1]


def test_the_input_version_is_part_of_every_key_and_required(world: World) -> None:
    first = embed(world.gateway, LEAVE)
    other = embed(world.gateway, LEAVE, input_version="input@2")

    assert other.sent == 1
    assert other.keys != first.keys
    with pytest.raises(ValueError, match="input_version is required"):
        embed(world.gateway, LEAVE, input_version="")


def test_a_cached_vector_of_another_size_is_not_reused(world: World) -> None:
    embed(world.gateway, LEAVE)
    resized = make_config(
        models={"fake:vec": ModelSpec(kind="embedding", dimensions=16)},
        tasks={"embed": ("fake:vec",)},
    )
    world.embedders["fake:vec"] = FakeEmbeddingClient("vec", dimensions=16)

    reply = embed(world.build(resized), LEAVE)

    assert reply.sent == 1
    assert len(reply.vectors[0]) == 16


def test_zero_reply_is_retried_and_a_legacy_zero_cache_entry_is_repaired(world: World) -> None:
    with (
        patch.object(vec(world), "embed", return_value=EmbeddingReply(vectors=((0.0,) * 8,))),
        pytest.raises(NoModelAvailableError, match="all-zero"),
    ):
        embed(world.gateway, LEAVE)
    assert world.cache.size() == 0
    first = embed(world.gateway, LEAVE)
    key = first.keys[0]
    entry = world.cache.get(key)
    assert entry is not None
    bad = entry.reply.model_copy(update={"vector": (0.0,) * 8})
    world.cache.put(key, entry.model_copy(update={"reply": bad}))
    assert embed(world.gateway, LEAVE).sent == 1
    assert embed(world.gateway, LEAVE).vectors == first.vectors


def test_no_texts_no_request_and_no_record(world: World) -> None:
    reply = embed(world.gateway)

    assert reply.vectors == ()
    assert vec(world).calls == 0
    assert world.log.records() == []


# ------------------------------------------------------------------- one model


def test_the_task_names_its_one_model_and_its_size_before_any_call(world: World) -> None:
    assert world.gateway.embedding_model("embed") == ("fake:vec", 8)
    assert vec(world).calls == 0


def test_tasks_of_the_wrong_kind_are_refused(world: World) -> None:
    with pytest.raises(TaskKindError, match="call complete"):
        embed(world.gateway, LEAVE, task="header")
    with pytest.raises(TaskKindError, match="call embed"):
        ask(world.gateway, task="embed")
    with pytest.raises(UnknownTaskError):
        embed(world.gateway, LEAVE, task="nope")


def test_an_unavailable_model_fails_the_call_and_nothing_is_cached(world: World) -> None:
    vec(world).fail_next("unavailable")

    with pytest.raises(NoModelAvailableError, match="fake:vec: unavailable"):
        embed(world.gateway, LEAVE, EXPENSES)
    retried = embed(world.gateway, LEAVE, EXPENSES)

    assert retried.sent == 2  # nothing was kept from the failure
    assert [(r.outcome, r.items) for r in world.log.records()] == [("unavailable", 2), ("ok", 2)]


def test_a_rate_limited_model_rests_its_quota() -> None:
    world = make_world(quota_world())
    vec(world).fail_next("rate_limited")
    with pytest.raises(NoModelAvailableError):
        embed(world.gateway, LEAVE)

    with pytest.raises(NoModelAvailableError, match="rate-limited"):
        embed(world.gateway, LEAVE)
    assert vec(world).calls == 1  # the second was skipped, no request
    world.clock.advance(601)
    assert embed(world.gateway, LEAVE).sent == 1


def test_an_invalid_request_stops(world: World) -> None:
    vec(world).fail_next("invalid")

    with pytest.raises(InvalidModelRequestError):
        embed(world.gateway, LEAVE)


def test_vectors_of_the_wrong_size_are_refused_as_a_configuration_error(world: World) -> None:
    world.embedders["fake:vec"] = FakeEmbeddingClient("vec", dimensions=8, wrong_size=4)
    gateway = world.build(world.gateway.config)

    with pytest.raises(InvalidModelRequestError, match="4 numbers per vector"):
        embed(gateway, LEAVE)
    assert world.cache.size() == 0


def test_a_model_without_its_key_or_past_its_quota_is_skipped_without_a_request() -> None:
    config = quota_world(per_day=1)
    world = make_world(config)
    embed(world.gateway, LEAVE)

    with pytest.raises(NoModelAvailableError, match="daily quota vec_q used"):
        embed(world.gateway, EXPENSES)
    assert embed(world.gateway, LEAVE).cached == 1  # the cache still answers

    world.embedders["fake:vec"] = FakeEmbeddingClient("vec", dimensions=8, missing="OLLAMA down")
    with pytest.raises(NoModelAvailableError, match="OLLAMA down"):
        embed(world.build(config), OFFICE)


# ----------------------------------------------------------------- deletes, log


def test_a_deleted_documents_vectors_are_forgotten_unless_another_document_uses_them(
    world: World,
) -> None:
    embed(world.gateway, LEAVE, EXPENSES, document_key="hr_docs:HR-PL-01")
    embed(world.gateway, EXPENSES, document_key="hr_docs:HR-PL-02")  # shares one text

    dropped = world.gateway.forget("hr_docs:HR-PL-01")

    assert dropped == 1  # the leave text; the expenses text is still used by HR-PL-02
    assert embed(world.gateway, EXPENSES, document_key="hr_docs:HR-PL-02").cached == 1
    assert embed(world.gateway, LEAVE, document_key="hr_docs:HR-PL-03").sent == 1


def test_the_cost_log_never_holds_the_texts_or_the_vectors(world: World) -> None:
    reply = embed(world.gateway, LEAVE, EXPENSES)
    embed(world.gateway, LEAVE)

    dumped = json.dumps([r.model_dump(mode="json") for r in world.log.records()])
    assert "carry" not in dumped
    assert "Expense" not in dumped
    assert str(reply.vectors[0][0]) not in dumped
    assert all(r.prompt_version == "input@1" for r in world.log.records())
    assert all(r.document_key == "hr_docs:HR-PL-01" for r in world.log.records())


def test_fake_vectors_are_unit_length_and_say_nothing_about_meaning(world: World) -> None:
    same, paraphrase = embed(
        world.gateway, LEAVE, "Up to 5 unused days carry into next year."
    ).vectors

    assert math.isclose(math.sqrt(sum(v * v for v in same)), 1.0, rel_tol=1e-9)
    cosine = sum(a * b for a, b in zip(same, paraphrase, strict=True))
    assert cosine < 0.9  # a paraphrase is just another text to the fake
