"""``t7_embed: model`` (S10): vectors through the gateway's embed task, reused and forgotten."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from ekb.contracts import Chunk
from ekb.platform.gateway.config import ModelSpec
from ekb.platform.gateway.errors import NoModelAvailableError
from ekb.platform.gateway.models import EmbeddingReply
from ekb.process.t7_embed.input import embed_input
from ekb.process.t7_embed.options.model import ModelEmbedder
from ekb.process.t7_embed.step import EmbedStep
from tests.unit.platform.gateway.helpers import make_config, make_world
from tests.unit.process.t7_embed.conftest import chunks_of, synthetic


def test_its_id_names_the_input_recipe_and_the_tasks_one_model_and_size() -> None:
    world = make_world()
    embedder = ModelEmbedder(world.gateway)

    assert embedder.model_id == "model@1(input@1, fake:vec, 8)"
    assert embedder.dimensions == 8
    assert world.embedders["fake:vec"].calls == 0  # known from the configuration


def test_int8_round_trip_preserves_signed_components_and_has_a_distinct_id() -> None:
    model = ModelSpec(
        kind="embedding", dimensions=1024, params={"model_kwargs": {"embedding_types": ["int8"]}}
    )
    world = make_world(make_config(models={"fake:vec": model}, tasks={"embed": ("fake:vec",)}))
    step = EmbedStep(ModelEmbedder(world.gateway))
    doc, chunks = chunks_of("# Leave\n\nSynthetic policy.\n")
    vector = (-128, 127, 0, 1) * 256
    client = world.embedders["fake:vec"]
    with patch.object(client, "embed", return_value=EmbeddingReply(vectors=(vector,))) as call:
        first = step.handle(doc, chunks)
        again = step.handle(doc, chunks)
    call.assert_called_once()
    assert first.model_id == "model@1(input@1, fake:vec, 1024, int8)"
    assert (first.sent, again.cached) == (1, 1)
    loaded = Chunk.model_validate_json(again.chunks[0].model_dump_json())
    assert loaded.vector == vector
    assert all(isinstance(v, int) for v in loaded.vector)
    assert not loaded.publishable


@pytest.mark.parametrize("bad", [128, -129, 0.5])
def test_int8_model_rejects_invalid_components(bad: float) -> None:
    model = ModelSpec(
        kind="embedding", dimensions=2, params={"model_kwargs": {"embedding_types": ["int8"]}}
    )
    world = make_world(make_config(models={"fake:vec": model}, tasks={"embed": ("fake:vec",)}))
    with (
        patch.object(
            world.embedders["fake:vec"], "embed", return_value=EmbeddingReply(vectors=((1, bad),))
        ),
        pytest.raises(NoModelAvailableError, match="signed int8"),
    ):
        ModelEmbedder(world.gateway).embed(["test"])
    assert world.cache.size() == 0


def test_a_document_is_embedded_once_then_comes_from_the_cache() -> None:
    world = make_world()
    step = EmbedStep(ModelEmbedder(world.gateway))
    document, chunks = synthetic("HR-PL-01")

    first = step.handle(document, chunks)
    again = step.handle(document, chunks)

    assert (first.sent, first.cached) == (len(chunks), 0)
    assert (again.sent, again.cached) == (0, len(chunks))
    assert [c.vector for c in again.chunks] == [c.vector for c in first.chunks]
    assert world.embedders["fake:vec"].sent == [[embed_input(c) for c in chunks]]
    assert {r.document_key for r in world.log.records()} == {document.document_key}
    assert {r.prompt_version for r in world.log.records()} == {"input@1"}
    assert first.chunks[0].embedding_model == "model@1(input@1, fake:vec, 8)"


def test_an_edit_sends_only_the_chunks_whose_text_changed() -> None:
    world = make_world()
    step = EmbedStep(ModelEmbedder(world.gateway))
    body = "# Leave\n\n## Carry-over\n\nUp to 5 days.\n\n## Requests\n\nAsk your manager.\n"
    step.handle(*chunks_of(body, key="hr_docs:HR-PL-01"))

    edited = step.handle(*chunks_of(body.replace("5 days", "7 days"), key="hr_docs:HR-PL-01"))

    assert edited.sent == 1
    assert edited.cached == len(edited.chunks) - 1
    assert "7 days" in world.embedders["fake:vec"].sent[-1][0]


def test_when_the_model_cannot_answer_the_step_fails_and_keeps_nothing() -> None:
    world = make_world()
    world.embedders["fake:vec"].fail_next("unavailable")
    step = EmbedStep(ModelEmbedder(world.gateway))
    document, chunks = synthetic("HR-PL-01")

    with pytest.raises(NoModelAvailableError):
        step.handle(document, chunks)
    assert world.cache.size() == 0
    assert step.handle(document, chunks).sent == len(chunks)  # the retry works


def test_purging_the_document_forgets_its_vectors() -> None:
    world = make_world()
    step = EmbedStep(ModelEmbedder(world.gateway))
    document, chunks = synthetic("HR-PL-01")
    step.handle(document, chunks)

    dropped = world.gateway.forget(document.document_key)

    assert dropped == len({embed_input(c) for c in chunks})
    assert world.cache.size() == 0
