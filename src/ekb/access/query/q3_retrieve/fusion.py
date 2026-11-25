"""Reciprocal rank fusion (RRF): how Q3 merges the keyword and vector legs (card 4.5).

The two legs score on different scales (BM25 is unbounded, cosine sits between
-1 and 1), so their scores can't be added. RRF uses only the **ranks**: a chunk
at rank ``r`` in a leg earns ``1 / (rrf_k + r)``, and its fused score is the sum
over the legs that found it::

    keyword: #1 HR-PL-01#2   #2 HR-PL-04#1
    vector:  #1 HR-PL-02#1   #2 HR-PL-01#2
    fused:   HR-PL-01#2 = 1/61 + 1/62 = 0.0325   (found by both: first)
             HR-PL-02#1 = 1/61        = 0.0164
             HR-PL-04#1 = 1/62        = 0.0161

``rrf_k`` (60, from the original paper by Cormack, Clarke and Büttcher, 2009)
keeps one leg's first place from outweighing broad agreement: a chunk both legs
rank in their top few beats a chunk only one leg ranks first. Ties are broken
by the better rank in either leg, then by document and position, so the order is
always the same.

:func:`merge` applies the same rule to whole searches (S14): when Q1 splits a
question, each part's ranking counts as one list.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ekb.contracts import Chunk, Hit
from ekb.stores.search.port import Scored

RRF_K = 60


@dataclass
class _Found:
    chunk: Chunk
    keyword_rank: int | None = None
    keyword_score: float | None = None
    vector_rank: int | None = None
    vector_score: float | None = None

    def fused(self, rrf_k: int) -> float:
        ranks = (self.keyword_rank, self.vector_rank)
        return sum(1 / (rrf_k + rank) for rank in ranks if rank is not None)

    def best_rank(self) -> int:
        return min(rank for rank in (self.keyword_rank, self.vector_rank) if rank is not None)


def fuse(
    keyword: Sequence[Scored], vector: Sequence[Scored], rrf_k: int = RRF_K
) -> tuple[Hit, ...]:
    """Every chunk either leg found, ranked by fused score (rank 1 is the best)."""
    if rrf_k < 0:
        raise ValueError("rrf_k must not be negative")
    found: dict[str, _Found] = {}
    for rank, scored in enumerate(keyword, start=1):
        entry = found.setdefault(scored.chunk.chunk_id, _Found(scored.chunk))
        entry.keyword_rank, entry.keyword_score = rank, scored.score
    for rank, scored in enumerate(vector, start=1):
        entry = found.setdefault(scored.chunk.chunk_id, _Found(scored.chunk))
        entry.vector_rank, entry.vector_score = rank, scored.score
    ordered = sorted(
        found.values(),
        key=lambda f: (-f.fused(rrf_k), f.best_rank(), f.chunk.document_key, f.chunk.position),
    )
    return tuple(
        Hit(
            chunk=f.chunk,
            rank=rank,
            score=f.fused(rrf_k),
            keyword_rank=f.keyword_rank,
            keyword_score=f.keyword_score,
            vector_rank=f.vector_rank,
            vector_score=f.vector_score,
        )
        for rank, f in enumerate(ordered, start=1)
    )


def renumbered(hits: Sequence[Hit]) -> tuple[Hit, ...]:
    """The hits with ranks 1, 2, 3 … in their current order (after a reranker)."""
    return tuple(hit.model_copy(update={"rank": rank}) for rank, hit in enumerate(hits, start=1))


def merge(rankings: Sequence[Sequence[Hit]], rrf_k: int = RRF_K) -> tuple[Hit, ...]:
    """Several searches' hits as one ranking, by the same rule (S14).

    Q1 can split a question into several searches; each gets its own ranking. A
    chunk at rank ``r`` in a search earns ``1 / (rrf_k + r)`` there, summed over the
    searches that found it. So each search's first place beats every other search's
    second place, and a chunk several searches found adds up and rises. A chunk
    keeps the legs' ranks and scores of the search that ranked it best. Ties: the
    better rank in any search, then document and position.
    """
    if rrf_k < 0:
        raise ValueError("rrf_k must not be negative")
    scores: dict[str, float] = {}
    best: dict[str, Hit] = {}
    for ranking in rankings:
        for hit in ranking:
            key = hit.chunk.chunk_id
            scores[key] = scores.get(key, 0.0) + 1 / (rrf_k + hit.rank)
            if key not in best or hit.rank < best[key].rank:
                best[key] = hit
    ordered = sorted(
        best.values(),
        key=lambda h: (
            -scores[h.chunk.chunk_id],
            h.rank,
            h.chunk.document_key,
            h.chunk.position,
        ),
    )
    return tuple(
        hit.model_copy(update={"rank": rank, "score": scores[hit.chunk.chunk_id]})
        for rank, hit in enumerate(ordered, start=1)
    )
