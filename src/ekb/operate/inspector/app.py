"""The inspector API (FastAPI): read-only views over the event log and the change log.

==============================================  =====================================
GET /api/health                                 liveness
GET /api/sources                                items and events per source
GET /api/documents[?source=]                    every item's current state
GET /api/events[?since=&source=&document_key=]  events after a cursor (live polling)
GET /api/changelog                              bus partitions and consumer groups (S3)
GET /api/registry[?source=]                     the document registry's records (S3)
GET /api/originals?document_key=                an item's stored originals, metadata only (S3)
==============================================  =====================================

Every route is a GET: a test fails if a write route appears. The factory
refuses any profile but ``local``.
"""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import FastAPI, Query

from ekb.changelog.bus.port import EventBus
from ekb.changelog.rawstore.port import RawStore
from ekb.changelog.registry.port import DocumentRegistry
from ekb.contracts import ChangeOp
from ekb.operate.inspector.log import EventLog
from ekb.operate.inspector.models import (
    ChangeLogView,
    DocumentView,
    EventPage,
    EventView,
    OriginalView,
    RecordView,
    SourceView,
)

LOCAL_PROFILE = "local"
HOST = "127.0.0.1"  # never another interface: the inspector shows everything to anyone
DEFAULT_PORT = 8090


class InspectorNotAllowedError(RuntimeError):
    """The inspector was asked to start outside the local profile."""


def create_inspector_app(
    log: EventLog,
    *,
    profile: str,
    bus: EventBus,
    registry: DocumentRegistry,
    rawstore: RawStore,
) -> FastAPI:
    if profile != LOCAL_PROFILE:
        raise InspectorNotAllowedError(
            f"the inspector runs only in the {LOCAL_PROFILE!r} profile, not {profile!r}: "
            "it shows every document and permission to whoever opens it"
        )
    app = FastAPI(
        title="ekb inspector",
        version="1",
        description="Local, read-only view of the knowledge base (D19).",
    )

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/sources")
    def sources() -> list[SourceView]:
        latest = log.latest()
        views: list[SourceView] = []
        for source in sorted({e.event.source for e in log.entries()}):
            items = [entry for entry, _ in latest.values() if entry.event.source == source]
            entries = log.entries(source=source)
            deleted = sum(1 for e in items if e.event.op is ChangeOp.DELETE)
            views.append(
                SourceView(
                    source=source,
                    items=len(items) - deleted,
                    deleted=deleted,
                    events=len(entries),
                    last_event_at=entries[-1].received_at if entries else None,
                )
            )
        return views

    @app.get("/api/documents")
    def documents(source: str | None = None) -> list[DocumentView]:
        views = [
            DocumentView(
                document_key=key,
                source=entry.event.source,
                source_id=entry.event.source_id,
                deleted=entry.event.op is ChangeOp.DELETE,
                last=EventView.of(entry),
                events=count,
            )
            for key, (entry, count) in log.latest().items()
            if source is None or entry.event.source == source
        ]
        return sorted(views, key=lambda v: v.document_key)

    @app.get("/api/events")
    def events(
        since: Annotated[int, Query(ge=0)] = 0,
        source: str | None = None,
        document_key: str | None = None,
    ) -> EventPage:
        cursor = log.cursor
        entries = log.entries(since=since, source=source, document_key=document_key)
        return EventPage(
            events=[EventView.of(e) for e in entries if e.seq <= cursor], cursor=cursor
        )

    @app.get("/api/changelog")
    def changelog() -> ChangeLogView:
        return ChangeLogView.of(bus.stats())

    @app.get("/api/registry")
    def registry_records(source: str | None = None) -> list[RecordView]:
        return [
            RecordView.of(record, originals=len(rawstore.versions(record.document_key)))
            for record in registry.records(source)
        ]

    @app.get("/api/originals")
    def originals(document_key: str) -> list[OriginalView]:
        return [OriginalView.of(o) for o in rawstore.versions(document_key)]

    return app


def openapi_json(app: FastAPI) -> str:
    """The schema the UI's types are generated from, stable byte for byte."""
    return json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"
