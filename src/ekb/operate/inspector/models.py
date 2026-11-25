"""What the inspector API returns. This is the contract the UI's TypeScript types
are generated from (``ui/openapi.json`` → ``ui/src/api/schema.d.ts``), so a
change here is a change the UI must follow; CI fails if the generated types
are stale.

They are view models, separate from ``ChangeEvent`` on purpose: the internal
contract can evolve without breaking the UI, and lists are plain sorted lists
(JSON has no sets).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from ekb.changelog.bus.port import BusStats
from ekb.changelog.rawstore.port import StoredOriginal
from ekb.changelog.registry.port import DocumentRecord
from ekb.contracts import (
    Acl,
    Block,
    BlockKind,
    ChangeEvent,
    ChangeOp,
    Chunk,
    DiffAction,
    PiiKind,
    Sensitivity,
)
from ekb.operate.inspector.gateway import GatewayPanel
from ekb.operate.inspector.log import ChunkEntry, LoggedDecision, LoggedEvent, ParsedEntry
from ekb.platform.gateway.config import split_ref
from ekb.platform.gateway.models import CallRecord, Outcome
from ekb.platform.gateway.status import ProviderStatus
from ekb.platform.gateway.usage import QuotaUsage, Totals, all_quota_usage, spent_usd


class _View(BaseModel):
    model_config = ConfigDict(frozen=True)


class AclView(_View):
    users: list[str]
    groups: list[str]

    @classmethod
    def of(cls, acl: Acl) -> AclView:
        return cls(users=sorted(acl.users), groups=sorted(acl.groups))


class EventView(_View):
    """One event as received, with the log's arrival number and its place in the bus."""

    seq: int
    received_at: datetime
    partition: int
    offset: int
    document_key: str
    source: str
    source_id: str
    op: ChangeOp
    version: str
    occurred_at: datetime
    acl: AclView
    person_id: str | None
    changed_by: str | None
    raw_pointer: str | None
    content_hash: str | None
    schema_version: int

    @classmethod
    def of(cls, entry: LoggedEvent) -> EventView:
        event: ChangeEvent = entry.event
        return cls(
            seq=entry.seq,
            received_at=entry.received_at,
            partition=entry.partition,
            offset=entry.offset,
            document_key=event.document_key,
            source=event.source,
            source_id=event.source_id,
            op=event.op,
            version=event.version,
            occurred_at=event.occurred_at,
            acl=AclView.of(event.acl),
            person_id=event.person_id,
            changed_by=event.changed_by,
            raw_pointer=event.raw_pointer,
            content_hash=event.content_hash,
            schema_version=event.schema_version,
        )


class EventPage(_View):
    """Events after a cursor, and the cursor to pass next time (for live polling)."""

    events: list[EventView]
    cursor: int


class DocumentView(_View):
    """An item's current state: its latest event, and how many it has had."""

    document_key: str
    source: str
    source_id: str
    deleted: bool
    last: EventView
    events: int


class SourceView(_View):
    source: str
    items: int  # not deleted
    deleted: int
    events: int
    last_event_at: datetime | None


class GroupView(_View):
    """A consumer group: per partition, the offset of its next pending event."""

    group: str
    committed: list[int]
    lag: int


class ChangeLogView(_View):
    """The event bus: how many events each partition holds, and how far each group has read."""

    partitions: int
    end_offsets: list[int]
    events: int
    groups: list[GroupView]

    @classmethod
    def of(cls, stats: BusStats) -> ChangeLogView:
        return cls(
            partitions=len(stats.end_offsets),
            end_offsets=list(stats.end_offsets),
            events=stats.events,
            groups=[
                GroupView(group=g.group, committed=list(g.committed), lag=g.lag)
                for g in stats.groups
            ],
        )


