"""Running T7 for one document's chunks, and checking every vector the embedder returned.

::

    chunks  = T6's result                                  # cut, headed, labelled, linked
    texts   = [embed_input(chunk) for chunk in chunks]     # header + section trail + text
    vectors = embedder.embed(texts, document_key=...)      # the option: model, fake
    chunks  = embedded(parsed, chunks, vectors, size)      # a vector and its ID on each

T7 runs right after T6, on the same document, so only where T2 decided
``process``. A permission change (the fast lane) or an unchanged save (a skip)
never embeds again: the vector comes from the text, which didn't change.

**The step checks, the embedder only answers.** Exactly one vector per chunk,
each of the embedder's size, every number finite, none all zeros. The step then
preserves signed int8 components and scales float vectors to length 1. A dot
product gives the cosine for unit vectors. And chunks that already carry a vector
or an ACL are refused: a new version is a new set of chunks, and T7 runs
before T8.

After T7 a chunk has its sensitivity (T5) and its vector, but still no ACL:
it is **not publishable** until T8 (S11) has set who may read it.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from ekb.contracts import Chunk, ParsedDocument
from ekb.process.t7_embed.input import embed_input
from ekb.process.t7_embed.port import Embedder, Vectors


@dataclass(frozen=True)
class EmbedResult:
    document: ParsedDocument
    chunks: tuple[Chunk, ...]
    """T6's chunks with their ``vector`` and ``embedding_model``."""
    model_id: str
    sent: int
    """Chunks whose text a model embedded now."""
    cached: int
    """Chunks whose vector came from the cache: the same text was embedded before."""
    note: str
    reason: str


class VectorState(StrEnum):
    """How a chunk's vector compares with what the current embedder would make."""

    NONE = "none"
    """No vector yet: T7 has not run on it."""
    CURRENT = "current"
    """Made by the current embedder: comparable with a question embedded now."""
    STALE = "stale"
    """Made by another model, size or input recipe: embed it again before searching."""


def vector_state(chunk: Chunk, current_model_id: str) -> VectorState:
    """Whether ``chunk``'s vector was made by ``current_model_id`` (D10, D16 #8)."""
    if chunk.vector is None:
        return VectorState.NONE
    if chunk.embedding_model == current_model_id:
        return VectorState.CURRENT
    return VectorState.STALE


def summary(result: Vectors, chunks: int) -> str:
    """``4 chunks embedded: 1 sent, 3 from the cache``."""
    if chunks == 0:
        return "no chunks to embed"
    return f"{chunks} chunks embedded: {result.sent} sent, {result.cached} from the cache"


class EmbedStep:
    """T7 with one embedder."""

    def __init__(self, embedder: Embedder) -> None:
        self._embedder = embedder

    @property
    def model_id(self) -> str:
        return self._embedder.model_id

    @property
    def dimensions(self) -> int:
        return self._embedder.dimensions

    def handle(self, document: ParsedDocument, chunks: tuple[Chunk, ...]) -> EmbedResult:
        check_input(document, chunks)
        if chunks:
            texts = [embed_input(chunk) for chunk in chunks]
            result = self._embedder.embed(texts, document_key=document.document_key)
        else:
            result = Vectors(vectors=(), model_id=self._embedder.model_id)
        out = embedded(document, chunks, result, self._embedder.dimensions)
        return EmbedResult(
            document=document,
            chunks=out,
            model_id=result.model_id,
            sent=result.sent,
            cached=result.cached,
            note=result.note,
            reason=f"embedded with {result.model_id}: {summary(result, len(chunks))}",
        )


class EmbedError(ValueError):
    """T7 was given chunks it must not embed, or an embedder broke its promise."""


def check_input(document: ParsedDocument, chunks: Sequence[Chunk]) -> None:
    for chunk in chunks:
        where = f"{document.document_key} v{document.version}, {chunk.chunk_id}"
        if (chunk.document_key, chunk.document_version) != (
            document.document_key,
            document.version,
        ):
            raise EmbedError(f"{where} is from {chunk.document_key} v{chunk.document_version}")
        if chunk.acl is not None:
            raise EmbedError(f"{where} already has an ACL: T7 runs before T8")
        if chunk.vector is not None:
            raise EmbedError(f"{where} already has a vector: a new version is a new set of chunks")


def embedded(
    document: ParsedDocument, chunks: tuple[Chunk, ...], result: Vectors, dimensions: int
) -> tuple[Chunk, ...]:
    """Attach each checked vector in its declared representation."""
    where = f"{document.document_key} v{document.version}"
    if len(result.vectors) != len(chunks):
        raise EmbedError(
            f"{where}: {len(chunks)} chunks but {len(result.vectors)} vectors "
            f"from {result.model_id}"
        )
    out: list[Chunk] = []
    for chunk, vector in zip(chunks, result.vectors, strict=True):
        if result.int8:
            if (
                len(vector) != dimensions
                or not any(vector)
                or not all(isinstance(v, int) and -128 <= v <= 127 for v in vector)
            ):
                raise EmbedError(f"{chunk.chunk_id}: invalid signed int8 vector")
            stored = vector
        else:
            stored = unit(vector, dimensions, chunk.chunk_id)
        out.append(
            chunk.model_copy(
                update={
                    "vector": stored,
                    "embedding_model": result.model_id,
                }
            )
        )
    return tuple(out)


def unit(vector: Sequence[float], dimensions: int, chunk_id: str = "") -> tuple[float, ...]:
    """``vector`` scaled to length 1, after checking its size and its numbers."""
    if len(vector) != dimensions:
        raise EmbedError(f"{chunk_id}: a vector of {len(vector)} numbers, expected {dimensions}")
    if not all(math.isfinite(v) for v in vector):
        raise EmbedError(f"{chunk_id}: a vector with a non-finite number")
    scale = max(map(abs, vector), default=0.0)
    if scale == 0:
        raise EmbedError(f"{chunk_id}: a vector of zeros points nowhere")
    scaled = tuple(v / scale for v in vector)
    norm = math.hypot(*scaled)
    return tuple(v / norm for v in scaled)


def cosine(first: Sequence[float], second: Sequence[float]) -> float:
    """How close two vectors point, from -1 to 1: their dot product once both have length 1."""
    if len(first) != len(second):
        raise ValueError("vectors of different sizes cannot be compared")
    if not any(first) or not any(second):
        return 0.0
    return math.sumprod(unit(first, len(first)), unit(second, len(second)))
