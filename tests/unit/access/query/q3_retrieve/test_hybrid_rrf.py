"""``q3_retrieve: hybrid_rrf``: two legs over what the scope admits, fused, reranked, cut."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from ekb.access.query.q3_retrieve.options.hybrid_rrf import HybridRrfRetriever
from ekb.access.query.q3_retrieve.options.rerank_none import NoReranker
from ekb.access.query.q3_retrieve.port import EncoderUnavailableError
from ekb.contracts import Hit, Principals, Scope
from ekb.stores.search.options.in_memory import InMemorySearchStore
from ekb.stores.search.port import RankingSnapshot
from tests.unit.stores.search.test_ranking import ALICE, BOB, PAY, SPACE, chunk, store_with


def scope(principals: Principals | None, user: str = "alice") -> Scope:
    return Scope(user_id=user, principals=principals, reason="test", prefilter_id="test@1")


class Encoder:
    """Hand-made vectors: each question is given its direction."""

    def __init__(self, vectors: dict[str, tuple[float, ...]], space: str = SPACE) -> None:
        self.vectors, self.asked = vectors, list[str]()
        self._space = space

    @property
    def space(self) -> str:
        return self._space

    def encode(self, question: str) -> Sequence[float]:
        self.asked.append(question)
        if question not in self.vectors:
            raise EncoderUnavailableError("ollama: connection refused")
        return self.vectors[question]


class Reversing:
    reranker_id = "reverse@1"

    def rerank(self, question: str, hits: Sequence[Hit]) -> tuple[Hit, ...]:
        return tuple(reversed(hits))


class Untouchable(InMemorySearchStore):
    """A store that fails the test if anything is ranked."""

    def ranking_snapshot(self, principals: Principals | None) -> RankingSnapshot:
        raise AssertionError("the store was asked")


def corpus() -> InMemorySearchStore:
    return store_with(
        chunk("hr_docs:LEAVE", "annual leave is 25 days", vector=(0.95, 0.05, 0.0)),
        chunk("hr_docs:FERIE", "ferie: 26 giorni", vector=(0.9, 0.1, 0.0)),
        chunk("hr_docs:TRAVEL", "travel and expenses", vector=(0.0, 1.0, 0.0)),
        chunk("hr_docs:PAY", "salary bands and annual leave pay", acl=PAY, vector=(1.0, 0, 0)),
    )


def test_both_legs_are_fused_and_each_hit_says_where_it_came_from() -> None:
    encoder = Encoder({"annual leave": (1.0, 0.0, 0.0)})
    retrieval = HybridRrfRetriever(corpus(), encoder).retrieve("annual leave", scope(ALICE))
    keys = [h.chunk.document_key for h in retrieval.hits]
    assert keys[:2] == ["hr_docs:LEAVE", "hr_docs:FERIE"]  # FERIE: no shared word, found by meaning
    leave, ferie = retrieval.hits[:2]
    assert (leave.keyword_rank, leave.vector_rank) == (1, 1)
    assert (ferie.keyword_rank, ferie.vector_rank) == (None, 2)
    assert (retrieval.searched, retrieval.keyword_found, retrieval.vector_found) == (3, 1, 3)
    assert retrieval.vector_leg
    assert retrieval.notes == ()
    assert "hr_docs:PAY" not in keys  # the pre-filter: alice may not read it


def test_deletion_during_encoding_is_visible_to_both_legs() -> None:
    store = store_with(chunk("hr_docs:A", "salary"))

    class DeletingEncoder(Encoder):
        def encode(self, question: str) -> Sequence[float]:
            store.remove("hr_docs:A")
            return super().encode(question)

    result = HybridRrfRetriever(store, DeletingEncoder({"salary": (1, 0, 0)})).retrieve(
        "salary", scope(ALICE)
    )
    assert result.hits == ()
    assert result.searched == result.keyword_found == result.vector_found == 0


def test_replacement_during_reranking_discards_the_stale_result() -> None:
    store = store_with(chunk("hr_docs:A", "salary"))

    class RestrictingReranker(Reversing):
        def rerank(self, question: str, hits: Sequence[Hit]) -> tuple[Hit, ...]:
            store.publish("hr_docs:A", [chunk("hr_docs:A", "salary", acl=PAY)])
            return super().rerank(question, hits)

    result = HybridRrfRetriever(store, reranker=RestrictingReranker()).retrieve(
        "salary", scope(ALICE)
    )
    assert result.hits == ()
    assert result.searched == result.keyword_found == 0


def test_whoever_may_read_more_gets_it_from_the_same_question() -> None:
    encoder = Encoder({"annual leave pay": (1.0, 0.0, 0.0)})
    q3 = HybridRrfRetriever(corpus(), encoder)
    bob = [h.chunk.document_key for h in q3.retrieve("annual leave pay", scope(BOB, "bob")).hits]
    alice = [h.chunk.document_key for h in q3.retrieve("annual leave pay", scope(ALICE)).hits]
    assert bob[0] == "hr_docs:PAY"
    assert "hr_docs:PAY" not in alice


def test_a_scope_that_reads_nothing_gets_nothing_and_the_store_is_not_asked() -> None:
    encoder = Encoder({"salary": (1.0, 0.0, 0.0)})
    nobody = Scope(user_id="olivia", principals=None, reason="has left", prefilter_id="acl@1")
    retrieval = HybridRrfRetriever(Untouchable(), encoder).retrieve("salary", nobody)
    assert retrieval.hits == ()
    assert retrieval.searched == 0
    assert retrieval.notes == ("nothing was searched: olivia reads nothing",)
    assert encoder.asked == []  # not even embedded


def test_without_an_encoder_or_with_the_model_down_only_the_keyword_leg_runs() -> None:
    q3 = HybridRrfRetriever(corpus())
    alone = q3.retrieve("annual leave", scope(ALICE))
    assert [h.chunk.document_key for h in alone.hits] == ["hr_docs:LEAVE"]
    assert not alone.vector_leg
    assert alone.vector_found == 0
    assert alone.notes == ("keyword leg only: no embedding model for questions",)
    down = HybridRrfRetriever(corpus(), Encoder({})).retrieve("annual leave", scope(ALICE))
    assert [h.chunk.document_key for h in down.hits] == ["hr_docs:LEAVE"]
    assert down.notes == (
        "keyword leg only: the embedding model did not answer (ollama: connection refused)",
    )


def test_chunks_embedded_with_another_model_are_left_to_the_keyword_leg_and_noted() -> None:
    store = store_with(
        chunk("hr_docs:A", "annual leave", vector=(1.0, 0.0, 0.0)),
        chunk("hr_docs:B", "annual leave too", space="old@1(input@1, x, 3)"),
    )
    retrieval = HybridRrfRetriever(store, Encoder({"leave": (1.0, 0.0, 0.0)})).retrieve(
        "leave", scope(ALICE)
    )
    b = next(h for h in retrieval.hits if h.chunk.document_key == "hr_docs:B")
    assert (b.keyword_rank, b.vector_rank) == (2, None)
    assert retrieval.notes == (
        "1 of 2 chunks were embedded with another model: only the keyword leg can find "
        "them until they are embedded again",
    )


def test_the_reranker_sees_every_fused_candidate_before_the_top_k_are_kept() -> None:
    store = store_with(*(chunk(f"hr_docs:{n}", f"leave {'x ' * n}") for n in range(1, 8)))
    plain = HybridRrfRetriever(store, top_k=2).retrieve("leave", scope(ALICE))
    reversed_ = HybridRrfRetriever(store, reranker=Reversing(), top_k=2).retrieve(
        "leave", scope(ALICE)
    )
    assert [h.chunk.document_key for h in plain.hits] == ["hr_docs:1", "hr_docs:2"]
    assert [h.chunk.document_key for h in reversed_.hits] == ["hr_docs:7", "hr_docs:6"]
    assert [h.rank for h in reversed_.hits] == [1, 2]
    same = HybridRrfRetriever(store, reranker=NoReranker(), top_k=2).retrieve("leave", scope(ALICE))
    assert same.hits == plain.hits


def test_each_leg_proposes_at_most_candidates_and_the_answer_keeps_top_k() -> None:
    store = store_with(*(chunk(f"hr_docs:{n}", "leave", vector=(1.0, n, 0.0)) for n in range(9)))
    encoder = Encoder({"leave": (1.0, 0.0, 0.0)})
    retrieval = HybridRrfRetriever(store, encoder, top_k=3, candidates=4).retrieve(
        "leave", scope(ALICE)
    )
    assert len(retrieval.hits) == 3
    assert (retrieval.keyword_found, retrieval.vector_found, retrieval.searched) == (4, 4, 9)


def test_an_empty_question_searches_nothing() -> None:
    retrieval = HybridRrfRetriever(Untouchable()).retrieve("  ", scope(ALICE))
    assert retrieval.hits == ()
    assert retrieval.notes == ("an empty question: nothing to search",)


def test_the_retriever_names_its_settings_and_refuses_impossible_ones() -> None:
    q3 = HybridRrfRetriever(InMemorySearchStore(), reranker=NoReranker())
    assert q3.retriever_id == "hybrid_rrf@1(top_k=5, candidates=20, rrf_k=60, rerank=none@1)"
    assert HybridRrfRetriever(InMemorySearchStore()).retriever_id.endswith("rerank=none)")
    assert q3.encoder is None
    for bad in ({"top_k": 0}, {"candidates": 0}, {"rrf_k": -1}):
        with pytest.raises(ValueError, match="must"):
            HybridRrfRetriever(InMemorySearchStore(), **bad)  # type: ignore[arg-type]