class RecordView(_View):
    """One registry record: what the knowledge base last processed for an item."""

    document_key: str
    source: str
    source_id: str
    version: str
    deleted: bool
    content_hash: str | None
    acl: AclView
    raw_pointer: str | None
    person_id: str | None
    changed_by: str | None
    updated_at: datetime
    events: int
    originals: int
    """How many versions of the item the raw store holds."""

    @classmethod
    def of(cls, record: DocumentRecord, originals: int) -> RecordView:
        return cls(
            document_key=record.document_key,
            source=record.source,
            source_id=record.source_id,
            version=record.version,
            deleted=record.deleted,
            content_hash=record.content_hash,
            acl=AclView.of(record.acl),
            raw_pointer=record.raw_pointer,
            person_id=record.person_id,
            changed_by=record.changed_by,
            updated_at=record.updated_at,
            events=record.events,
            originals=originals,
        )


class OriginalView(_View):
    """One stored version of an item's original: where it is and how big, not its bytes."""

    pointer: str
    version: str
    size: int
    checksum: str
    stored_at: datetime

    @classmethod
    def of(cls, original: StoredOriginal) -> OriginalView:
        return cls(
            pointer=original.pointer,
            version=original.version,
            size=original.size,
            checksum=original.checksum,
            stored_at=original.stored_at,
        )


class DecisionView(_View):
    """One T2 decision: the event, what it needs, and why (S4)."""

    seq: int
    decided_at: datetime
    document_key: str
    source: str
    source_id: str
    op: ChangeOp
    version: str
    previous_version: str | None
    """The version T2 compared against; ``None`` if the item was never processed."""
    action: DiffAction
    reason: str
    acl: AclView
    changed_by: str | None
    purged: int
    """Originals removed from the raw store (deletes only)."""

    @classmethod
    def of(cls, entry: LoggedDecision) -> DecisionView:
        decision = entry.decision
        event = decision.event
        return cls(
            seq=entry.seq,
            decided_at=entry.decided_at,
            document_key=event.document_key,
            source=event.source,
            source_id=event.source_id,
            op=event.op,
            version=event.version,
            previous_version=decision.previous_version,
            action=decision.action,
            reason=decision.reason,
            acl=AclView.of(event.acl),
            changed_by=event.changed_by,
            purged=entry.purged,
        )


class DiffCounts(_View):
    """How many decisions of each kind T2 has made since the inspector started."""

    process: int
    acl_only: int
    skip: int
    purge: int

    @classmethod
    def of(cls, counts: dict[DiffAction, int]) -> DiffCounts:
        return cls(**{action.value: count for action, count in counts.items()})


class DecisionPage(_View):
    """Decisions after a cursor (filtered), the next cursor, and the totals (unfiltered)."""

    decisions: list[DecisionView]
    cursor: int
    counts: DiffCounts


class ParsedSummaryView(_View):
    """One parsed document, without its blocks: what the list on the T3 screen shows (S5)."""

    document_key: str
    source: str
    source_id: str
    version: str
    title: str
    language: str | None
    media_type: str
    parser_id: str
    blocks: int
    kinds: dict[BlockKind, int]
    """How many blocks of each kind, in the order they first appear."""
    runs: int
    """How many times T3 parsed this item since the inspector started."""
    parsed_at: datetime

    @classmethod
    def of(cls, entry: ParsedEntry) -> ParsedSummaryView:
        doc = entry.document
        kinds: dict[BlockKind, int] = {}
        for block in doc.blocks:
            kinds[block.kind] = kinds.get(block.kind, 0) + 1
        source, _, source_id = doc.document_key.partition(":")
        return cls(
            document_key=doc.document_key,
            source=source,
            source_id=source_id,
            version=doc.version,
            title=doc.title,
            language=doc.language,
            media_type=doc.media_type,
            parser_id=doc.parser_id,
            blocks=len(doc.blocks),
            kinds=kinds,
            runs=entry.runs,
            parsed_at=entry.parsed_at,
        )


class RecordCountView(_View):
    """A source whose items T3 found to be structured records, not documents (D7)."""

    source: str
    items: int


class ParsedListView(_View):
    documents: list[ParsedSummaryView]
    records: list[RecordCountView]


class BlockView(_View):
    """One block of a parsed document, as in the contract (``ekb.contracts.Block``)."""

    kind: BlockKind
    text: str
    section: list[str]
    level: int
    marker: str
    rows: list[list[str]]

    @classmethod
    def of(cls, block: Block) -> BlockView:
        return cls(
            kind=block.kind,
            text=block.text,
            section=list(block.section),
            level=block.level,
            marker=block.marker,
            rows=[list(row) for row in block.rows],
        )


