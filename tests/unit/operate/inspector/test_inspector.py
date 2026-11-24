"""The inspector: local only, read only, and a stable contract for the UI."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from ekb.contracts import Acl, ChangeEvent, ChangeOp
from ekb.operate.inspector.app import (
    HOST,
    InspectorNotAllowedError,
    create_inspector_app,
    openapi_json,
)
from ekb.operate.inspector.log import EventLog
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


@pytest.fixture
def log() -> EventLog:
    log = EventLog()
    log.append(_event("HR-PL-04", ChangeOp.UPSERT, "1.1"))
    log.append(_event("HR-PL-99", ChangeOp.UPSERT, "1.1"))
    log.append(_event("HR-PL-04", ChangeOp.UPSERT, "2.1", changed_by="dana"))
    log.append(_event("HR-PL-99", ChangeOp.DELETE, "1.1", changed_by="dana"))
    log.append(
        ChangeEvent(
            source="identity",
            source_id="users/alice",
            op=ChangeOp.UPSERT,
            version="0",
            occurred_at=AT,
            raw_pointer="mem://x",
            person_id="alice",
        )
    )
    return log


@pytest.fixture
def client(log: EventLog) -> TestClient:
    return TestClient(create_inspector_app(log, profile="local"))


def test_the_log_knows_what_each_source_holds(log: EventLog) -> None:
    assert log.known("hr_docs") == {"HR-PL-04": "2.1"}  # HR-PL-99 is deleted
    assert log.known("identity") == {"users/alice": "0"}
    assert log.cursor == 5


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
    log.append(_event("HR-PL-01", ChangeOp.UPSERT, "2.1"))
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
        create_inspector_app(EventLog(), profile=profile)


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
    assert committed == openapi_json(create_inspector_app(EventLog(), profile="local"))
