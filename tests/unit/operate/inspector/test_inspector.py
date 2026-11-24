"""The inspector: local only, read only, and a stable contract for the UI."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from ekb.changelog.bus.consume import drain
from ekb.changelog.bus.options.in_memory import InMemoryEventBus
from ekb.changelog.rawstore.options.in_memory import InMemoryRawStore
from ekb.changelog.registry.options.in_memory import InMemoryRegistry
from ekb.changelog.registry.port import record_event
from ekb.contracts import Acl, ChangeEvent, ChangeOp
from ekb.operate.inspector.app import (
    HOST,
    InspectorNotAllowedError,
    create_inspector_app,
    openapi_json,
)
from ekb.operate.inspector.log import DecisionLog, EventLog
from ekb.process.t2_diff.options.content_hash import ContentHashDiffer
from ekb.process.t2_diff.step import DiffResult, DiffStep
from tests.conftest import REPO_ROOT

AT = datetime(2026, 11, 24, 9, 0, tzinfo=UTC)


def _event(source_id: str, op: ChangeOp, version: str, **extra: object) -> ChangeEvent:
    fields: dict[str, object] = {
        "source": "hr_docs",
        "source_id": source_id,
        "op": op,
        "version": version,
        "occurred_at": AT,
    }
    if op is ChangeOp.UPSERT:
        fields |= {"raw_pointer": f"mem://raw/hr_docs/{source_id}/{version}", "content_hash": "h"}
        fields |= {"acl": Acl(groups=frozenset({"all-staff"}))}
    return ChangeEvent.model_validate(fields | extra)


EVENTS = [
    _event("HR-PL-04", ChangeOp.UPSERT, "1.1"),
    _event("HR-PL-99", ChangeOp.UPSERT, "1.1"),
    _event("HR-PL-04", ChangeOp.UPSERT, "2.1", changed_by="dana"),
    _event("HR-PL-99", ChangeOp.DELETE, "1.1", changed_by="dana"),
    ChangeEvent(
        source="identity",
        source_id="users/alice",
        op=ChangeOp.UPSERT,
        version="0",
        occurred_at=AT,
        raw_pointer="mem://x",
        person_id="alice",
    ),
]


@pytest.fixture
def bus() -> InMemoryEventBus:
    bus = InMemoryEventBus(partitions=3)
    for event in EVENTS:
        bus.publish(event)
    return bus


@pytest.fixture
def registry(bus: InMemoryEventBus) -> InMemoryRegistry:
    registry = InMemoryRegistry()
    drain(bus, "registry", lambda d: record_event(registry, d.event))
    return registry


@pytest.fixture
def rawstore() -> InMemoryRawStore:
    store = InMemoryRawStore(clock=lambda: AT)
    store.put("hr_docs:HR-PL-04", "1.1", b"first")
    store.put("hr_docs:HR-PL-04", "2.1", b"second")
    return store


@pytest.fixture
def log(bus: InMemoryEventBus) -> EventLog:
    log = EventLog()
    drain(bus, "inspector", log.append)
    return log


@pytest.fixture
def decisions(bus: InMemoryEventBus) -> DecisionLog:
    """T2's decisions on EVENTS, plus a repeat of the last edit, as the inspector keeps them."""
    decisions = DecisionLog()

    def keep(result: DiffResult) -> None:
        decisions.append(result.decision, result.purged)

    step = DiffStep(ContentHashDiffer(), InMemoryRegistry(), InMemoryRawStore(), keep)
    for event in [*EVENTS, EVENTS[2]]:
        step.handle(event)
    return decisions


def _app(
    log: EventLog,
    profile: str = "local",
    bus: InMemoryEventBus | None = None,
    registry: InMemoryRegistry | None = None,
    rawstore: InMemoryRawStore | None = None,
    decisions: DecisionLog | None = None,
) -> FastAPI:
    return create_inspector_app(
        log,
        profile=profile,
        bus=bus or InMemoryEventBus(),
        registry=registry or InMemoryRegistry(),
        rawstore=rawstore or InMemoryRawStore(),
        decisions=decisions or DecisionLog(),
    )


