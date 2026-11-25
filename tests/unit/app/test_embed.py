"""T7 behind T6, T5, T4, T3 and T2 (S10): what is embedded when, and what a purge forgets."""

from __future__ import annotations

import pytest

from ekb.app.changelog import ChangeLog
from ekb.app.chunk import Processed, chunk_after_t3, chunk_step
from ekb.app.classify import classify_step, forgetter
from ekb.app.diff import diff_step
from ekb.app.embed import embed_step, embedder, probe
from ekb.app.entities import entity_store, entity_sync, link_step
from ekb.app.parse import parse_after_t2, parse_step
from ekb.contracts import Acl, ChangeEvent, DiffAction
from ekb.platform.gateway.errors import NoModelAvailableError
from ekb.platform.registry import UnknownOptionError
from ekb.process.t2_diff.step import DiffStep
from tests.unit.changelog.factories import acl_change, delete, upsert
from tests.unit.platform.gateway.helpers import make_world
from tests.unit.process.t3_parse.conftest import page_json

PAGE = "hr_docs:HR-PL-01"
BODY = (
    "# Annual leave\n\n## Carrying leave over\nUp to 5 unused days move to January.\n\n"
    "## Requests\nAsk your manager two weeks ahead.\n"
)


class World:
    """T2 to T7 on an in-memory change log, with the fake embedding model behind the gateway."""

    def __init__(self) -> None:
        self.changelog = ChangeLog.in_memory()
        self.models = make_world()
        self.gateway = self.models.gateway
        self.client = self.models.embedders["fake:vec"]
        self.processed: list[Processed] = []
        self.dropped: list[str] = []
        store = entity_store()
        records = entity_sync(self.changelog, store)
        then = chunk_after_t3(
            chunk_step(),
            self.processed.append,
            classify_step(),
            link_step(store),
            records,
            t7=embed_step("model", self.gateway),
        )
        forget = forgetter(self.gateway, self.dropped.append, records.forget)
        self.t2: DiffStep = diff_step(
            self.changelog, parse_after_t2(parse_step(self.changelog), then, forget)
        )

    def page(self, version: str = "1", body: str = BODY) -> ChangeEvent:
        pointer = self.changelog.store_original(PAGE, version, page_json(body))
        return upsert("HR-PL-01", version, raw_pointer=pointer)

    def handle(self, *events: ChangeEvent) -> list[DiffAction]:
        return [self.t2.handle(event).decision.action for event in events]


def test_every_chunk_leaves_with_a_vector_and_its_model_id_but_unpublishable() -> None:
    world = World()
    world.handle(world.page())

    (processed,) = world.processed
    assert processed.vectors is not None
    assert processed.final == processed.vectors.chunks
    for chunk in processed.final:
        assert chunk.vector is not None
        assert len(chunk.vector) == 8
        assert chunk.embedding_model == "model@1(input@1, fake:vec, 8)"
        assert chunk.sensitivity is not None
        assert not chunk.publishable  # no ACL until T8 (S11)


def test_a_skip_or_the_fast_lane_never_embeds_again() -> None:
    world = World()
    first = world.page()
    revoked = acl_change("HR-PL-01", "2", Acl(groups=frozenset({"it-employees"})))

    assert world.handle(first, first, revoked) == [
        DiffAction.PROCESS,
        DiffAction.SKIP,
        DiffAction.ACL_ONLY,
    ]
    assert world.client.calls == 1
    assert len(world.processed) == 1


def test_an_edit_sends_only_the_chunks_whose_text_changed() -> None:
    world = World()
    world.handle(world.page())
    world.handle(world.page("2", BODY.replace("5 unused days", "7 unused days")))

    edited = world.processed[-1].vectors
    assert edited is not None
    assert (edited.sent, edited.cached) == (1, len(edited.chunks) - 1)
    assert world.client.calls == 2


def test_a_purge_forgets_the_documents_vectors() -> None:
    world = World()
    world.handle(world.page())
    assert world.models.cache.size() > 0

    assert world.handle(delete("HR-PL-01", "2")) == [DiffAction.PURGE]

    assert world.dropped == [PAGE]
    assert world.models.cache.size() == 0  # derived from the deleted text: gone with it


def test_if_the_model_is_down_nothing_is_kept_and_the_event_comes_back() -> None:
    world = World()
    world.client.fail_next("unavailable", "unavailable")

    for _ in range(2):  # delivered again: decided as new again, fails again
        with pytest.raises(NoModelAvailableError):
            world.t2.handle(world.page())
        assert world.changelog.registry.get(PAGE) is None
    assert world.processed == []

    assert world.handle(world.page()) == [DiffAction.PROCESS]  # the model is back
    assert world.processed[0].vectors is not None


def test_the_wiring_names_the_options() -> None:
    assert embedder("fake").model_id == "fake@1(input@1, sha256, 32)"
    assert embedder("fake", dimensions=8).dimensions == 8
    assert embedder("model", make_world().gateway).model_id == "model@1(input@1, fake:vec, 8)"
    with pytest.raises(ValueError, match="needs the model gateway"):
        embedder("model")
    with pytest.raises(UnknownOptionError):
        embedder("bge_m3_ollama")  # the model is chosen in the gateway's config now


def test_the_probe_says_whether_the_model_answers_and_leaves_nothing_cached() -> None:
    world = make_world()
    client = world.embedders["fake:vec"]

    assert probe(world.gateway) is None
    assert world.cache.size() == 0
    client.fail_next("unavailable")
    reason = probe(world.gateway)
    assert reason is not None
    assert "fake:vec: unavailable" in reason
    assert client.calls == 2  # never answered from the cache
