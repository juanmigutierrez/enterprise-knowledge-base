"""The query path (S13): Q2 → Q3 wired, with the options the profile names.

Box 6's first two steps, as one call: :meth:`Search.ask` takes who is asking and
their question, and returns Q2's scope and Q3's retrieval::

    (user, question) ─▶ Q2 pre-filter ─▶ Scope(principals | None)
                     ─▶ Q3 retrieve: keyword leg + vector leg in the search store,
                        each over the chunks the scope admits ─▶ RRF ─▶ rerank ─▶ top k

Three choices are made here, in the composition root, and nowhere else:

- **Which options.** :func:`ekb.app.choices.choose_query` reads them from the
  profile: ``acl_and_cedar`` runs as its stand-in ``acl`` until S15, and the
  configured reranker as ``none`` until card 4.6; both swaps are printed.
- **What embeds the questions.** T7's own embedder, the one the worker uses
  (:func:`query_encoder`), so a question's vector is always comparable with the
  chunks' vectors: same model, same gateway task, and Q3 compares only chunks
  whose ``embedding_model`` is T7's current ID. A question belongs to no
  document, so its vector is cached untagged: the cache keeps a hash of the
  text and the vector, never the question. When T7 runs fake vectors (offline,
  the ``ci`` profile) there is nothing to compare by meaning: no encoder, so Q3
  runs its keyword leg alone and says so.
- **Which stores.** The same search and permission stores the worker writes
  (:class:`ekb.app.pipeline.Stores`), so a change the worker applies is in the
  next search.

Q1 (planning) and Q4 (re-check, redact, cite) wrap this in S14
(:mod:`ekb.app.answer`); the MCP tools call that in S15.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from time import perf_counter

from ekb.access.query.q2_prefilter.port import PreFilter
from ekb.access.query.q3_retrieve.port import (
    EncoderUnavailableError,
    QueryEncoder,
    Reranker,
    Retriever,
)
from ekb.app.choices import Choices
from ekb.app.embed import embedder
from ekb.app.pipeline import Stores
from ekb.contracts import Retrieval, SearchResult
from ekb.platform import registry
from ekb.platform.gateway.errors import GatewayError
from ekb.platform.gateway.port import TextEmbedder
from ekb.process.t7_embed.port import Embedder

MEANINGLESS = frozenset({"fake"})
"""T7 options whose vectors carry no meaning: comparing a question with them ranks at random."""


class EmbedderEncoder:
    """Q3's :class:`~ekb.access.query.q3_retrieve.port.QueryEncoder` over T7's embedder."""

    def __init__(self, inner: Embedder) -> None:
        self._inner = inner

    @property
    def space(self) -> str:
        return self._inner.model_id

    def encode(self, question: str) -> Sequence[float]:
        try:
            return self._inner.embed([question]).vectors[0]
        except GatewayError as exc:
            raise EncoderUnavailableError(str(exc)) from exc


def query_encoder(write: Choices, gateway: TextEmbedder | None = None) -> QueryEncoder | None:
    """What embeds the questions: T7's embedder as the write path's choices name it, or
    ``None`` when its vectors mean nothing (``fake``)."""
    option = write.option("t7_embed")
    if option in MEANINGLESS:
        return None
    return EmbedderEncoder(embedder(option, gateway, **write.params("t7_embed")))


def encoder_note(write: Choices, encoder: QueryEncoder | None) -> str:
    """One line on how questions are embedded, for the demo and the inspector."""
    if encoder is not None:
        return f"questions embedded by T7's model: {encoder.space}"
    return (
        f"questions not embedded: t7_embed runs {write.option('t7_embed')} vectors, which "
        "carry no meaning, so Q3 runs its keyword leg alone"
    )


@dataclass(frozen=True)
class Search:
    """Q2 then Q3, for one person and one question."""

    q2: PreFilter
    q3: Retriever

    def ask(self, user_id: str, question: str) -> SearchResult:
        start = perf_counter()
        scope = self.q2.scope(user_id)
        retrieval = self.q3.retrieve(question, scope)
        current = self.q2.scope(user_id)
        if current.principals != scope.principals:
            retrieval = Retrieval(
                question=question,
                user_id=user_id,
                retriever_id=self.q3.retriever_id,
                notes=("group membership changed during search: search again",),
            )
        return SearchResult(
            scope=current, retrieval=retrieval, ms=(perf_counter() - start) * 1000.0
        )


def build_search(query: Choices, stores: Stores, encoder: QueryEncoder | None = None) -> Search:
    """Q2 and Q3 as ``query`` (from :func:`~ekb.app.choices.choose_query`) names them,
    reading ``stores``; ``encoder`` embeds the questions (``None``: keyword leg only)."""
    q2 = registry.create(
        "q2_prefilter",
        query.option("q2_prefilter"),
        permissions=stores.permissions,
        **query.params("q2_prefilter"),
    )
    if not isinstance(q2, PreFilter):
        raise TypeError(f"q2_prefilter option {query.option('q2_prefilter')!r} is not a PreFilter")
    reranker = registry.create("q3_rerank", query.option("q3_rerank"), **query.params("q3_rerank"))
    if not isinstance(reranker, Reranker):
        raise TypeError(f"q3_rerank option {query.option('q3_rerank')!r} is not a Reranker")
    q3 = registry.create(
        "q3_retrieve",
        query.option("q3_retrieve"),
        index=stores.search,
        encoder=encoder,
        reranker=reranker,
        **query.params("q3_retrieve"),
    )
    if not isinstance(q3, Retriever):
        raise TypeError(f"q3_retrieve option {query.option('q3_retrieve')!r} is not a Retriever")
    return Search(q2, q3)
