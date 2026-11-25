"""``q3_retrieve: hybrid_rrf``: keywords and vectors, fused by rank, then the top k (S13).

For one question and one scope:

1. **Nothing for no one.** A scope that reads nothing (unknown person, leaver)
   gets an empty retrieval; the store is not even asked.
2. **Keyword leg.** BM25 over the chunks the scope admits: the best
   ``candidates`` that share a word with the question. It finds exact terms
   ("Ley 10/2021", "HR-PL-07", a person's name) that vectors blur.
3. **Vector leg.** The question is embedded with T7's model (the encoder) and
   compared with the admitted chunks' vectors: the ``candidates`` closest. It
   finds paraphrases and other languages ("parental leave" → "congedo
   parentale") that share no word. Without an encoder, or when the model is
   down, only the keyword leg runs, and the retrieval says so.
4. **Fusion.** Reciprocal rank fusion (``fusion``) merges the two lists by rank.
5. **Rerank, then cut.** The reranker, if any, reorders every fused candidate;
   the first ``top_k`` are kept (card 4.7: five go to the answering model).

Card 4.5 compares this with each leg alone; the settings are code defaults
until an experiment says otherwise (``top_k`` comes from ``config/``).
"""

from __future__ import annotations

from collections.abc import Sequence

from ekb.access.query.q3_retrieve.fusion import RRF_K, fuse, renumbered
from ekb.access.query.q3_retrieve.port import EncoderUnavailableError, QueryEncoder, Reranker
from ekb.contracts import Retrieval, Scope
from ekb.platform.registry import register
from ekb.stores.search.port import SearchStore

VERSION = 1
TOP_K = 5
"""Chunks kept for the answer (card 4.7's starting choice)."""
CANDIDATES = 20
"""Chunks each leg proposes before fusion: enough for the fused top 5 to draw on both."""


@register("q3_retrieve", "hybrid_rrf")
class HybridRrfRetriever:
    def __init__(
        self,
        index: SearchStore,
        encoder: QueryEncoder | None = None,
        reranker: Reranker | None = None,
        top_k: int = TOP_K,
        candidates: int = CANDIDATES,
        rrf_k: int = RRF_K,
    ) -> None:
        if top_k < 1 or candidates < 1:
            raise ValueError("top_k and candidates must be at least 1")
        if rrf_k < 0:
            raise ValueError("rrf_k must not be negative")
        self._index = index
        self._encoder = encoder
        self._reranker = reranker
        self._top_k, self._candidates, self._rrf_k = top_k, candidates, rrf_k

    @property
    def retriever_id(self) -> str:
        return (
            f"hybrid_rrf@{VERSION}(top_k={self._top_k}, candidates={self._candidates}, "
            f"rrf_k={self._rrf_k}, rerank={self._rerank_id})"
        )

    @property
    def _rerank_id(self) -> str:
        return self._reranker.reranker_id if self._reranker is not None else "none"

    @property
    def encoder(self) -> QueryEncoder | None:
        """What embeds the questions; ``None``: the keyword leg only."""
        return self._encoder

    def retrieve(self, question: str, scope: Scope) -> Retrieval:
        empty = Retrieval(question=question, user_id=scope.user_id, retriever_id=self.retriever_id)
        principals = scope.principals
        if principals is None:
            return empty.model_copy(
                update={"notes": (f"nothing was searched: {scope.user_id} reads nothing",)}
            )
        if not question.strip():
            return empty.model_copy(update={"notes": ("an empty question: nothing to search",)})

        encoded, notes = self._encode(question)
        snapshot = self._index.ranking_snapshot(principals)
        keyword = snapshot.keyword(question, self._candidates)
        vector = snapshot.nearest(*encoded, self._candidates) if encoded is not None else None
        if vector and vector.skipped:
            notes.append(
                f"{vector.skipped} of {vector.searched} chunks were embedded with another "
                "model: only the keyword leg can find them until they are embedded again"
            )
        fused = fuse(keyword.hits, vector.hits if vector else (), self._rrf_k)
        if self._reranker is not None:
            fused = self._reranker.rerank(question, fused)
        if not snapshot.is_current():
            return empty.model_copy(update={"notes": ("searchable content changed: search again",)})
        hits = renumbered(fused[: self._top_k])
        return empty.model_copy(
            update={
                "hits": hits,
                "searched": keyword.searched,
                "keyword_found": len(keyword.hits),
                "vector_found": len(vector.hits) if vector else 0,
                "vector_leg": vector is not None,
                "notes": tuple(notes),
            }
        )

    def _encode(self, question: str) -> tuple[tuple[Sequence[float], str] | None, list[str]]:
        if self._encoder is None:
            return None, ["keyword leg only: no embedding model for questions"]
        try:
            return (self._encoder.encode(question), self._encoder.space), []
        except EncoderUnavailableError as exc:
            return None, [f"keyword leg only: the embedding model did not answer ({exc})"]
