"""The search store's ranking (S13): BM25 and vectors, both over what the asker may read only.

The pre-filter is the gate here: a chunk the asker may not read is never scored,
never counted in the statistics and never takes one of the ``k`` places.
"""

from __future__ import annotations

import math

import pytest

from ekb.contracts import Acl, Chunk, Principals, Sensitivity
from ekb.process.t7_embed.input import embed_input
from ekb.stores.search.options.in_memory import InMemorySearchStore
from ekb.stores.search.port import Scored
from ekb.stores.search.text import fold, indexed_text, terms
from tests.unit.stores.search.test_in_memory import published, record

EVERYONE = Acl(groups=frozenset({"all-staff"}))
PAY = Acl(groups=frozenset({"hr-compensation"}))
ALICE = Principals(user_id="alice", groups=frozenset({"all-staff", "it-employees"}))
BOB = Principals(user_id="bob", groups=frozenset({"all-staff", "hr-compensation"}))
SPACE = "test@1(input@1, hand, 3)"


def chunk(
    key: str,
    text: str,
    *,
    position: int = 0,
    acl: Acl = EVERYONE,
    vector: tuple[float, ...] = (1.0, 0.0, 0.0),
    space: str = SPACE,
    section: tuple[str, ...] = (),
    header: str = "",
) -> Chunk:
    return Chunk(
        document_key=key,
        document_version="1.1",
        chunk_id=f"{key}#{position}",
        text=text,
        section=section,
        position=position,
        context_header=header,
        sensitivity=Sensitivity.INTERNAL,
        acl=acl,
        acl_version="1.1",
        resolver_id="test@1",
        embedding_model=space,
        vector=vector,
    )


def store_with(*chunks: Chunk) -> InMemorySearchStore:
    store = InMemorySearchStore()
    by_document: dict[str, list[Chunk]] = {}
    for c in chunks:
        by_document.setdefault(c.document_key, []).append(c)
    for key, batch in by_document.items():
        store.publish(key, batch)
    return store


def ids(hits: tuple[Scored, ...]) -> list[str]:
    return [h.chunk.chunk_id for h in hits]


# ------------------------------------------------------------------ text


def test_terms_fold_case_and_accents_and_split_on_anything_but_letters_and_digits() -> None:
    assert terms("Días de VACACIONES") == ["dias", "de", "vacaciones"]
    assert terms("Ley 10/2021, HR-PL-07 (€75)") == ["ley", "10", "2021", "hr", "pl", "07", "75"]
    assert fold("Carla Gómez") == "carla gomez"


def test_the_indexed_text_is_header_then_trail_then_text_as_t7_embeds_it() -> None:
    c = chunk("hr_docs:A", "You may carry 5 days.", section=("Leave", "Carry"), header="Annual")
    assert indexed_text(c) == "Annual\n\nLeave > Carry\n\nYou may carry 5 days."
    assert indexed_text(c) == embed_input(c)
    bare = chunk("hr_docs:B", "Only text.")
    assert indexed_text(bare) == embed_input(bare) == "Only text."


# ------------------------------------------------------------------ keyword leg


def test_bm25_ranks_rarer_and_more_frequent_terms_higher() -> None:
    store = store_with(
        chunk("hr_docs:A", "annual leave: 25 days of annual leave"),
        chunk("hr_docs:B", "leave for parents"),
        chunk("hr_docs:C", "expenses and travel"),
    )
    ranking = store.keyword("annual leave", ALICE, 10)
    assert ids(ranking.hits) == ["hr_docs:A#0", "hr_docs:B#0"]  # C shares no word: not returned
    assert ranking.searched == 3
    assert ranking.hits[0].score > ranking.hits[1].score > 0


def test_bm25_matches_whatever_the_case_or_accents_and_finds_the_header_and_trail() -> None:
    store = store_with(
        chunk("hr_docs:A", "Tienes 23 días.", section=("Vacaciones",)),
        chunk("hr_docs:B", "Nothing here.", header="Teletrabajo en Madrid"),
    )
    assert ids(store.keyword("DIAS", ALICE, 5).hits) == ["hr_docs:A#0"]
    assert ids(store.keyword("vacaciones", ALICE, 5).hits) == ["hr_docs:A#0"]
    assert ids(store.keyword("teletrabajo", ALICE, 5).hits) == ["hr_docs:B#0"]


def test_the_keyword_leg_never_scores_what_the_asker_may_not_read() -> None:
    """The pre-filter gate: the best match is restricted, so alice never gets it, and it
    does not take one of her k places."""
    store = store_with(
        chunk("hr_docs:CMP", "salary bands salary bands salary", acl=PAY),
        chunk("hr_docs:A", "salary is paid monthly"),
        chunk("hr_docs:B", "bands of the salary review"),
    )
    alice = store.keyword("salary bands", ALICE, 1)
    assert ids(alice.hits) == ["hr_docs:B#0"]  # one place, and it is hers
    assert alice.searched == 2
    bob = store.keyword("salary bands", BOB, 1)
    assert ids(bob.hits) == ["hr_docs:CMP#0"]
    assert store.keyword("salary", None, 5).hits == ()  # unknown or inactive: nothing
    assert store.keyword("salary", None, 5).searched == 0


def test_a_page_the_asker_cannot_read_does_not_move_their_scores() -> None:
    """Statistics come from the admitted chunks only: no side channel through the scores."""
    mine = (chunk("hr_docs:A", "remote work two days"), chunk("hr_docs:B", "office days"))
    before = store_with(*mine).keyword("remote work", ALICE, 5)
    secret = chunk("hr_docs:CMP", "remote remote work work work " * 20, acl=PAY)
    after = store_with(*mine, secret).keyword("remote work", ALICE, 5)
    assert [(h.chunk.chunk_id, h.score) for h in after.hits] == [
        (h.chunk.chunk_id, h.score) for h in before.hits
    ]