class ParsedDocumentView(_View):
    """One parsed document: its blocks in reading order, and its clean text."""

    summary: ParsedSummaryView
    blocks: list[BlockView]
    text: str

    @classmethod
    def of(cls, entry: ParsedEntry) -> ParsedDocumentView:
        return cls(
            summary=ParsedSummaryView.of(entry),
            blocks=[BlockView.of(b) for b in entry.document.blocks],
            text=entry.document.text,
        )


class ChunkSummaryView(_View):
    """One chunked document, without its chunks: what the list on the T4 screen shows (S6)."""

    document_key: str
    source: str
    source_id: str
    version: str
    title: str
    chunker_id: str
    max_tokens: int
    chunks: int
    tokens: int
    """All its chunks' tokens added up."""
    smallest: int
    largest: int
    over_limit: int
    """Chunks over ``max_tokens``: a single block too big to split, such as a long table."""
    runs: int
    """How many times T4 chunked this item since the inspector started."""
    chunked_at: datetime
    header_note: str
    """What T4's header writer did (S8): who wrote the headers, or why there are none."""
    classifier_note: str
    """What T5 did (S8): where the department came from, which model, or rules only."""
    sensitivity: dict[str, int]
    """Chunks per sensitivity label, e.g. ``{"internal": 3, "confidential": 1}`` (S8)."""

    @classmethod
    def of(cls, entry: ChunkEntry) -> ChunkSummaryView:
        sizes = [chunk.tokens for chunk in entry.chunks] or [0]
        source, _, source_id = entry.document_key.partition(":")
        levels = [chunk.sensitivity for chunk in entry.chunks if chunk.sensitivity is not None]
        return cls(
            document_key=entry.document_key,
            source=source,
            source_id=source_id,
            version=entry.version,
            title=entry.title,
            chunker_id=entry.chunker_id,
            max_tokens=entry.max_tokens,
            chunks=len(entry.chunks),
            tokens=sum(sizes),
            smallest=min(sizes),
            largest=max(sizes),
            over_limit=sum(1 for n in sizes if n > entry.max_tokens),
            runs=entry.runs,
            chunked_at=entry.chunked_at,
            header_note=entry.header_note,
            classifier_note=entry.classifier_note,
            sensitivity={
                level.value: levels.count(level) for level in Sensitivity if level in levels
            },
        )


class ChunkListView(_View):
    documents: list[ChunkSummaryView]
    chunks: int
    """Chunks across the listed documents."""


class ChunkView(_View):
    """One chunk, as T4 and T5 left it: header and labels, no vector or ACL yet."""

    chunk_id: str
    position: int
    section: list[str]
    tokens: int
    text: str
    context_header: str
    header_id: str
    """What wrote the header (S8): ``none@1``, or the option, prompt version and model."""
    department: str | None
    sensitivity: Sensitivity | None
    """``None`` only when T5 has not run on it."""
    pii: list[PiiKind]
    classifier_id: str
    why: str
    """Why T5 gave it its sensitivity, in plain words."""
    publishable: bool
    """Always false after T5: the publication gate opens only once T7 and T8 ran too."""

    @classmethod
    def of(cls, chunk: Chunk, why: str = "") -> ChunkView:
        return cls(
            chunk_id=chunk.chunk_id,
            position=chunk.position,
            section=list(chunk.section),
            tokens=chunk.tokens,
            text=chunk.text,
            context_header=chunk.context_header,
            header_id=chunk.header_id,
            department=chunk.department,
            sensitivity=chunk.sensitivity,
            pii=list(chunk.pii),
            classifier_id=chunk.classifier_id,
            why=why,
            publishable=chunk.publishable,
        )


class ChunkedDocumentView(_View):
    """One document's chunks, in reading order."""

    summary: ChunkSummaryView
    chunks: list[ChunkView]

    @classmethod
    def of(cls, entry: ChunkEntry) -> ChunkedDocumentView:
        why = list(entry.why) + [""] * (len(entry.chunks) - len(entry.why))
        return cls(
            summary=ChunkSummaryView.of(entry),
            chunks=[
                ChunkView.of(chunk, reason)
                for chunk, reason in zip(entry.chunks, why, strict=False)  # why may be longer
            ],
        )


