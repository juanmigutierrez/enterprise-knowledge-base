"""Port for T7 · Embed. Every embedding option implements :class:`Embedder`.

An embedder takes texts (built by the step from each chunk, see
``ekb.process.t7_embed.input``) and returns **one vector per text**, nothing
else. The step (:class:`~ekb.process.t7_embed.step.EmbedStep`) checks them and
puts them on the chunks, so an embedder never touches a chunk.

``model_id`` is stored with every vector (``Chunk.embedding_model``, D10). It
names the option, the input recipe, the model and the size:
``model@1(input@1, ollama:bge-m3, 1024)``. Vectors are only comparable with
vectors of the same ID, so a chunk whose ID differs from the current one must
be embedded again before it is searched with the new model; storing the ID makes
that a planned batch job, not a guess (D16 #8).

Embedding is a **gate**, not an enrichment: a chunk without a vector is never
published (``Chunk.publishable``). So an embedder that cannot answer raises,
and the document is retried, unlike T4's header, which is left empty.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class Vectors:
    """One document's vectors, in chunk order, and what made them."""

    vectors: tuple[tuple[float, ...], ...]
    model_id: str
    sent: int = 0
    """Texts a model embedded now (each distinct text once)."""
    cached: int = 0
    """Texts answered from the gateway's cache: embedded before, no request."""
    note: str = ""
    """In plain words: which model, and how many texts came from the cache."""
    int8: bool = False


@runtime_checkable
class Embedder(Protocol):
    """Turns texts into vectors of one fixed size."""

    @property
    def model_id(self) -> str:
        """The option, input recipe, model and size:
        ``model@1(input@1, ollama:bge-m3, 1024)``."""
        ...

    @property
    def dimensions(self) -> int:
        """How many numbers each vector has."""
        ...

    def embed(self, texts: Sequence[str], *, document_key: str | None = None) -> Vectors:
        """One vector per text, in order. ``document_key`` tags them in the gateway's
        cache, so a deleted document's vectors are forgotten with it."""
        ...
