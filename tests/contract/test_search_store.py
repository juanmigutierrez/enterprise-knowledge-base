"""Every search store option keeps the port's rules (S17): the publication gate, one
version at a time, deleted is gone, the readers filtered in the store, and both
ranking legs over what the asker may read only.

Each test runs on ``in_memory``, on ``opensearch`` over a fake client (every pull
request), and on ``opensearch`` against the node in ``docker compose``
(``-m integration``, after ``make up``). One rule is the in-memory store's alone,
tested in ``tests/unit/stores/search/test_ranking.py``: BM25 statistics from the
asker's readable chunks only (OpenSearch counts the whole index; S17c walkthrough).
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

import pytest

from ekb.contracts import Acl, Chunk, Principals
from ekb.platform.infra import InfrastructureError, new_namespace
from ekb.stores.search.options import opensearch
from ekb.stores.search.options.in_memory import InMemorySearchStore
from ekb.stores.search.options.opensearch import OpenSearchSearchStore
from ekb.stores.search.port import Scored, SearchStore, UnpublishableError
from tests.fakes.opensearch import FakeOpenSearch
from tests.unit.stores.search.test_in_memory import published, record
from tests.unit.stores.search.test_ranking import ALICE, BOB, PAY, SPACE, chunk

Lifetimes = Callable[[str], int] | None


@dataclass
class Stores:
    option: str
    fake: FakeOpenSearch = field(default_factory=FakeOpenSearch)
    namespace: str = field(default_factory=lambda: new_namespace("test"))
    made: list[SearchStore] = field(default_factory=list)

    def new(self, current_lifetime: Lifetimes = None) -> SearchStore:
        store: SearchStore
        if self.option == "in_memory":
            store = InMemorySearchStore(current_lifetime)
        elif self.option == "opensearch_fake":
            store = OpenSearchSearchStore(
                current_lifetime, namespace=self.namespace, client=self.fake
            )
        else:
            store = OpenSearchSearchStore(current_lifetime, namespace=self.namespace)
        self.made.append(store)
        return store

    def reopen(self) -> SearchStore:
        if self.option == "in_memory":
            pytest.skip("an in-memory store does not outlive its process")
        return self.new()

    def with_chunks(self, *chunks: Chunk, current_lifetime: Lifetimes = None) -> SearchStore:
        store = self.new(current_lifetime)
        by_document: dict[str, list[Chunk]] = {}
        for c in chunks:
            by_document.setdefault(c.document_key, []).append(c)
        for key, batch in by_document.items():
            store.publish(key, batch)
        return store


@pytest.fixture(
    params=[
        "in_memory",
        "opensearch_fake",
        pytest.param("opensearch", marks=pytest.mark.integration),
    ]
)
def stores(request: pytest.FixtureRequest) -> Iterator[Stores]:
    made = Stores(str(request.param))
    try:
        yield made
    finally:
        if made.option == "opensearch" and made.made:
            first = made.made[0]
            assert isinstance(first, OpenSearchSearchStore)
            first.destroy()


def ids(hits: tuple[Scored, ...]) -> list[str]:
    return [h.chunk.chunk_id for h in hits]


# ------------------------------------------------------------------ writing


def test_publishing_a_document_stores_its_chunks_in_order(stores: Stores) -> None:
    store = stores.new()
    chunks = published()
    result = store.publish("hr_docs:HR-PL-01", tuple(reversed(chunks)))
    assert (result.published, result.replaced) == (len(chunks), 0)
    assert store.chunks("hr_docs:HR-PL-01") == chunks
    assert store.documents() == ("hr_docs:HR-PL-01",)
    assert stores.reopen().chunks("hr_docs:HR-PL-01") == chunks


@pytest.mark.parametrize(
    ("missing_field", "missing"),
    [("acl", "readers (T8)"), ("sensitivity", "a sensitivity (T5)"), ("vector", "a vector (T7)")],
)
def test_one_unpublishable_chunk_refuses_the_whole_batch_and_changes_nothing(
    stores: Stores, missing_field: str, missing: str
) -> None:
    store = stores.new()
    store.publish("hr_docs:HR-PL-01", published())
    good, *rest = published()
    bad = rest[0].model_copy(update={missing_field: None})
    with pytest.raises(UnpublishableError, match=re.escape(f"not publishable: missing {missing}")):
        store.publish("hr_docs:HR-PL-01", (good, bad))
    assert store.chunks("hr_docs:HR-PL-01") == published()


def test_chunks_of_another_document_two_versions_or_one_id_twice_are_refused(
    stores: Stores,
) -> None:
    store = stores.new()
    chunks = published()
    with pytest.raises(UnpublishableError, match="belongs to hr_docs:HR-PL-01"):
        store.publish("hr_docs:HR-PL-02", chunks)
    mixed = (chunks[0], chunks[1].model_copy(update={"document_version": "2.0"}))
    with pytest.raises(UnpublishableError, match="one version at a time"):
        store.publish("hr_docs:HR-PL-01", mixed)
    with pytest.raises(UnpublishableError, match="different lifetimes"):
        store.publish(
            "hr_docs:HR-PL-01", (chunks[0], chunks[1].model_copy(update={"original_lifetime": 1}))
        )
    with pytest.raises(UnpublishableError, match="the same chunk ID twice"):
        store.publish("hr_docs:HR-PL-01", (chunks[0], chunks[0]))
    assert store.chunks() == ()


def test_a_new_version_replaces_every_chunk_of_the_old_one(stores: Stores) -> None:
    store = stores.new()
    old = published()
    assert len(old) > 1
    store.publish("hr_docs:HR-PL-01", old)
    shorter = tuple(c.model_copy(update={"document_version": "2.0"}) for c in old[:1])
    result = store.publish("hr_docs:HR-PL-01", shorter)
    assert (result.published, result.replaced) == (1, len(old))
    assert store.chunks("hr_docs:HR-PL-01") == shorter
    assert store.publish("hr_docs:HR-PL-01", ()).published == 0  # no chunks: removed
    assert store.chunks() == ()


def test_new_readers_change_only_the_readers_of_every_chunk(stores: Stores) -> None:
    store = stores.new()
    chunks = published()
    store.publish("hr_docs:HR-PL-01", chunks)
    assert store.set_readers(record("hr_docs:HR-PL-01", PAY)) == len(chunks)
    changed = store.chunks("hr_docs:HR-PL-01")
    assert {c.acl for c in changed} == {PAY}
    assert {c.acl_version for c in changed} == {"1.2"}
    assert [(c.text, c.vector) for c in changed] == [(c.text, c.vector) for c in chunks]
    assert store.set_readers(record("hr_docs:HR-PL-77", PAY)) == 0


def test_a_removed_document_is_never_returned(stores: Stores) -> None:
    store = stores.new()
    store.publish("hr_docs:HR-PL-01", published())
    store.publish("hr_docs:HR-PL-99", published("HR-PL-99"))
    assert store.remove("hr_docs:HR-PL-99") == len(published("HR-PL-99"))
    assert store.remove("hr_docs:HR-PL-99") == 0
    assert store.documents() == ("hr_docs:HR-PL-01",)
    assert all(c.document_key != "hr_docs:HR-PL-99" for c in store.readable_by(ALICE))


def test_readable_by_returns_only_what_the_principals_admit(stores: Stores) -> None:
    store = stores.new()
    store.publish("hr_docs:HR-PL-01", published())
    store.publish("hr_docs:HR-CMP-01", published("HR-CMP-01", PAY))
    by_name = Principals(user_id="carla", groups=frozenset())
    store.publish("hr_docs:HR-PL-02", published("HR-PL-02", Acl(users=frozenset({"carla"}))))
    assert {c.document_key for c in store.readable_by(ALICE)} == {"hr_docs:HR-PL-01"}
    assert {c.document_key for c in store.readable_by(BOB)} == {
        "hr_docs:HR-PL-01",
        "hr_docs:HR-CMP-01",
    }
    assert {c.document_key for c in store.readable_by(by_name)} == {"hr_docs:HR-PL-02"}
    assert store.readable_by(None) == ()


# ------------------------------------------------------------------ keyword leg


def test_bm25_ranks_documents_that_share_the_words_and_counts_what_it_searched(
    stores: Stores,
) -> None:
    store = stores.with_chunks(
        chunk("hr_docs:A", "annual leave: 25 days of annual leave"),
        chunk("hr_docs:B", "leave for parents"),
        chunk("hr_docs:C", "expenses and travel"),
    )
    ranking = store.keyword("annual leave", ALICE, 10)
    assert ids(ranking.hits) == ["hr_docs:A#0", "hr_docs:B#0"]
    assert ranking.searched == 3
    assert ranking.hits[0].score > ranking.hits[1].score > 0


def test_bm25_matches_whatever_the_case_or_accents_and_finds_the_header_and_trail(
    stores: Stores,
) -> None:
    store = stores.with_chunks(
        chunk("hr_docs:A", "Tienes 23 días.", section=("Vacaciones",)),
        chunk("hr_docs:B", "Nothing here.", header="Teletrabajo en Madrid"),
        chunk("hr_docs:C", "Policy HR-PL-07 applies."),
    )
    assert ids(store.keyword("DIAS", ALICE, 5).hits) == ["hr_docs:A#0"]
    assert ids(store.keyword("vacaciones", ALICE, 5).hits) == ["hr_docs:A#0"]
    assert ids(store.keyword("teletrabajo", ALICE, 5).hits) == ["hr_docs:B#0"]
    assert ids(store.keyword("pl 07", ALICE, 5).hits) == ["hr_docs:C#0"]


def test_the_keyword_leg_never_returns_what_the_asker_may_not_read(stores: Stores) -> None:
    store = stores.with_chunks(
        chunk("hr_docs:CMP", "salary bands salary bands salary", acl=PAY),
        chunk("hr_docs:A", "salary is paid monthly"),
        chunk("hr_docs:B", "bands of the salary review"),
    )
    alice = store.keyword("salary bands", ALICE, 1)
    assert ids(alice.hits) == ["hr_docs:B#0"]  # one place, and it is hers
    assert alice.searched == 2
    assert ids(store.keyword("salary bands", BOB, 1).hits) == ["hr_docs:CMP#0"]
    assert store.keyword("salary", None, 5).hits == ()
    assert store.keyword("salary", None, 5).searched == 0


def test_the_keyword_leg_returns_nothing_for_no_words_or_no_places(stores: Stores) -> None:
    store = stores.with_chunks(chunk("hr_docs:A", "annual leave"))
    assert store.keyword("?!", ALICE, 5).hits == ()
    assert store.keyword("annual", ALICE, 0).hits == ()
    assert store.keyword("annual", ALICE, 5).searched == 1


# ------------------------------------------------------------------ vector leg


def test_the_vector_leg_ranks_by_cosine_similarity(stores: Stores) -> None:
    store = stores.with_chunks(
        chunk("hr_docs:A", "a", vector=(1.0, 0.0, 0.0)),
        chunk("hr_docs:B", "b", vector=(0.6, 0.8, 0.0)),
        chunk("hr_docs:C", "c", vector=(0.0, 0.0, 1.0)),
    )
    ranking = store.nearest((1.0, 1.0, 0.0), SPACE, ALICE, 2)
    assert ids(ranking.hits) == ["hr_docs:B#0", "hr_docs:A#0"]
    assert ranking.hits[0].score == pytest.approx((0.6 + 0.8) / math.sqrt(2), rel=1e-5)
    assert ranking.hits[1].score == pytest.approx(1 / math.sqrt(2), rel=1e-5)
    assert ranking.searched == 3


def test_the_vector_leg_compares_only_vectors_of_the_same_model_and_counts_the_rest(
    stores: Stores,
) -> None:
    store = stores.with_chunks(
        chunk("hr_docs:A", "a"),
        chunk("hr_docs:B", "b", space="other@1(input@1, x, 3)"),
        chunk("hr_docs:C", "c", vector=(1.0, 0.0)),  # same model ID, another size
    )
    ranking = store.nearest((1.0, 0.0, 0.0), SPACE, ALICE, 5)
    assert ids(ranking.hits) == ["hr_docs:A#0"]
    assert (ranking.searched, ranking.skipped) == (3, 2)


def test_the_vector_leg_never_compares_what_the_asker_may_not_read(stores: Stores) -> None:
    store = stores.with_chunks(
        chunk("hr_docs:CMP", "pay", acl=PAY, vector=(1.0, 0.0, 0.0)),
        chunk("hr_docs:A", "a", vector=(0.0, 1.0, 0.0)),
    )
    assert ids(store.nearest((1.0, 0.0, 0.0), SPACE, ALICE, 1).hits) == ["hr_docs:A#0"]
    assert ids(store.nearest((1.0, 0.0, 0.0), SPACE, BOB, 1).hits) == ["hr_docs:CMP#0"]
    assert store.nearest((1.0, 0.0, 0.0), SPACE, None, 5).hits == ()
    assert store.nearest((0.0, 0.0, 0.0), SPACE, ALICE, 5).hits == ()  # no direction


# ------------------------------------------------------------------ deletion gate


def test_nothing_returns_a_removed_hidden_or_closed_document(stores: Stores) -> None:
    lifetimes = {"hr_docs:A": 0, "hr_docs:B": 0, "hr_docs:C": 0}
    store = stores.new(current_lifetime=lifetimes.__getitem__)
    for key in lifetimes:
        store.publish(key, [chunk(key, "annual leave")])
    store.remove("hr_docs:A")
    store.set_visible("hr_docs:B", visible=False)
    lifetimes["hr_docs:C"] = 1  # a delete was accepted; T2 has not purged yet
    assert store.keyword("annual leave", ALICE, 5).hits == ()
    assert store.nearest((1.0, 0.0, 0.0), SPACE, ALICE, 5).hits == ()
    assert store.chunks() == ()
    assert store.readable_by(ALICE) == ()
    assert len(store.chunks_for_update("hr_docs:B")) == 1  # hidden, but still its own
    assert store.chunks_for_update("hr_docs:C") == ()  # closed: not even for an update
    store.set_visible("hr_docs:B", visible=True)
    store.set_visible("hr_docs:B", visible=True)  # twice is fine
    assert ids(store.keyword("annual leave", ALICE, 5).hits) == ["hr_docs:B#0"]


def test_a_new_version_is_ranked_by_its_own_words_and_new_readers_apply_at_once(
    stores: Stores,
) -> None:
    store = stores.with_chunks(chunk("hr_docs:A", "remote work"))
    store.publish("hr_docs:A", [chunk("hr_docs:A", "office only")])
    assert store.keyword("remote", ALICE, 5).hits == ()
    assert ids(store.keyword("office", ALICE, 5).hits) == ["hr_docs:A#0"]
    store.set_readers(record("hr_docs:A", PAY))
    assert store.keyword("office", ALICE, 5).hits == ()
    assert ids(store.keyword("office", BOB, 5).hits) == ["hr_docs:A#0"]


def test_a_ranking_snapshot_knows_when_the_readable_corpus_changed(stores: Stores) -> None:
    store = stores.with_chunks(chunk("hr_docs:A", "remote work"))
    snapshot = store.ranking_snapshot(ALICE)
    assert ids(snapshot.keyword("remote", 5).hits) == ["hr_docs:A#0"]
    assert snapshot.is_current()
    store.set_readers(record("hr_docs:A", PAY))
    assert not snapshot.is_current()


def test_the_synthetic_pages_rank_for_their_readers_only(stores: Stores) -> None:
    store = stores.new()
    store.publish("hr_docs:HR-PL-01", published())
    store.publish("hr_docs:HR-CMP-01", published("HR-CMP-01", PAY))
    alice = {h.chunk.document_key for h in store.keyword("salary bands", ALICE, 20).hits}
    bob = {h.chunk.document_key for h in store.keyword("salary bands", BOB, 20).hits}
    assert "hr_docs:HR-CMP-01" not in alice
    assert "hr_docs:HR-CMP-01" in bob


# ------------------------------------------------------------------ OpenSearch only (fake)


def test_only_complete_generations_are_visible_during_publication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeOpenSearch()
    writer = OpenSearchSearchStore(client=fake)
    reader = OpenSearchSearchStore(client=fake)
    key = "hr_docs:HR-PL-01"
    original = published()
    writer.publish(key, original)
    newer = tuple(c.model_copy(update={"document_version": "2.0"}) for c in original[:2])
    bulk = fake.bulk

    def observe_stage(body: list[dict[str, Any]], refresh: bool = False) -> dict[str, Any]:
        result = bulk(body, refresh)
        assert reader.chunks(key) == original
        assert reader.chunks_for_update(key) == original
        return result

    monkeypatch.setattr(fake, "bulk", observe_stage)
    fake.fail_bulk_after = 1
    with pytest.raises(InfrastructureError, match="incomplete generation"):
        writer.publish(key, newer)
    assert reader.chunks(key) == original
    fake.fail_bulk_after = None
    writer.publish(key, newer)
    assert reader.chunks(key) == newer
    assert len(fake.indexes[writer.index]) == len(newer)


def test_reader_restarts_a_paged_scan_across_a_publication(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeOpenSearch()
    reader = OpenSearchSearchStore(client=fake)
    writer = OpenSearchSearchStore(client=fake)
    key = "hr_docs:HR-PL-01"
    original = published()
    writer.publish(key, original)
    newer = tuple(c.model_copy(update={"document_version": "2.0"}) for c in original[:2])
    monkeypatch.setattr(opensearch, "PAGE", 1)
    search = reader._search
    switched = False

    def switch_after_first_page(index: str, body: dict[str, Any]) -> list[dict[str, Any]]:
        nonlocal switched
        hits = search(index, body)
        if not switched and index == reader.index:
            switched = True
            writer.publish(key, newer)
        return hits

    monkeypatch.setattr(reader, "_search", switch_after_first_page)
    assert reader.chunks(key) == newer
    assert switched


def test_legacy_chunks_stay_readable_until_replaced() -> None:
    fake = FakeOpenSearch()
    store = OpenSearchSearchStore(client=fake)
    key = "hr_docs:HR-PL-01"
    old = published()
    fake.indices.create(store.index, opensearch.CHUNKS_INDEX)
    properties = fake.mappings[store.index]["properties"]
    del properties["generation"]
    for size in {len(c.vector) for c in old if c.vector}:
        properties[opensearch.vector_field(size)] = opensearch.vector_mapping(size)
    for c in old:
        fake.index(store.index, c.chunk_id, opensearch.document(c))
    assert store.chunks(key) == old
    assert store.publish(key, old[:1]).replaced == len(old)
    assert store.chunks(key) == old[:1]


def test_opensearch_that_cannot_be_reached_is_an_infrastructure_error() -> None:
    fake = FakeOpenSearch()
    store = OpenSearchSearchStore(client=fake)
    store.publish("hr_docs:HR-PL-01", published())
    fake.down = True
    with pytest.raises(ConnectionError):  # the sync loops retry it
        store.readable_by(ALICE)
    with pytest.raises(InfrastructureError):
        store.remove("hr_docs:HR-PL-01")


def test_one_vector_field_per_size_and_a_zero_vector_is_kept_but_not_indexed() -> None:
    fake = FakeOpenSearch()
    store = OpenSearchSearchStore(client=fake, namespace="t-fields")
    store.publish("hr_docs:A", [chunk("hr_docs:A", "a", vector=(0.0, 0.0, 0.0))])
    store.publish("hr_docs:B", [chunk("hr_docs:B", "b", vector=(1.0, 0.0))])
    fields = fake.mappings["t-fields-ekb-chunks"]["properties"]
    assert {name for name in fields if name.startswith("vector_")} == {"vector_2", "vector_3"}
    assert all("vector_3" not in row for row in fake.indexes["t-fields-ekb-chunks"].values())
    assert store.chunks("hr_docs:A")[0].vector == (0.0, 0.0, 0.0)
    again = OpenSearchSearchStore(client=fake, namespace="t-fields")  # another process
    again.publish("hr_docs:C", [chunk("hr_docs:C", "c", vector=(1.0, 0.0))])
    assert again.documents() == ("hr_docs:A", "hr_docs:B", "hr_docs:C")


def test_replacement_removes_chunks_beyond_the_result_window() -> None:
    store = OpenSearchSearchStore(client=FakeOpenSearch())
    key = "hr_docs:long"
    first = chunk(key, "policy")
    store.publish(
        key,
        [first.model_copy(update={"position": i, "chunk_id": f"{key}#{i}"}) for i in range(10_001)],
    )
    store.publish(key, [first])
    assert store.chunks(key) == (first,)


def test_hidden_and_closed_documents_beyond_the_result_window_stay_unreadable() -> None:
    fake = FakeOpenSearch()
    closed = "zz-closed"
    hidden = "zz-hidden"
    store = OpenSearchSearchStore(lambda key: int(key == closed), client=fake)
    for i in range(10_000):
        store.set_visible(f"hidden-{i}", visible=False)
    store.set_visible(hidden, visible=False)
    for key in (closed, hidden):
        store.publish(key, [chunk(key, "policy")])
    assert store.readable_by(ALICE) == ()
