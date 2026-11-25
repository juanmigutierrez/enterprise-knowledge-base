"""Reciprocal rank fusion: ranks, not scores, decide; agreement between the legs wins."""

from __future__ import annotations

import pytest

from ekb.access.query.q3_retrieve.fusion import fuse, merge, renumbered
from ekb.contracts import Chunk, Hit
from ekb.stores.search.port import Scored
from tests.unit.stores.search.test_ranking import chunk

A, B, C, D = (chunk(f"hr_docs:{n}", n) for n in ("A", "B", "C", "D"))


def test_a_chunk_both_legs_found_beats_one_found_by_a_single_leg_first() -> None:
    hits = fuse([Scored(A, 9.0), Scored(B, 3.0)], [Scored(C, 0.9), Scored(A, 0.8)])
    assert [h.chunk.chunk_id for h in hits] == ["hr_docs:A#0", "hr_docs:C#0", "hr_docs:B#0"]
    first = hits[0]
    assert first.score == pytest.approx(1 / 61 + 1 / 62)
    assert (first.rank, first.keyword_rank, first.vector_rank) == (1, 1, 2)
    assert (first.keyword_score, first.vector_score) == (9.0, 0.8)
    assert [h.legs for h in hits] == [("keyword", "vector"), ("vector",), ("keyword",)]


def test_scores_on_different_scales_do_not_matter_only_ranks() -> None:
    big = fuse([Scored(A, 1000.0)], [Scored(B, 0.01)])
    small = fuse([Scored(A, 0.001)], [Scored(B, 0.99)])
    assert [h.score for h in big] == [h.score for h in small]


def test_ties_break_on_the_better_rank_then_document_and_the_k_constant_counts() -> None:
    hits = fuse([Scored(B, 1.0)], [Scored(A, 1.0)])
    assert [h.chunk.chunk_id for h in hits] == ["hr_docs:A#0", "hr_docs:B#0"]  # same score
    assert fuse([Scored(A, 1.0)], [], rrf_k=0)[0].score == 1.0
    with pytest.raises(ValueError, match="rrf_k"):
        fuse([], [], rrf_k=-1)
    assert fuse([], []) == ()


def test_renumbering_follows_the_new_order() -> None:
    hits = fuse([Scored(A, 2.0), Scored(B, 1.0)], [])
    assert [h.rank for h in renumbered(tuple(reversed(hits)))] == [1, 2]
    assert renumbered(tuple(reversed(hits)))[0].chunk.chunk_id == "hr_docs:B#0"


# ------------------------------------------------------------------ merge (S14)


def _ranked(*chunks: Chunk) -> tuple[Hit, ...]:
    return renumbered([Hit(chunk=c, rank=0, score=0.0, keyword_rank=1) for c in chunks])


def test_merging_searches_adds_up_what_both_found_then_ranks_each_searchs_best() -> None:
    """Q1 split a question in two: B, second in both, rises; each part's best follows."""
    merged = merge([_ranked(A, B), _ranked(C, B)])
    assert [h.chunk.chunk_id for h in merged] == ["hr_docs:B#0", "hr_docs:A#0", "hr_docs:C#0"]
    assert merged[0].score == pytest.approx(2 / 62)  # second in both searches: found twice
    assert merged[1].score == pytest.approx(1 / 61)
    assert [h.rank for h in merged] == [1, 2, 3]


def test_a_merged_chunk_keeps_the_legs_of_the_search_that_ranked_it_best() -> None:
    first = renumbered([Hit(chunk=A, rank=0, score=0.0, keyword_rank=7, vector_rank=None)])
    second = renumbered(
        [Hit(chunk=B, rank=0, score=0.0), Hit(chunk=A, rank=0, score=0.0, vector_rank=2)]
    )
    merged = {h.chunk.chunk_id: h for h in merge([first, second])}
    assert (merged["hr_docs:A#0"].keyword_rank, merged["hr_docs:A#0"].vector_rank) == (7, None)


def test_merging_one_search_keeps_its_order_and_nothing_is_nothing() -> None:
    assert [h.chunk.chunk_id for h in merge([_ranked(B, A)])] == ["hr_docs:B#0", "hr_docs:A#0"]
    assert merge([]) == ()
    assert merge([(), ()]) == ()
    with pytest.raises(ValueError, match="rrf_k"):
        merge([], rrf_k=-1)


def test_each_searchs_first_place_beats_another_searchs_second_place() -> None:
    merged = merge([_ranked(A, B), _ranked(C, D)])
    assert [h.chunk.chunk_id for h in merged][:2] == ["hr_docs:A#0", "hr_docs:C#0"]
