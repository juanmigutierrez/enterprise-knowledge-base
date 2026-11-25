"""T4's header and T5 behind T3 and T2 (S8): what runs when, and what a purge forgets."""

from __future__ import annotations

import pytest

from ekb.app.changelog import ChangeLog
from ekb.app.chunk import Processed, chunk_after_t3, chunk_step, header_writer
from ekb.app.classify import classifier, classify_step, forgetter
from ekb.app.diff import diff_step
from ekb.app.parse import parse_after_t2, parse_step
from ekb.contracts import Acl, Chunk, DiffAction, ParsedDocument, Sensitivity
from ekb.platform.registry import UnknownOptionError
from ekb.process.t2_diff.step import DiffStep
from ekb.process.t5_classify.port import Classification
from ekb.process.t5_classify.step import ClassifyStep
from tests.unit.changelog.factories import acl_change, delete, upsert
from tests.unit.platform.gateway.helpers import World, make_config, make_world
from tests.unit.process.t3_parse.conftest import page_json

KEY = "hr_docs:HR-PL-04"
BODY = "# Remote work\n\n## The rule\nTwo days a week.\n\n## Equipment\nA laptop.\n"


@pytest.fixture
def world() -> World:
    return make_world(
        make_config(tasks={"context_header": ("fake:two",), "classify": ("fake:two",)})
    )


@pytest.fixture
def changelog() -> ChangeLog:
    changelog = ChangeLog.in_memory()
    changelog.store_original(KEY, "1.1", page_json(BODY))
    return changelog


class Kept:
    def __init__(self) -> None:
        self.processed: list[Processed] = []
        self.dropped: list[str] = []


def _pipeline(
    changelog: ChangeLog, world: World, kept: Kept, t5: ClassifyStep | None = None
) -> tuple[DiffStep, list[int]]:
    t4 = chunk_step(header=header_writer("model", world.gateway))
    t5 = t5 or classify_step("model_plus_rules", world.gateway)
    forget = forgetter(world.gateway, kept.dropped.append)
    forgotten: list[int] = []
    then = chunk_after_t3(t4, kept.processed.append, t5)
    step = diff_step(
        changelog,
        parse_after_t2(parse_step(changelog), then, lambda key: forgotten.append(forget(key))),
    )
    return step, forgotten


def test_headers_and_labels_run_once_per_process_and_a_purge_forgets_the_replies(
    changelog: ChangeLog, world: World
) -> None:
    kept = Kept()
    t2, forgotten = _pipeline(changelog, world, kept)
    first = upsert("HR-PL-04", "1.1")
    actions = [
        t2.handle(event).decision.action
        for event in (
            first,  # process: header + labels
            first,  # skip
            acl_change("HR-PL-04", "1.2", Acl(groups=frozenset({"it-employees"}))),  # fast lane
        )
    ]
    assert actions == [DiffAction.PROCESS, DiffAction.SKIP, DiffAction.ACL_ONLY]
    (result,) = kept.processed  # never for a skip or the fast lane
    assert [c.context_header for c in result.final] == ["two chunk_0", "two chunk_1"]
    assert {c.sensitivity for c in result.final} == {Sensitivity.INTERNAL}
    assert {c.department for c in result.final} == {"people"}  # from the page's own label
    assert result.labels is not None
    assert result.final == result.labels.chunks
    assert world.cache.size() == 2  # the header reply and the labels reply, both tagged

    assert t2.handle(delete("HR-PL-04", "1.2")).decision.action is DiffAction.PURGE
    assert kept.dropped == [KEY]  # the stores drop it first...
    assert forgotten == [2]  # ...then the gateway forgets both cached replies about it
    assert world.cache.size() == 0


def test_processed_final_is_t4_s_chunks_without_t5() -> None:
    assert Processed(parse=None, chunks=None).final == ()  # type: ignore[arg-type]


def test_if_t5_fails_nothing_is_kept_and_t2_records_nothing(
    changelog: ChangeLog, world: World
) -> None:
    class Broken:
        classifier_id = "broken@1"

        def classify(self, document: ParsedDocument, chunks: tuple[Chunk, ...]) -> Classification:
            raise RuntimeError("classifier bug")

    kept = Kept()
    t2, _ = _pipeline(changelog, world, kept, ClassifyStep(Broken()))
    for _ in range(2):  # delivered again: decided as new again, fails again
        with pytest.raises(RuntimeError, match="classifier bug"):
            t2.handle(upsert("HR-PL-04", "1.1"))
        assert changelog.registry.get(KEY) is None
    assert kept.processed == []


def test_without_a_gateway_a_purge_still_drops_the_stores() -> None:
    dropped: list[str] = []
    assert forgetter(None, dropped.append)(KEY) == 0
    assert dropped == [KEY]


def test_the_wiring_names_the_options_and_refuses_a_model_option_without_a_gateway(
    world: World,
) -> None:
    assert header_writer("none").header_id == "none@1"
    assert header_writer("model", world.gateway).header_id == "model@1(header@1)"
    assert classifier().classifier_id == "rules@1"
    assert classifier("model_plus_rules", world.gateway).classifier_id == (
        "model_plus_rules@1(classify@2, rules@1)"
    )
    with pytest.raises(ValueError, match="needs the model gateway"):
        header_writer("model")
    with pytest.raises(ValueError, match="needs the model gateway"):
        classifier("model_plus_rules")
    with pytest.raises(UnknownOptionError):
        header_writer("sonnet")
    with pytest.raises(UnknownOptionError):
        classifier("presidio")  # a later option of card 4.9