@pytest.fixture
def client(
    log: EventLog,
    bus: InMemoryEventBus,
    registry: InMemoryRegistry,
    rawstore: InMemoryRawStore,
    decisions: DecisionLog,
) -> TestClient:
    return TestClient(_app(log, bus=bus, registry=registry, rawstore=rawstore, decisions=decisions))


def test_the_log_is_the_inspector_groups_copy_of_the_bus(
    log: EventLog, bus: InMemoryEventBus
) -> None:
    assert log.cursor == 5
    assert [e.event for e in log.entries()] == EVENTS  # one partition order kept per item
    first = log.entries()[0]
    assert (first.partition, first.offset) == (0, 0)  # HR-PL-04 lives in partition 0
    assert {g.group: g.lag for g in bus.stats().groups} == {"inspector": 0}


def test_documents_show_each_items_current_state(client: TestClient) -> None:
    documents = client.get("/api/documents").json()
    assert [d["document_key"] for d in documents] == [
        "hr_docs:HR-PL-04",
        "hr_docs:HR-PL-99",
        "identity:users/alice",
    ]
    edited, gone, alice = documents
    assert (edited["last"]["version"], edited["last"]["changed_by"], edited["events"]) == (
        "2.1",
        "dana",
        2,
    )
    assert edited["last"]["acl"] == {"users": [], "groups": ["all-staff"]}
    assert gone["deleted"] is True
    assert alice["last"]["person_id"] == "alice"
    only = client.get("/api/documents", params={"source": "identity"}).json()
    assert [d["document_key"] for d in only] == ["identity:users/alice"]


def test_events_page_after_a_cursor_and_filter(client: TestClient, log: EventLog) -> None:
    page = client.get("/api/events", params={"since": 3}).json()
    assert [e["seq"] for e in page["events"]] == [4, 5]
    assert page["cursor"] == 5
    history = client.get("/api/events", params={"document_key": "hr_docs:HR-PL-04"}).json()
    assert [e["version"] for e in history["events"]] == ["1.1", "2.1"]  # the item's history
    log.append(InMemoryEventBus().publish(_event("HR-PL-01", ChangeOp.UPSERT, "2.1")))
    assert [e["seq"] for e in client.get("/api/events", params={"since": 5}).json()["events"]] == [
        6
    ]


def test_sources_count_items_and_deletes(client: TestClient) -> None:
    sources = {s["source"]: s for s in client.get("/api/sources").json()}
    assert (sources["hr_docs"]["items"], sources["hr_docs"]["deleted"]) == (1, 1)
    assert sources["hr_docs"]["events"] == 4
    assert sources["identity"]["items"] == 1


@pytest.mark.parametrize("profile", ["ci", "bedrock", "production", ""])
def test_it_refuses_every_profile_but_local(profile: str) -> None:
    """D19 gate: the inspector shows everything, so it never runs outside local."""
    with pytest.raises(InspectorNotAllowedError, match="local"):
        _app(EventLog(), profile=profile)


def test_it_is_read_only(client: TestClient) -> None:
    routes = [r for r in client.app.routes if isinstance(r, APIRoute)]  # type: ignore[attr-defined]
    assert routes
    assert {m for r in routes for m in (r.methods or set())} == {"GET"}


def test_it_binds_to_the_loopback_interface_only() -> None:
    assert HOST == "127.0.0.1"


def test_the_ui_contract_is_up_to_date() -> None:
    """ui/openapi.json is what the UI's types are generated from: it must match the API.

    Fix with: uv run ekb inspector --export-openapi ui/openapi.json; cd ui; npm run gen:types
    """
    committed = (REPO_ROOT / "ui" / "openapi.json").read_text(encoding="utf-8")
    assert committed == openapi_json(_app(EventLog()))


def test_events_say_where_they_sit_in_the_bus(client: TestClient) -> None:
    events = client.get("/api/events").json()["events"]
    places = [(e["source_id"], e["partition"], e["offset"]) for e in events]
    (first, second) = [(p, o) for source_id, p, o in places if source_id == "HR-PL-04"]
    assert first[0] == second[0]  # one partition
    assert first[1] < second[1]  # in order


