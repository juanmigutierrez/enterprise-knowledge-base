"""``/api/lifecycle`` (S16): the scripted changes, applied one at a time, and their effect.

The panel is tested with a fake source and a fake query path: what it promises is
the order of the story, the three moments (before, right after, now), that the loop
is held while a change is applied, and the one write route's errors.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from ekb.changelog.bus.options.in_memory import InMemoryEventBus
from ekb.changelog.rawstore.options.in_memory import InMemoryRawStore
from ekb.changelog.registry.options.in_memory import InMemoryRegistry
from ekb.contracts import (
    AccessVerdict,
    Answer,
    AnswerResult,
    ChangeOp,
    Chunk,
    Citation,
    Hit,
    Passage,
    QueryPlan,
    Released,
    Scope,
    SourceAccess,
    Trace,
    TraceStatus,
    Withheld,
)
from ekb.operate.inspector.app import create_inspector_app
from ekb.operate.inspector.lifecycle import (
    ActionInfo,
    LifecyclePanel,
    LifecycleRefusedError,
    LifecycleSourceError,
    ProbeInfo,
)
from ekb.operate.inspector.log import ChunkStore, DecisionLog, EventLog, ParsedStore
from ekb.operate.inspector.trace import TraceLog

PAGE = "hr_docs:HR-PL-11"
ACTIONS = [
    ActionInfo("add_page", "Add a page", "dana", "docs tool", "creates it", "webhook", False, "x"),
    ActionInfo(
        "revoke_page",
        "Revoke",
        "dana",
        "docs tool",
        "narrows it",
        "webhook",
        False,
        "y",
        after=("add_page",),
    ),
]
PROBES = [ProbeInfo("alice", "How often?", PAGE)]


class World:
    """A source that holds the page or not, and readers who may read it or not."""

    def __init__(self) -> None:
        self.page = False
        self.alice_reads = True
        self.fail: Exception | None = None
        self.traces = TraceLog()
        self.paused = 0
        self.running = threading.Lock()

    def apply(self, action_id: str) -> None:
        if self.fail is not None:
            raise self.fail
        assert self.running.locked()  # the loop is held while the source changes
        if action_id == "add_page":
            self.page = True
        if action_id == "revoke_page":
            self.alice_reads = False
        self.traces.append(_trace(action_id))

    def ask(self, user: str, question: str) -> AnswerResult:
        scope = Scope(user_id=user, principals=None, reason=f"reads as {user}", prefilter_id="t")
        passages: tuple[Passage, ...] = ()
        withheld: tuple[Withheld, ...] = ()
        if self.page and self.alice_reads:
            passages = (_passage(),)
        elif self.page:
            access = SourceAccess(document_key=PAGE, verdict=AccessVerdict.DENIED, reason="no")
            withheld = (Withheld(hit=_hit(), access=access), Withheld(hit=_hit(), access=access))
        return AnswerResult(
            plan=QueryPlan(question=question, searches=(question,)),
            scope=scope,
            searches=(),
            hits=(),
            released=Released(passages=passages, withheld=withheld),
            answer=Answer(question=question, user_id=user, reason=scope.reason),
        )

    @contextmanager
    def pause(self) -> Iterator[None]:
        self.paused += 1
        with self.running:
            yield

    def panel(self, *, wired: bool = True) -> LifecyclePanel:
        return LifecyclePanel(
            actions=ACTIONS,
            probes=PROBES,
            apply=self.apply,
            ask=self.ask,
            paused=self.pause,
            traces=self.traces,
            linked=lambda key: (_chunk(),) if self.page else (),
            watched=PAGE,
            wired=lambda: wired,
        )


def _chunk() -> Chunk:
    return Chunk(
        document_key=PAGE,
        document_version="1.1",
        chunk_id=f"{PAGE}#0",
        position=0,
        text="Mentoring",
        entity_ids=("person:lucia", "team:T-PPL"),
    )


def _hit() -> Hit:
    return Hit(chunk=_chunk(), rank=1, score=1.0)


def _passage() -> Passage:
    citation = Citation(
        number=1, document_key=PAGE, title="Mentoring", chunk_id=f"{PAGE}#0", document_version="1"
    )
    return Passage(citation=citation, text="Groups meet 2 times a month.")


def _trace(action_id: str) -> Trace:
    return Trace(
        document_key=PAGE,
        source="hr_docs",
        op=ChangeOp.UPSERT,
        version=action_id,
        partition=0,
        offset=1,
        attempt=1,
        action=None,
        status=TraceStatus.PUBLISHED,
        steps=(),
        started_at=datetime.now(UTC),
        ms=1.0,
    )


def _client(panel: LifecyclePanel | None) -> TestClient:
    app = create_inspector_app(
        EventLog(),
        profile="local",
        bus=InMemoryEventBus(),
        registry=InMemoryRegistry(),
        rawstore=InMemoryRawStore(),
        decisions=DecisionLog(),
        parsed=ParsedStore(),
        chunks=ChunkStore(),
        current_lifetime=lambda key: 0,
        traces=panel.traces if panel is not None else None,
        lifecycle=panel,
    )
    return TestClient(app)


@pytest.fixture
def world() -> World:
    return World()


def test_the_screen_lists_the_story_and_what_can_run_now(world: World) -> None:
    view = _client(world.panel()).get("/api/lifecycle").json()

    assert view["wired"] is True
    assert [(a["id"], a["blocked"]) for a in view["actions"]] == [
        ("add_page", ""),
        ("revoke_page", "needs add_page first"),
    ]
    assert view["steps"] == []
    (now,) = view["now"]
    assert (now["user_id"], now["cited"], now["pages"]) == ("alice", False, [])
    assert view["watched"] == PAGE
    assert view["links"] == []


def test_applying_a_change_records_before_and_right_after_with_the_loop_held(
    world: World,
) -> None:
    client = _client(world.panel())

    step = client.post("/api/lifecycle/actions/add_page").json()

    assert world.paused == 1
    assert (step["number"], step["action_id"]) == (1, "add_page")
    assert [p["cited"] for p in step["before"]] == [False]
    assert [p["cited"] for p in step["right_after"]] == [True]
    assert [t["version"] for t in step["traces"]] == ["add_page"]
    view = client.get("/api/lifecycle").json()
    assert [(a["id"], a["applied"]) for a in view["actions"]] == [
        ("add_page", True),
        ("revoke_page", False),
    ]
    assert view["links"] == ["person:lucia", "team:T-PPL"]


def test_a_revocation_shows_right_after_the_change_with_the_page_held_back_once(
    world: World,
) -> None:
    client = _client(world.panel())
    client.post("/api/lifecycle/actions/add_page")

    step = client.post("/api/lifecycle/actions/revoke_page").json()

    (after,) = step["right_after"]
    assert after["cited"] is False
    assert after["held_back"] == [
        {"document_key": PAGE, "verdict": "denied", "reason": "no"}  # each page once
    ]
    view = client.get("/api/lifecycle").json()
    assert [s["action_id"] for s in view["steps"]] == ["revoke_page", "add_page"]  # newest first
    first = next(s for s in view["steps"] if s["action_id"] == "add_page")
    assert [t["version"] for t in first["traces"]] == ["add_page"]  # until the next change


def test_the_write_route_refuses_what_the_story_does_not_allow(world: World) -> None:
    client = _client(world.panel())

    assert client.post("/api/lifecycle/actions/drop_tables").status_code == 404
    refused = client.post("/api/lifecycle/actions/revoke_page")
    assert refused.status_code == 409
    assert "needs add_page first" in refused.json()["detail"]
    client.post("/api/lifecycle/actions/add_page")
    assert client.post("/api/lifecycle/actions/add_page").status_code == 409

    world.fail = LifecycleSourceError("the docs tool did not answer")
    failed = client.post("/api/lifecycle/actions/revoke_page")
    assert failed.status_code == 502
    world.fail = LifecycleRefusedError("revoke_page was already applied")
    assert client.post("/api/lifecycle/actions/revoke_page").status_code == 409
    assert [s["action_id"] for s in client.get("/api/lifecycle").json()["steps"]] == ["add_page"]


def test_nothing_runs_before_the_sources_are_up_or_without_the_panel(world: World) -> None:
    waiting = _client(world.panel(wired=False))
    assert waiting.get("/api/lifecycle").json()["now"] == []
    assert waiting.post("/api/lifecycle/actions/add_page").status_code == 503
    absent = _client(None)
    assert absent.get("/api/lifecycle").json()["wired"] is False
    assert absent.post("/api/lifecycle/actions/add_page").status_code == 503


def test_observation_failure_records_the_change_and_polling_recovers(world: World) -> None:
    panel = world.panel()
    observed = panel.ask

    def flaky(user: str, question: str) -> AnswerResult:
        if world.page:
            raise RuntimeError("query temporarily unavailable")
        return observed(user, question)

    panel.ask = flaky
    client = _client(panel)
    changed = client.post("/api/lifecycle/actions/add_page")
    assert changed.status_code == 200
    assert changed.json()["right_after"] == []
    assert client.post("/api/lifecycle/actions/add_page").status_code == 409
    panel.ask = observed
    view = client.get("/api/lifecycle").json()
    assert view["now"][0]["cited"]
    assert [step["action_id"] for step in view["steps"]] == ["add_page"]
