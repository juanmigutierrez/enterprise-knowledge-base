"""The inspector's Pipeline tab (S12): every attempt the worker made, step by step."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from ekb.changelog.bus.options.in_memory import InMemoryEventBus
from ekb.changelog.rawstore.options.in_memory import InMemoryRawStore
from ekb.changelog.registry.options.in_memory import InMemoryRegistry
from ekb.contracts import ChangeOp, DiffAction, StepOutcome, StepTrace, Trace, TraceStatus
from ekb.operate.inspector.app import create_inspector_app
from ekb.operate.inspector.log import ChunkStore, DecisionLog, EventLog, ParsedStore
from ekb.operate.inspector.trace import TraceLog

AT = datetime(2026, 11, 25, 9, 0, tzinfo=UTC)


def _step(name: str, ms: float = 1.0, *, failed: bool = False) -> StepTrace:
    outcome = StepOutcome.FAILED if failed else StepOutcome.DONE
    return StepTrace(step=name, outcome=outcome, detail=f"{name} did its part", ms=ms)


def _trace(
    key: str,
    status: TraceStatus,
    *steps: StepTrace,
    attempt: int = 1,
    action: DiffAction | None = DiffAction.PROCESS,
    op: ChangeOp = ChangeOp.UPSERT,
    error: str = "",
) -> Trace:
    return Trace(
        document_key=key,
        source=key.partition(":")[0],
        op=op,
        version="2.1",
        partition=0,
        offset=attempt,
        attempt=attempt,
        action=action,
        status=status,
        steps=steps,
        error=error,
        started_at=AT,
        ms=sum(s.ms for s in steps),
    )


def _client(traces: TraceLog | None) -> TestClient:
    return TestClient(
        create_inspector_app(
            EventLog(),
            profile="local",
            bus=InMemoryEventBus(),
            registry=InMemoryRegistry(),
            rawstore=InMemoryRawStore(),
            decisions=DecisionLog(),
            parsed=ParsedStore(),
            chunks=ChunkStore(),
            current_lifetime=lambda key: 0,
            traces=traces,
        )
    )


def _log() -> TraceLog:
    log = TraceLog()
    log.append(
        _trace("hr_docs:HR-PL-04", TraceStatus.PUBLISHED, _step("T2 diff"), _step("T3 parse"))
    )
    log.append(
        _trace(
            "hr_docs:HR-PL-06",
            TraceStatus.FAILED,
            _step("T2 diff"),
            _step("T7 embed", 2.0, failed=True),
            action=None,
            error="GatewayError: connection refused",
        )
    )
    log.append(
        _trace(
            "hr_docs:HR-PL-06",
            TraceStatus.PUBLISHED,
            _step("T2 diff"),
            _step("T7 embed", 3.0),
            attempt=2,
        )
    )
    log.append(_trace("identity:users/alice", TraceStatus.RECORD, _step("T2 diff")))
    log.append(
        _trace(
            "hr_docs:HR-PL-99",
            TraceStatus.PURGED,
            _step("T2 diff"),
            _step("purge search"),
            action=DiffAction.PURGE,
            op=ChangeOp.DELETE,
        )
    )
    return log


def test_the_list_shows_each_items_latest_attempt_and_the_totals() -> None:
    view = _client(_log()).get("/api/pipeline/traces").json()

    rows = {d["document_key"]: d for d in view["documents"]}
    assert list(rows) == [
        "hr_docs:HR-PL-04",
        "hr_docs:HR-PL-06",
        "hr_docs:HR-PL-99",
        "identity:users/alice",
    ]
    assert (rows["hr_docs:HR-PL-06"]["traces"], rows["hr_docs:HR-PL-06"]["last"]["attempt"]) == (
        2,
        2,
    )
    assert rows["hr_docs:HR-PL-06"]["last"]["status"] == "published"
    assert rows["identity:users/alice"]["last"]["source_id"] == "users/alice"
    assert view["statuses"]["published"] == 2
    assert view["statuses"]["parked"] == 0
    assert view["attempts"] == 5
    totals = {s["step"]: s for s in view["steps"]}
    assert totals["T7 embed"] == {"step": "T7 embed", "runs": 2, "failed": 1, "total_ms": 5.0}
    assert totals["T2 diff"]["runs"] == 5


def test_the_list_filters_on_the_latest_attempt() -> None:
    client = _client(_log())
    purged = client.get("/api/pipeline/traces", params={"status": "purged"}).json()
    assert [d["document_key"] for d in purged["documents"]] == ["hr_docs:HR-PL-99"]
    failed = client.get("/api/pipeline/traces", params={"status": "failed"}).json()
    assert failed["documents"] == []  # HR-PL-06's latest attempt succeeded
    identity = client.get("/api/pipeline/traces", params={"source": "identity"}).json()
    assert [d["source"] for d in identity["documents"]] == ["identity"]


def test_one_item_shows_every_attempt_newest_first_with_its_steps() -> None:
    view = (
        _client(_log())
        .get("/api/pipeline/trace", params={"document_key": "hr_docs:HR-PL-06"})
        .json()
    )

    assert [t["attempt"] for t in view["traces"]] == [2, 1]
    failed = view["traces"][1]
    assert (failed["status"], failed["action"], failed["error"]) == (
        "failed",
        None,
        "GatewayError: connection refused",
    )
    assert failed["steps"][1] == {
        "step": "T7 embed",
        "outcome": "failed",
        "detail": "T7 embed did its part",
        "ms": 2.0,
    }


def test_a_purged_items_history_stays() -> None:
    view = (
        _client(_log())
        .get("/api/pipeline/trace", params={"document_key": "hr_docs:HR-PL-99"})
        .json()
    )
    assert view["traces"][0]["status"] == "purged"
    assert view["traces"][0]["op"] == "delete"


def test_an_item_the_worker_never_saw_is_404_and_no_log_is_empty() -> None:
    client = _client(_log())
    missing = client.get("/api/pipeline/trace", params={"document_key": "hr_docs:nope"})
    assert missing.status_code == 404
    empty = _client(None).get("/api/pipeline/traces").json()
    assert (empty["documents"], empty["attempts"], empty["steps"]) == ([], 0, [])