def test_the_keyword_leg_returns_nothing_for_no_words_or_no_places() -> None:
    store = store_with(chunk("hr_docs:A", "annual leave"))
    assert store.keyword("?!", ALICE, 5).hits == ()
    assert store.keyword("annual", ALICE, 0).hits == ()
    assert store.keyword("annual", ALICE, 5).searched == 1


def test_bm25_follows_the_formula() -> None:
    """One term in one of two chunks: idf = ln(1 + (2 - 1 + 0.5) / (1 + 0.5)) = ln 2."""
    store = store_with(chunk("hr_docs:A", "leave leave"), chunk("hr_docs:B", "other words"))
    (hit,) = store.keyword("leave", ALICE, 5).hits
    k1, b, tf, length, average = 1.2, 0.75, 2, 2, 2.0
    expected = math.log(2) * tf * (k1 + 1) / (tf + k1 * (1 - b + b * length / average))
    assert hit.score == pytest.approx(expected)


# ------------------------------------------------------------------ vector leg


def test_the_vector_leg_ranks_by_cosine_similarity() -> None:
    store = store_with(
        chunk("hr_docs:A", "a", vector=(1.0, 0.0, 0.0)),
        chunk("hr_docs:B", "b", vector=(0.6, 0.8, 0.0)),
        chunk("hr_docs:C", "c", vector=(0.0, 0.0, 1.0)),
    )
    ranking = store.nearest((1.0, 1.0, 0.0), SPACE, ALICE, 2)
    assert ids(ranking.hits) == ["hr_docs:B#0", "hr_docs:A#0"]
    assert ranking.hits[0].score == pytest.approx((0.6 + 0.8) / math.sqrt(2))
    assert ranking.searched == 3


def test_the_vector_leg_compares_only_vectors_of_the_same_model_and_counts_the_rest() -> None:
    store = store_with(
        chunk("hr_docs:A", "a"),
        chunk("hr_docs:B", "b", space="other@1(input@1, x, 3)"),
        chunk("hr_docs:C", "c", vector=(1.0, 0.0)),  # same ID, another size
    )
    ranking = store.nearest((1.0, 0.0, 0.0), SPACE, ALICE, 5)
    assert ids(ranking.hits) == ["hr_docs:A#0"]
    assert (ranking.searched, ranking.skipped) == (3, 2)


def test_the_vector_leg_never_compares_what_the_asker_may_not_read() -> None:
    store = store_with(
        chunk("hr_docs:CMP", "pay", acl=PAY, vector=(1.0, 0.0, 0.0)),
        chunk("hr_docs:A", "a", vector=(0.0, 1.0, 0.0)),
    )
    alice = store.nearest((1.0, 0.0, 0.0), SPACE, ALICE, 1)
    assert ids(alice.hits) == ["hr_docs:A#0"]
    assert ids(store.nearest((1.0, 0.0, 0.0), SPACE, BOB, 1).hits) == ["hr_docs:CMP#0"]
    assert store.nearest((1.0, 0.0, 0.0), SPACE, None, 5).hits == ()
    assert store.nearest((0.0, 0.0, 0.0), SPACE, ALICE, 5).hits == ()  # no direction


# ------------------------------------------------------------------ deletion gate


def test_neither_leg_returns_a_removed_hidden_or_closed_document() -> None:
    """The deletion gate, for ranking: removed, hidden during recovery, or of a lifetime
    a delete has closed."""
    lifetimes = {"hr_docs:A": 0, "hr_docs:B": 0, "hr_docs:C": 0}
    store = InMemorySearchStore(current_lifetime=lifetimes.__getitem__)
    for key in lifetimes:
        store.publish(key, [chunk(key, "annual leave")])
    store.remove("hr_docs:A")
    store.set_visible("hr_docs:B", visible=False)
    lifetimes["hr_docs:C"] = 1  # a delete was accepted; T2 has not purged yet
    assert store.keyword("annual leave", ALICE, 5).hits == ()
    assert store.nearest((1.0, 0.0, 0.0), SPACE, ALICE, 5).hits == ()
    store.set_visible("hr_docs:B", visible=True)
    assert ids(store.keyword("annual leave", ALICE, 5).hits) == ["hr_docs:B#0"]


def test_a_new_version_is_ranked_by_its_own_words_and_new_readers_apply_at_once() -> None:
    store = store_with(chunk("hr_docs:A", "remote work"))
    store.publish("hr_docs:A", [chunk("hr_docs:A", "office only")])
    assert store.keyword("remote", ALICE, 5).hits == ()
    assert ids(store.keyword("office", ALICE, 5).hits) == ["hr_docs:A#0"]
    store.set_readers(record("hr_docs:A", PAY))  # the fast lane
    assert store.keyword("office", ALICE, 5).hits == ()
    assert ids(store.keyword("office", BOB, 5).hits) == ["hr_docs:A#0"]


def test_the_synthetic_pages_rank_for_their_readers_only() -> None:
    store = InMemorySearchStore()
    store.publish("hr_docs:HR-PL-01", published())
    store.publish("hr_docs:HR-CMP-01", published("HR-CMP-01", PAY))
    alice = {h.chunk.document_key for h in store.keyword("salary bands", ALICE, 20).hits}
    bob = {h.chunk.document_key for h in store.keyword("salary bands", BOB, 20).hits}
    assert "hr_docs:HR-CMP-01" not in alice
    assert "hr_docs:HR-CMP-01" in bob
