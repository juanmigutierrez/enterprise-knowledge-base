"""Reciprocal rank fusion: ranks, not scores, decide; agreement between the legs wins."""

from __future__ import annotations

import pytest

from ekb.access.query.q3_retrieve.fusion import fuse, renumbered
from ekb.stores.search.port import Scored
from tests.unit.stores.search.test_ranking import chunk

A, B, C = (chunk(f"hr_docs:{n}", n) for n in ("A", "B", "C"))


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
