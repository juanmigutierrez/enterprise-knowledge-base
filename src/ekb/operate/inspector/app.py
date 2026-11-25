"""The inspector API (FastAPI): read-only views over the event log and the change log.

==============================================  =====================================
GET /api/health                                 liveness
GET /api/sources                                items and events per source
GET /api/documents[?source=]                    every item's current state
GET /api/events[?since=&source=&document_key=]  events after a cursor (live polling)
GET /api/changelog                              bus partitions and consumer groups (S3)
GET /api/registry[?source=]                     the document registry's records (S3)
GET /api/originals?document_key=                an item's stored originals, metadata only (S3)
GET /api/t2/decisions[?since=&source=&action=   T2's decisions and totals (S4)
                     &document_key=]
GET /api/t3/parsed[?source=]                    parsed documents, and records not parsed (S5)
GET /api/t3/parsed/document?document_key=       one parsed document's blocks and text (S5)
GET /api/t4/chunks[?source=]                    chunked documents, sizes and labels (S6, S8)
GET /api/t4/chunks/document?document_key=       one document's chunks, headers, labels (S6, S8)
GET /api/gateway                                the model gateway: routes, quotas, spend (S7)
GET /api/gateway/calls[?since=&task=]           the cost log's records and totals (S7)
==============================================  =====================================

Every route is a GET: a test fails if a write route appears. The factory
refuses any profile but ``local``.

Deleted content is never returned: T3's and T4's output of an item whose
lifetime a delete closed is hidden at once, even before T2's purge drops it.
The gateway's cost log holds no prompt or reply at all, only hashes and numbers.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query

from ekb.changelog.bus.port import EventBus
from ekb.changelog.rawstore.port import RawStore
from ekb.changelog.registry.port import DocumentRegistry
from ekb.contracts import ChangeOp, DiffAction
from ekb.operate.inspector.gateway import GatewayPanel
from ekb.operate.inspector.log import ChunkStore, DecisionLog, EventLog, ParsedStore
from ekb.operate.inspector.models import (
    CallPage,
    CallTotalsView,
    CallView,
    ChangeLogView,
    ChunkedDocumentView,
    ChunkListView,
    ChunkSummaryView,
    DecisionPage,
    DecisionView,
    DiffCounts,
    DocumentView,
    EventPage,
    EventView,
    GatewayView,
    OriginalView,
    ParsedDocumentView,
    ParsedListView,
    ParsedSummaryView,
    RecordCountView,
    RecordView,
    SourceView,
)
from ekb.platform.gateway.usage import totals

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
    decisions: DecisionLog,
    parsed: ParsedStore,
    chunks: ChunkStore,
    current_lifetime: Callable[[str], int],
    gateway: GatewayPanel | None = None,
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

    @app.get("/api/t2/decisions")
    def t2_decisions(
        since: Annotated[int, Query(ge=0)] = 0,
        source: str | None = None,
        action: DiffAction | None = None,
        document_key: str | None = None,
    ) -> DecisionPage:
        cursor = decisions.cursor
        entries = decisions.entries(
            since=since, source=source, action=action, document_key=document_key
        )
        return DecisionPage(
            decisions=[DecisionView.of(e) for e in entries if e.seq <= cursor],
            cursor=cursor,
            counts=DiffCounts.of(decisions.counts(upto=cursor)),
        )

    @app.get("/api/t3/parsed")
    def t3_parsed(source: str | None = None) -> ParsedListView:
        return ParsedListView(
            documents=[
                ParsedSummaryView.of(e)
                for e in parsed.entries(source)
                if e.lifetime == current_lifetime(e.document.document_key)
            ],
            records=[
                RecordCountView(source=name, items=items)
                for name, items in parsed.records().items()
                if source is None or name == source
            ],
        )

    @app.get("/api/t3/parsed/document", responses={404: {"description": "Not parsed"}})
    def t3_parsed_document(document_key: str) -> ParsedDocumentView:
        entry = parsed.get(document_key)
        if entry is None or entry.lifetime != current_lifetime(document_key):
            raise HTTPException(
                status_code=404,
                detail=f"{document_key} is not parsed: not a document, not processed yet, "
                "or deleted",
            )
        return ParsedDocumentView.of(entry)

    @app.get("/api/t4/chunks")
    def t4_chunks(source: str | None = None) -> ChunkListView:
        documents = [
            ChunkSummaryView.of(e)
            for e in chunks.entries(source)
            if e.lifetime == current_lifetime(e.document_key)
        ]
        return ChunkListView(documents=documents, chunks=sum(d.chunks for d in documents))

    @app.get("/api/t4/chunks/document", responses={404: {"description": "Not chunked"}})
    def t4_chunks_document(document_key: str) -> ChunkedDocumentView:
        entry = chunks.get(document_key)
        if entry is None or entry.lifetime != current_lifetime(document_key):
            raise HTTPException(
                status_code=404,
                detail=f"{document_key} has no chunks: not a document, not processed yet, "
                "or deleted",
            )
        return ChunkedDocumentView.of(entry)

    panel = gateway or GatewayPanel()

    @app.get("/api/gateway")
    def gateway_overview() -> GatewayView:
        return GatewayView.of(panel)

    @app.get("/api/gateway/calls")
    def gateway_calls(since: Annotated[int, Query(ge=0)] = 0, task: str | None = None) -> CallPage:
        records = panel.records()
        calls = [
            CallView.of(seq, record)
            for seq, record in enumerate(records, start=1)
            if seq > since and (task is None or record.task == task)
        ]
        return CallPage(
            calls=calls,
            cursor=len(records),
            totals=[CallTotalsView.of(row) for row in totals(records)],
        )

    return app


def openapi_json(app: FastAPI) -> str:
    """The schema the UI's types are generated from, stable byte for byte."""
    return json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"
