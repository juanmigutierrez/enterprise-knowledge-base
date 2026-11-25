"""Port for Q3 · Retrieve. Every retrieval option implements :class:`Retriever`.

A retriever takes a question and Q2's :class:`~ekb.contracts.Scope` and returns
the chunks to answer from, best first (:class:`~ekb.contracts.Retrieval`). It
asks the search store, which ranks **only the chunks the scope admits** (the
pre-filter), so nothing it returns, counts or notes can be about a chunk the
asker may not read.

Two helpers it may be given:

- a :class:`QueryEncoder`, to turn the question into a vector. Vectors compare
  only with vectors from the same model (D10), so the encoder names its
  ``space``: the embedding model ID the chunks must carry (T7's ``model_id``).
  The composition root builds it from T7's own embedder, so the question and
  the chunks are always embedded by the same model. Without one, only the
  keyword leg runs, and the retrieval says so;
- a :class:`Reranker`, to reorder the fused candidates before the top ``k`` are
  kept (card 4.6).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from ekb.contracts import Hit, Retrieval, Scope


class EncoderUnavailableError(RuntimeError):
    """The embedding model cannot embed the question right now.

    Q3 then searches with the keyword leg alone and says so: the pre-filter
    does not depend on vectors, so this never widens what anyone can read.
    """


@runtime_checkable
class QueryEncoder(Protocol):
    """Turns a question into a vector comparable with the published chunks' vectors."""

    @property
    def space(self) -> str:
        """The ``Chunk.embedding_model`` these vectors compare with, e.g.
        ``model@1(input@1, ollama:bge-m3, 1024)``."""
        ...

    def encode(self, question: str) -> Sequence[float]:
        """One vector. Raises :class:`EncoderUnavailableError` when the model can't answer."""
        ...


@runtime_checkable
class Reranker(Protocol):
    """Reorders the fused candidates, best first; Q3 keeps the top ``k`` afterwards."""

    @property
    def reranker_id(self) -> str:
        """The option and its version, e.g. ``none@1``."""
        ...

    def rerank(self, question: str, hits: Sequence[Hit]) -> tuple[Hit, ...]:
        """The same hits, reordered. Ranks are renumbered by Q3 afterwards."""
        ...


@runtime_checkable
class Retriever(Protocol):
    """Finds the chunks to answer one question from, among those the scope admits."""

    @property
    def retriever_id(self) -> str:
        """The option, version and settings, e.g. ``hybrid_rrf@1(top_k=5, ...)``."""
        ...

    def retrieve(self, question: str, scope: Scope) -> Retrieval: ...