# ----------------------------------------------------------- model gateway (S7)


class GatewayProviderView(_View):
    """A provider some task uses, and whether its key is set (never the key)."""

    provider: str
    key_env: str | None
    ready: bool
    models: list[str]

    @classmethod
    def of(cls, status: ProviderStatus) -> GatewayProviderView:
        return cls(
            provider=status.provider,
            key_env=status.key_env,
            ready=status.ready,
            models=list(status.models),
        )


class GatewayRouteView(_View):
    """A task and its candidates, tried in order."""

    task: str
    candidates: list[str]


class GatewayModelView(_View):
    ref: str
    """``provider:model``, as the configuration and the cost log name it."""
    provider: str
    model: str
    input_usd_per_million: float
    output_usd_per_million: float
    free: bool
    quota: str | None


class GatewayQuotaView(_View):
    """Where one free-tier quota stands now, counted from the cost log."""

    name: str
    per_minute: int | None
    per_day: int | None
    resets: str
    """The time zone whose midnight starts a new day for this quota."""
    used_last_minute: int
    used_today: int
    resets_at: datetime

    @classmethod
    def of(cls, usage: QuotaUsage) -> GatewayQuotaView:
        return cls(
            name=usage.name,
            per_minute=usage.per_minute,
            per_day=usage.per_day,
            resets=usage.resets,
            used_last_minute=usage.used_last_minute,
            used_today=usage.used_today,
            resets_at=usage.resets_at,
        )


class GatewayView(_View):
    """The gateway's configuration and where its quotas and spend stand (S7)."""

    cache: str
    cost_log: str
    max_spend_usd: float
    spent_usd: float | None
    """On paid models, over the cost log's whole life."""
    providers: list[GatewayProviderView]
    routes: list[GatewayRouteView]
    models: list[GatewayModelView]
    quotas: list[GatewayQuotaView]

    @classmethod
    def of(cls, panel: GatewayPanel) -> GatewayView:
        config, records = panel.config, panel.records()
        used = set(config.used_models())
        models = []
        for ref in sorted(used):
            provider, model = split_ref(ref)
            spec = config.models[ref]
            models.append(
                GatewayModelView(
                    ref=ref,
                    provider=provider,
                    model=model,
                    input_usd_per_million=spec.price.input,
                    output_usd_per_million=spec.price.output,
                    free=spec.price.free,
                    quota=spec.quota,
                )
            )
        quotas = {config.models[ref].quota for ref in used}
        return cls(
            cache=config.cache.option,
            cost_log=config.cost_log.option,
            max_spend_usd=panel.max_spend_usd,
            spent_usd=spent_usd(records),
            providers=[GatewayProviderView.of(p) for p in panel.providers],
            routes=[
                GatewayRouteView(task=task, candidates=list(chain))
                for task, chain in sorted(config.tasks.items())
            ],
            models=models,
            quotas=[
                GatewayQuotaView.of(u)
                for u in all_quota_usage(config, records, panel.clock())
                if u.name in quotas
            ],
        )


class CallView(_View):
    """One line of the cost log: a cache hit or one attempt at one model. No text."""

    seq: int
    """Its line in the cost log, 1, 2, 3 ..."""
    at: datetime
    task: str
    candidate: str
    prompt_version: str
    key: str
    outcome: Outcome
    quota: str | None
    input_tokens: int
    output_tokens: int
    cost_usd: float | None
    latency_ms: int
    document_key: str | None
    error: str | None

    @classmethod
    def of(cls, seq: int, record: CallRecord) -> CallView:
        return cls(seq=seq, **record.model_dump())


class CallTotalsView(_View):
    """Calls of one task to one model, over the whole cost log."""

    task: str
    candidate: str
    requests: int
    hits: int
    failures: int
    input_tokens: int
    output_tokens: int
    cost_usd: float | None

    @classmethod
    def of(cls, row: Totals) -> CallTotalsView:
        return cls(**row.model_dump())


class CallPage(_View):
    calls: list[CallView]
    cursor: int
    totals: list[CallTotalsView]
