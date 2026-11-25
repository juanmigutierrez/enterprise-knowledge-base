"""The query path wired (S13): Q2 → Q3 from config, over the worker's stores, with T7's model."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from ekb.access.query.q3_retrieve.port import EncoderUnavailableError
from ekb.app.choices import choose, choose_query
from ekb.app.pipeline import Stores
from ekb.app.search import EmbedderEncoder, build_search, encoder_note, query_encoder
from ekb.contracts import Chunk
from ekb.platform.config import load_settings
from ekb.platform.gateway.errors import GatewayError
from ekb.process.t7_embed.port import Vectors
from ekb.stores.entities.options.in_memory import InMemoryEntityStore
from ekb.stores.search.options.in_memory import InMemorySearchStore
from tests.unit.platform.gateway.helpers import make_world
from tests.unit.process.t8_permissions.conftest import loaded_store, membership
from tests.unit.stores.search.test_ranking import PAY, SPACE, chunk


def stores(*chunks: Chunk) -> Stores:
    index = InMemorySearchStore()
    by_document: dict[str, list[Chunk]] = {}
    for c in chunks:
        by_document.setdefault(c.document_key, []).append(c)
    for key, batch in by_document.items():
        index.publish(key, batch)
    return Stores(search=index, permissions=loaded_store(), entities=InMemoryEntityStore())


class Down:
    """T7's embedder with its model unreachable."""

    model_id = SPACE
    dimensions = 3

    def embed(self, texts: Sequence[str], *, document_key: str | None = None) -> Vectors:
        raise GatewayError("embed: ollama is not running")


def test_the_same_question_finds_what_each_person_may_read() -> None:
    search = build_search(
        choose_query(load_settings(profile="local")),
        stores(
            chunk("hr_docs:HR-CMP-01", "salary bands for engineering", acl=PAY),
            chunk("hr_docs:HR-PL-01", "annual leave and salary payment dates"),
        ),
    )
    alice = search.ask("alice", "salary bands")
    bob = search.ask("bob", "salary bands")
    assert [h.chunk.document_key for h in alice.retrieval.hits] == ["hr_docs:HR-PL-01"]
    assert bob.retrieval.hits[0].chunk.document_key == "hr_docs:HR-CMP-01"
    assert alice.scope.prefilter_id == "acl@1"
    assert alice.retrieval.retriever_id == (
        "hybrid_rrf@1(top_k=5, candidates=20, rrf_k=60, rerank=none@1)"
    )
    assert alice.ms >= 0


def test_a_group_change_reaches_the_next_search_with_no_chunk_rewritten() -> None:
    wired = stores(chunk("hr_docs:HR-CMP-01", "salary bands", acl=PAY))
    search = build_search(choose_query(load_settings(profile="ci")), wired)
    before = wired.search.chunks()
    assert search.ask("bob", "salary").retrieval.hits
    wired.permissions.put_membership(membership("bob", "all-staff", "hr-team", version="1"))
    assert search.ask("bob", "salary").retrieval.hits == ()
    assert wired.search.chunks() == before


def test_a_deleted_page_and_a_leaver_find_nothing() -> None:
    """The deletion and leaver gates, through the whole query path."""
    wired = stores(chunk("hr_docs:HR-PL-99", "business travel 2024"))
    search = build_search(choose_query(load_settings(profile="ci")), wired)
    assert search.ask("alice", "travel").retrieval.hits
    wired.search.remove("hr_docs:HR-PL-99")
    assert search.ask("alice", "travel").retrieval.hits == ()
    wired.permissions.put_membership(membership("olivia", "all-staff", active=False))
    wired.search.publish("hr_docs:HR-PL-01", [chunk("hr_docs:HR-PL-01", "travel")])
    left = search.ask("olivia", "travel")
    assert left.retrieval.hits == ()
    assert left.scope.reads_nothing


def test_group_revocation_during_encoding_discards_stale_hits_and_counts() -> None:
    wired = stores(chunk("hr_docs:A", "salary bands", acl=PAY))

    class RevokingEncoder:
        space = SPACE

        def encode(self, question: str) -> Sequence[float]:
            wired.permissions.put_membership(membership("bob", "all-staff", version="1"))
            return (1, 0, 0)

    search = build_search(choose_query(load_settings(profile="ci")), wired, RevokingEncoder())
    result = search.ask("bob", "salary")
    assert result.scope.principals is not None
    assert "hr-compensation" not in result.scope.principals.groups
    assert result.retrieval.hits == ()
    assert (
        result.retrieval.searched
        == result.retrieval.keyword_found
        == result.retrieval.vector_found
        == 0
    )


def test_questions_are_embedded_by_t7s_model_unless_its_vectors_mean_nothing() -> None:
    settings = load_settings(profile="ci")
    offline = choose(settings)
    assert query_encoder(offline) is None  # ci runs fake vectors
    assert "carry no meaning" in encoder_note(offline, None)

    gateway = make_world().gateway
    model = offline.with_options("test", t7_embed="model")
    encoder = query_encoder(model, gateway)
    assert isinstance(encoder, EmbedderEncoder)
    assert encoder.space == "model@1(input@1, fake:vec, 8)"  # what T7 stamps on every chunk
    assert len(encoder.encode("annual leave")) == 8
    assert encoder_note(model, encoder) == (
        "questions embedded by T7's model: model@1(input@1, fake:vec, 8)"
    )


def test_a_model_that_cannot_answer_leaves_the_keyword_leg_and_says_why() -> None:
    encoder = EmbedderEncoder(Down())
    with pytest.raises(EncoderUnavailableError, match="ollama is not running"):
        encoder.encode("annual leave")
    search = build_search(
        choose_query(load_settings(profile="ci")),
        stores(chunk("hr_docs:HR-PL-01", "annual leave")),
        encoder,
    )
    retrieval = search.ask("alice", "annual leave").retrieval
    assert [h.chunk.document_key for h in retrieval.hits] == ["hr_docs:HR-PL-01"]
    assert retrieval.notes == (
        "keyword leg only: the embedding model did not answer (embed: ollama is not running)",
    )