def test_changelog_shows_partitions_and_each_groups_position(client: TestClient) -> None:
    view = client.get("/api/changelog").json()
    assert (view["partitions"], view["events"], sum(view["end_offsets"])) == (3, 5, 5)
    groups = {g["group"]: g for g in view["groups"]}
    assert set(groups) == {"inspector", "registry"}
    assert groups["registry"]["lag"] == 0
    assert sum(groups["registry"]["committed"]) == 5


def test_registry_shows_tombstones_without_content_and_counts_originals(
    client: TestClient,
) -> None:
    records = {r["document_key"]: r for r in client.get("/api/registry").json()}
    assert list(records) == ["hr_docs:HR-PL-04", "hr_docs:HR-PL-99", "identity:users/alice"]
    edited, gone = records["hr_docs:HR-PL-04"], records["hr_docs:HR-PL-99"]
    assert (edited["version"], edited["events"], edited["originals"]) == ("2.1", 2, 2)
    assert edited["acl"] == {"users": [], "groups": ["all-staff"]}
    assert gone["deleted"] is True
    assert (gone["content_hash"], gone["raw_pointer"], gone["person_id"]) == (None, None, None)
    assert gone["acl"] == {"users": [], "groups": []}
    only = client.get("/api/registry", params={"source": "identity"}).json()
    assert [r["document_key"] for r in only] == ["identity:users/alice"]


def test_originals_list_versions_and_never_the_bytes(client: TestClient) -> None:
    versions = client.get("/api/originals", params={"document_key": "hr_docs:HR-PL-04"}).json()
    assert [(v["version"], v["size"]) for v in versions] == [("1.1", 5), ("2.1", 6)]
    assert versions[0]["pointer"] == "mem://ekb-raw/hr_docs/HR-PL-04/1.1"
    assert all("first" not in str(v) and "second" not in str(v) for v in versions)
    assert client.get("/api/originals", params={"document_key": "hr_docs:none"}).json() == []
    assert client.get("/api/originals").status_code == 422  # document_key is required


def test_t2_decisions_show_action_reason_and_totals(client: TestClient) -> None:
    page = client.get("/api/t2/decisions").json()
    assert page["cursor"] == 6
    assert page["counts"] == {"process": 3, "acl_only": 0, "skip": 2, "purge": 1}
    rows = [(d["source_id"], d["version"], d["action"]) for d in page["decisions"]]
    assert rows == [
        ("HR-PL-04", "1.1", "process"),
        ("HR-PL-99", "1.1", "process"),
        ("HR-PL-04", "2.1", "skip"),  # every upsert here has hash "h": same text
        ("HR-PL-99", "1.1", "purge"),
        ("users/alice", "0", "process"),
        ("HR-PL-04", "2.1", "skip"),
    ]
    assert page["decisions"][2]["reason"] == "new version, same content and readers as v1.1"
    repeat = page["decisions"][-1]
    assert (repeat["previous_version"], repeat["reason"]) == (
        "2.1",
        "repeat: v2.1 was already processed",
    )
    purge = page["decisions"][3]
    assert (purge["op"], purge["purged"], purge["acl"]) == (
        "delete",
        0,  # this test's raw store never held HR-PL-99's original
        {"users": [], "groups": []},
    )


def test_t2_decisions_page_after_a_cursor_and_filter(
    client: TestClient, decisions: DecisionLog
) -> None:
    page = client.get("/api/t2/decisions", params={"since": 4}).json()
    assert [d["seq"] for d in page["decisions"]] == [5, 6]
    skipped = client.get("/api/t2/decisions", params={"action": "skip"}).json()
    assert [d["seq"] for d in skipped["decisions"]] == [3, 6]
    assert skipped["counts"]["process"] == 3  # totals are never filtered
    one = client.get("/api/t2/decisions", params={"document_key": "hr_docs:HR-PL-99"}).json()
    assert [d["action"] for d in one["decisions"]] == ["process", "purge"]
    identity = client.get("/api/t2/decisions", params={"source": "identity"}).json()
    assert [d["source_id"] for d in identity["decisions"]] == ["users/alice"]
    assert client.get("/api/t2/decisions", params={"action": "maybe"}).status_code == 422
