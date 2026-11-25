"""The T7 step (S10): one checked unit vector per chunk, its ID with it, and still no ACL."""

from __future__ import annotations

import math
from collections.abc import Sequence

import pytest

from ekb.contracts import Acl
from ekb.process.t7_embed.input import embed_input
from ekb.process.t7_embed.options.fake import FakeEmbedder
from ekb.process.t7_embed.port import Vectors
from ekb.process.t7_embed.step import (
    EmbedError,
    EmbedStep,
    VectorState,
    cosine,
    embedded,
    unit,
    vector_state,
)
from tests.unit.process.t7_embed.conftest import chunks_of, synthetic


class Fixed:
    """An embedder that returns whatever it is given, and remembers what it was sent."""

    model_id = "fixed@1"
    dimensions = 3

    def __init__(self, *vectors: tuple[float, ...]) -> None:
        self.vectors = vectors
        self.sent: list[tuple[list[str], str | None]] = []

    def embed(self, texts: Sequence[str], *, document_key: str | None = None) -> Vectors:
        self.sent.append((list(texts), document_key))
        return Vectors(self.vectors, self.model_id, sent=len(texts))


def test_each_chunk_gets_a_unit_vector_and_the_id_that_made_it() -> None:
    document, chunks = synthetic("HR-PL-01")
    result = EmbedStep(FakeEmbedder(dimensions=32)).handle(document, chunks)

    assert len(result.chunks) == len(chunks) > 1
    for before, after in zip(chunks, result.chunks, strict=True):
        assert after.vector is not None
        assert len(after.vector) == 32
        assert math.isclose(math.sqrt(sum(v * v for v in after.vector)), 1.0, rel_tol=1e-9)
        assert after.embedding_model == "fake@1(input@1, sha256, 32)"
        assert after.model_copy(update={"vector": None, "embedding_model": None}) == before
    assert result.reason == (
        f"embedded with fake@1(input@1, sha256, 32): {len(chunks)} chunks embedded: "
        f"{len(chunks)} sent, 0 from the cache"
    )


def test_the_embedder_is_sent_each_chunks_input_and_the_document_key() -> None:
    document, chunks = chunks_of("# Leave\n\nYou may carry 5 days.\n")
    fixed = Fixed((1.0, 0.0, 0.0))

    EmbedStep(fixed).handle(document, chunks)

    assert fixed.sent == [([embed_input(c) for c in chunks], document.document_key)]


def test_a_vector_and_a_label_still_do_not_make_a_chunk_searchable_without_an_acl() -> None:
    """The publication gate (card 4.14): T7 opens nothing on its own; T8 (S11) sets the ACL."""
    document, chunks = synthetic("HR-PL-01")
    result = EmbedStep(FakeEmbedder()).handle(document, chunks)

    for chunk in result.chunks:
        assert chunk.sensitivity is not None
        assert chunk.vector is not None
        assert chunk.acl is None
        assert not chunk.publishable
    with_acl = result.chunks[0].model_copy(update={"acl": Acl(groups=("all-staff",))})
    assert with_acl.publishable  # what T8 will add


def test_vectors_are_scaled_to_length_one() -> None:
    document, chunks = chunks_of("# Leave\n\nYou may carry 5 days.\n")
    result = EmbedStep(Fixed((3.0, 4.0, 0.0))).handle(document, chunks)

    assert result.chunks[0].vector == pytest.approx((0.6, 0.8, 0.0))


@pytest.mark.parametrize(
    ("vectors", "message"),
    [
        ((), "1 chunks but 0 vectors"),
        (((1.0, 0.0),), "2 numbers, expected 3"),
        (((1.0, math.nan, 0.0),), "non-finite"),
        (((0.0, 0.0, 0.0),), "zeros points nowhere"),
    ],
    ids=["count", "size", "nan", "zeros"],
)
def test_a_broken_vector_is_refused(vectors: tuple[tuple[float, ...], ...], message: str) -> None:
    document, chunks = chunks_of("# Leave\n\nYou may carry 5 days.\n")
    assert len(chunks) == 1

    with pytest.raises(EmbedError, match=message):
        EmbedStep(Fixed(*vectors)).handle(document, chunks)


def test_the_step_refuses_chunks_from_another_document_or_already_past_t7_or_t8() -> None:
    document, chunks = synthetic("HR-PL-01")
    other, _ = synthetic("HR-PL-02")
    step = EmbedStep(FakeEmbedder())

    with pytest.raises(EmbedError, match="is from"):
        step.handle(other, chunks)
    with pytest.raises(EmbedError, match="T7 runs before T8"):
        step.handle(document, (chunks[0].model_copy(update={"acl": Acl(groups=("x",))}),))
    with pytest.raises(EmbedError, match="already has a vector"):
        step.handle(document, (chunks[0].model_copy(update={"vector": (1.0,)}),))


def test_no_chunks_no_call() -> None:
    document, _ = chunks_of("# Leave\n\nYou may carry 5 days.\n")
    fixed = Fixed()

    result = EmbedStep(fixed).handle(document, ())

    assert result.chunks == ()
    assert fixed.sent == []
    assert result.reason == "embedded with fixed@1: no chunks to embed"


def test_a_vector_made_by_another_model_is_stale() -> None:
    document, chunks = synthetic("HR-PL-01")
    (chunk, *_) = EmbedStep(FakeEmbedder(dimensions=32)).handle(document, chunks).chunks

    assert vector_state(chunks[0], "fake@1(input@1, sha256, 32)") is VectorState.NONE
    assert vector_state(chunk, "fake@1(input@1, sha256, 32)") is VectorState.CURRENT
    assert vector_state(chunk, "model@1(input@1, ollama:bge-m3, 1024)") is VectorState.STALE
    assert vector_state(chunk, "fake@1(input@2, sha256, 32)") is VectorState.STALE


def test_cosine_is_one_for_the_same_direction_and_zero_for_a_right_angle() -> None:
    assert cosine((1.0, 0.0), (2.0, 0.0)) == pytest.approx(1.0)
    assert cosine((1.0, 0.0), (0.0, 3.0)) == pytest.approx(0.0)
    assert cosine((1.0, 0.0), (-1.0, 0.0)) == pytest.approx(-1.0)
    with pytest.raises(ValueError, match="different sizes"):
        cosine((1.0,), (1.0, 0.0))


@pytest.mark.parametrize("scale", [1.7e308, 5e-324])
def test_unit_and_cosine_preserve_extreme_finite_directions(scale: float) -> None:
    vector = (scale, -scale)
    assert unit(vector, 2) == pytest.approx((2**-0.5, -(2**-0.5)))
    assert cosine(vector, (1.0, -1.0)) == pytest.approx(1.0)


def test_unit_and_embedded_check_on_their_own() -> None:
    assert unit((0.0, 2.0), 2) == (0.0, 1.0)
    document, chunks = chunks_of("# Leave\n\nYou may carry 5 days.\n")
    with pytest.raises(EmbedError, match="1 chunks but 2 vectors"):
        embedded(document, chunks, Vectors(((1.0,), (1.0,)), "x"), 1)
