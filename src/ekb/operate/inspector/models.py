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
from typing import Literal

from pydantic import BaseModel, ConfigDict

from ekb.changelog.bus.port import BusStats
from ekb.changelog.rawstore.port import StoredOriginal
from ekb.changelog.registry.port import DocumentRecord
from ekb.contracts import (
    AccessVerdict,
    Acl,
    AnswerResult,
    Block,
    BlockKind,
    ChangeEvent,
    ChangeOp,
    Chunk,
    DiffAction,
    DocumentAcl,
    Entity,
    EntityKind,
    EntityMention,
    Membership,
    Passage,
    PiiKind,
    Sensitivity,
    StepOutcome,
    TraceStatus,
    admits,
    readers_now,
    step_rank,
)
from ekb.operate.inspector.gateway import GatewayPanel
from ekb.operate.inspector.lifecycle import ActionInfo, Applied, LifecyclePanel, Probed
from ekb.operate.inspector.log import ChunkEntry, LoggedDecision, LoggedEvent, ParsedEntry
from ekb.operate.inspector.permissions import PermissionSnapshot
from ekb.operate.inspector.trace import LoggedTrace
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
    linker_note: str
    """What T6 did (S9): how many entities it matched against, any name it skipped."""
    entities: list[str]
    """Every entity ID any of its chunks is linked to, sorted (S9)."""
    embed_note: str
    """What T7 did (S10): which model, how many chunks were sent and how many came
    from the cache. Empty when T7 did not run."""
    embedder_id: str
    """What T7 embeds with now (S10), e.g. ``model@1(input@1, ollama:bge-m3, 1024)``."""
    embedded: int
    """Chunks with a vector (S10)."""

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
            linker_note=entry.linker_note,
            entities=sorted({e for chunk in entry.chunks for e in chunk.entity_ids}),
            embed_note=entry.embed_note,
            embedder_id=entry.embedder_id,
            embedded=sum(1 for chunk in entry.chunks if chunk.vector is not None),
        )


class ChunkListView(_View):
    documents: list[ChunkSummaryView]
    chunks: int
    """Chunks across the listed documents."""


class MentionView(_View):
    """The words in a chunk that name one entity, and the rule that matched them (S9)."""

    entity_id: str
    text: str
    rule: str

    @classmethod
    def of(cls, mention: EntityMention) -> MentionView:
        return cls(entity_id=mention.entity_id, text=mention.text, rule=mention.rule)


VectorState = Literal["none", "current", "stale"]
VECTOR_PREVIEW = 8
"""How many of a vector's numbers the UI shows: enough to see it is just numbers."""


class ChunkView(_View):
    """One chunk, as T4 to T7 left it: header, labels, links, vector; no ACL yet."""

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
    entity_ids: list[str]
    """The people and teams it names (T6, S9), as canonical entity IDs."""
    linker_id: str
    mentions: list[MentionView]
    """The words behind each link."""
    embedding_model: str | None
    """What made the vector (T7, S10): option, input recipe, model and size."""
    dimensions: int | None
    """How many numbers the vector has; ``None`` before T7."""
    vector_preview: list[float]
    """Its first numbers, rounded: the whole vector is not worth reading."""
    vector_state: VectorState
    """``none`` before T7; ``current`` when made by what T7 embeds with now; ``stale``
    when another model, size or input recipe made it (embed it again before searching)."""
    publishable: bool
    """Whether it has readers, a sensitivity and a vector: true once T8 has published it
    (S11). The readers themselves are on the T8 tab, which reads the search store."""

    @classmethod
    def of(
        cls,
        chunk: Chunk,
        why: str = "",
        mentions: tuple[EntityMention, ...] = (),
        embedder_id: str = "",
    ) -> ChunkView:
        vector = chunk.vector
        state: VectorState = (
            "none"
            if vector is None
            else "current"
            if chunk.embedding_model == embedder_id
            else "stale"
        )
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
            entity_ids=list(chunk.entity_ids),
            linker_id=chunk.linker_id,
            mentions=[MentionView.of(m) for m in mentions],
            embedding_model=chunk.embedding_model,
            dimensions=len(vector) if vector is not None else None,
            vector_preview=[round(v, 4) for v in (vector or ())[:VECTOR_PREVIEW]],
            vector_state=state,
            publishable=chunk.publishable,
        )


class ChunkedDocumentView(_View):
    """One document's chunks, in reading order."""

    summary: ChunkSummaryView
    chunks: list[ChunkView]

    @classmethod
    def of(cls, entry: ChunkEntry) -> ChunkedDocumentView:
        n = len(entry.chunks)
        why = list(entry.why[:n]) + [""] * (n - len(entry.why))
        found = list(entry.mentions[:n]) + [()] * (n - len(entry.mentions))
        return cls(
            summary=ChunkSummaryView.of(entry),
            chunks=[
                ChunkView.of(chunk, reason, mentions, entry.embedder_id)
                for chunk, reason, mentions in zip(entry.chunks, why, found, strict=True)
            ],
        )


# ------------------------------------------------------------------ T8 permissions (S11)

PublicationStatus = Literal["published", "readers_only", "waiting"]
"""``published``: its chunks are in the search store. ``readers_only``: its readers
are recorded but nothing is published (first seen as a permissions change, or a
version with no text). ``waiting``: chunked, but T8 has not published it."""


class PublicationView(_View):
    """One document's readers and what of it is searchable."""

    document_key: str
    title: str
    status: PublicationStatus
    chunks: int
    """Chunks of it in the search store."""
    text_version: str | None
    """The version of the published text (``Chunk.document_version``)."""
    acl: AclView | None
    """The readers T8 last copied; ``None`` while waiting."""
    acl_version: str | None
    """The source version the readers came from: newer than the text after a fast lane."""
    changed_at: datetime | None
    """When the source changed the readers."""
    resolver_id: str
    readers: list[str]
    """The active users who could read it now: its readers against today's groups."""
    unknown_groups: list[str]
    """Groups in its readers that no identity user is in."""


class MembershipView(_View):
    """One identity user as the permission store holds them."""

    user_id: str
    active: bool
    """An inactive user (a leaver) reads nothing, whatever their groups."""
    groups: list[str]
    source_version: str
    documents: int
    """Published documents they could read now."""


class PermissionsView(_View):
    resolver_id: str
    documents: list[PublicationView]
    users: list[MembershipView]
    published_chunks: int
    published_documents: int
    active_users: int

    @classmethod
    def of(
        cls, snapshot: PermissionSnapshot, entries: list[ChunkEntry], resolver_id: str
    ) -> PermissionsView:
        by_document: dict[str, list[Chunk]] = {}
        for chunk in snapshot.chunks:
            by_document.setdefault(chunk.document_key, []).append(chunk)
        records = {r.document_key: r for r in snapshot.records}
        titles = {e.document_key: e.title for e in entries}
        known = {group for m in snapshot.memberships for group in m.groups}
        keys = sorted(set(by_document) | set(records) | set(titles))
        documents = [
            _publication(key, by_document.get(key, []), records.get(key), titles, snapshot, known)
            for key in keys
        ]
        published = {key: chunks[0].acl for key, chunks in by_document.items() if chunks}
        return cls(
            resolver_id=resolver_id,
            documents=documents,
            users=[_membership(m, published) for m in snapshot.memberships],
            published_chunks=len(snapshot.chunks),
            published_documents=len(by_document),
            active_users=sum(1 for m in snapshot.memberships if m.active),
        )


def _publication(
    key: str,
    chunks: list[Chunk],
    record: DocumentAcl | None,
    titles: dict[str, str],
    snapshot: PermissionSnapshot,
    known: set[str],
) -> PublicationView:
    acl = chunks[0].acl if chunks else record.acl if record else None
    status: PublicationStatus = (
        "published" if chunks else "readers_only" if record is not None else "waiting"
    )
    return PublicationView(
        document_key=key,
        title=titles.get(key, ""),
        status=status,
        chunks=len(chunks),
        text_version=chunks[0].document_version if chunks else None,
        acl=AclView.of(acl) if acl is not None else None,
        acl_version=chunks[0].acl_version if chunks else record.acl_version if record else None,
        changed_at=record.occurred_at if record is not None else None,
        resolver_id=record.resolver_id if record is not None else "",
        readers=list(readers_now(acl, snapshot.memberships)) if acl is not None else [],
        unknown_groups=sorted(acl.groups - known) if acl is not None else [],
    )


def _membership(membership: Membership, published: dict[str, Acl | None]) -> MembershipView:
    principals = membership.principals
    return MembershipView(
        user_id=membership.user_id,
        active=membership.active,
        groups=sorted(membership.groups),
        source_version=membership.source_version,
        documents=sum(
            1 for acl in published.values() if acl is not None and admits(acl, principals)
        ),
    )


# ------------------------------------------------------------------ T6 entities (S9)


# ------------------------------------------------------------------ Search as user (S13, S14)


class RedactionView(_View):
    """Personal data Q4 removed from a released passage: the kind and how often."""

    kind: PiiKind
    count: int


class SearchHitView(_View):
    """One chunk Q3 kept for this person, where each leg ranked it, and what Q4 decided."""

    rank: int
    score: float
    """The fused (reciprocal rank fusion) score, after merging Q1's searches."""
    chunk_id: str
    document_key: str
    title: str
    section: list[str]
    text: str
    """The chunk's text as indexed (an inspector's view: personal data included)."""
    sensitivity: Sensitivity | None
    readers: AclView | None
    """The chunk's readers: what let this person find it."""
    keyword_rank: int | None
    keyword_score: float | None
    """BM25."""
    vector_rank: int | None
    vector_score: float | None
    """Cosine similarity to the question."""
    verdict: AccessVerdict
    """Q4: what the source said about this person and this document, now (S14)."""
    access_reason: str
    citation: int | None
    """The passage's number in the answer, ``[1]``; ``None`` when Q4 held it back."""
    released_text: str | None
    """The text as released, personal data removed; ``None`` when held back."""
    redactions: list[RedactionView]
    source_changed: bool
    """Released, but the source's text has changed since it was indexed."""


class WithheldView(_View):
    """A page the pre-filter kept out: it shares a word with the question, and this person
    may not read it. Inspector only: Q3 never sees it, and an agent never learns it."""

    document_key: str
    title: str
    chunks: int
    readers: AclView | None


class SearchView(_View):
    """One question asked as one person: Q1's plan, Q2's scope, Q3's hits, Q4's decisions."""

    question: str
    user_id: str
    reads_nothing: bool
    """Unknown or inactive: nothing was searched."""
    reason: str
    """Q2's answer in plain words, e.g. ``reads as alice + all-staff, it-employees``."""
    groups: list[str]
    """The groups they read with now (empty when they read nothing)."""
    searches: list[str]
    """Q1's plan: what was searched (S14)."""
    planner_id: str
    prefilter_id: str
    retriever_id: str
    postcheck_id: str
    encoder: str
    """How questions are embedded, or why they are not."""
    searched: int
    """Chunks this person may read: the only ones ranked."""
    keyword_found: int
    vector_found: int
    vector_leg: bool
    notes: list[str]
    """The answer's notes, as an agent would get them."""
    hits: list[SearchHitView]
    released: int
    """Passages Q4 released, cited."""
    held_back: int
    """Hits Q4 held back: the source did not say yes."""
    withheld: list[WithheldView]
    withheld_chunks: int
    ms: float

    @classmethod
    def of(
        cls,
        result: AnswerResult,
        withheld: tuple[Chunk, ...],
        titles: dict[str, str],
        encoder: str,
    ) -> SearchView:
        scope = result.scope
        retrievals = [search.retrieval for search in result.searches]
        kept_out: dict[str, list[Chunk]] = {}
        for chunk in withheld:
            kept_out.setdefault(chunk.document_key, []).append(chunk)
        passages = {p.citation.chunk_id: p for p in result.released.passages}
        answers = {a.document_key: a for a in result.released.checked}
        hits: list[SearchHitView] = []
        for hit in result.hits:
            chunk = hit.chunk
            passage: Passage | None = passages.get(chunk.chunk_id)
            access = answers.get(chunk.document_key)
            if access is None:  # Q4 did not look at it (source_recheck always does)
                verdict, why = AccessVerdict.UNAVAILABLE, "not checked by Q4"
            else:
                verdict, why = access.verdict, access.reason
            if passage is None and verdict.released:  # never released without a passage
                verdict = AccessVerdict.UNAVAILABLE
            hits.append(
                SearchHitView(
                    rank=hit.rank,
                    score=hit.score,
                    chunk_id=chunk.chunk_id,
                    document_key=chunk.document_key,
                    title=passage.citation.title if passage else titles.get(chunk.document_key, ""),
                    section=list(passage.citation.section if passage else chunk.section),
                    text=chunk.text,
                    sensitivity=chunk.sensitivity,
                    readers=AclView.of(chunk.acl) if chunk.acl is not None else None,
                    keyword_rank=hit.keyword_rank,
                    keyword_score=hit.keyword_score,
                    vector_rank=hit.vector_rank,
                    vector_score=hit.vector_score,
                    verdict=verdict,
                    access_reason=why,
                    citation=passage.citation.number if passage is not None else None,
                    released_text=passage.text if passage is not None else None,
                    redactions=[
                        RedactionView(kind=r.kind, count=r.count)
                        for r in (passage.redactions if passage is not None else ())
                    ],
                    source_changed=passage.citation.source_changed if passage else False,
                )
            )
        return cls(
            question=result.plan.question,
            user_id=scope.user_id,
            reads_nothing=scope.reads_nothing,
            reason=scope.reason,
            groups=sorted(scope.principals.groups) if scope.principals is not None else [],
            searches=list(result.plan.searches),
            planner_id=result.plan.planner_id,
            prefilter_id=scope.prefilter_id,
            retriever_id=retrievals[0].retriever_id if retrievals else "",
            postcheck_id=result.released.postcheck_id,
            encoder=encoder,
            searched=max((r.searched for r in retrievals), default=0),
            keyword_found=sum(r.keyword_found for r in retrievals),
            vector_found=sum(r.vector_found for r in retrievals),
            vector_leg=any(r.vector_leg for r in retrievals),
            notes=list(result.answer.notes),
            hits=hits,
            released=len(result.released.passages),
            held_back=len(result.released.withheld),
            withheld=[
                WithheldView(
                    document_key=key,
                    title=titles.get(key, ""),
                    chunks=len(group),
                    readers=AclView.of(group[0].acl) if group[0].acl is not None else None,
                )
                for key, group in sorted(kept_out.items())
            ],
            withheld_chunks=len(withheld),
            ms=round(result.ms, 3),
        )


class EntityLinkView(_View):
    """One chunk that names an entity, and how."""

    entity_id: str
    document_key: str
    title: str
    chunk_id: str
    text: str
    rule: str


class EntityView(_View):
    """One person or team in the entity store, and the chunks linked to it."""

    entity_id: str
    kind: EntityKind
    name: str
    aliases: list[str]
    team_id: str | None
    source_key: str
    """The HR record that defines it: purging that record removes it."""
    source_version: str
    links: list[EntityLinkView]

    @classmethod
    def of(cls, entity: Entity, links: list[EntityLinkView]) -> EntityView:
        return cls(
            entity_id=entity.entity_id,
            kind=entity.kind,
            name=entity.name,
            aliases=list(entity.aliases),
            team_id=entity.team_id,
            source_key=entity.source_key,
            source_version=entity.source_version,
            links=links,
        )


class EntityListView(_View):
    """The entity store, with every link the live chunks carry."""

    entities: list[EntityView]
    people: int
    teams: int
    linked_chunks: int
    """Chunks linked to at least one entity, of every live document."""
    stale_links: list[EntityLinkView]
    """Links to entities the store no longer holds (a leaver), kept by chunks processed
    before the entity went: S9's limit. Since S16 the worker re-links such pages after
    its next drain, so these last only until then, or while a re-link keeps failing."""

    @classmethod
    def of(cls, entities: tuple[Entity, ...], chunks: list[ChunkEntry]) -> EntityListView:
        links: dict[str, list[EntityLinkView]] = {}
        linked = 0
        for entry in chunks:
            for chunk, mentions in zip(entry.chunks, entry.mentions, strict=False):
                linked += 1 if mentions else 0
                for m in mentions:
                    links.setdefault(m.entity_id, []).append(
                        EntityLinkView(
                            entity_id=m.entity_id,
                            document_key=entry.document_key,
                            title=entry.title,
                            chunk_id=chunk.chunk_id,
                            text=m.text,
                            rule=m.rule,
                        )
                    )
        held = {entity.entity_id for entity in entities}
        return cls(
            entities=[EntityView.of(e, links.get(e.entity_id, [])) for e in entities],
            people=sum(1 for e in entities if e.kind is EntityKind.PERSON),
            teams=sum(1 for e in entities if e.kind is EntityKind.TEAM),
            linked_chunks=linked,
            stale_links=[
                link
                for entity_id, found in sorted(links.items())
                if entity_id not in held
                for link in found
            ],
        )


# ----------------------------------------------------------- model gateway (S7)


class TraceStepView(_View):
    """One step of one attempt (S12): ``T3 parse``, ``T8 publish``, ``purge search`` …"""

    step: str
    outcome: StepOutcome
    detail: str
    ms: float


class TraceView(_View):
    """One attempt at one event, step by step (S12). No content: keys, versions,
    decisions, counts, IDs and readers only."""

    seq: int
    document_key: str
    source: str
    source_id: str
    op: ChangeOp
    version: str
    partition: int | None
    offset: int | None
    """Where the event sits in the bus; ``None`` for a rebuild from the stored original."""
    attempt: int
    action: DiffAction | None
    """T2's decision; ``None`` when T2 did not decide (a failure, a parked event, a rebuild)."""
    status: TraceStatus
    steps: list[TraceStepView]
    error: str
    started_at: datetime
    ms: float

    @classmethod
    def of(cls, entry: LoggedTrace) -> TraceView:
        trace = entry.trace
        return cls(
            seq=entry.seq,
            document_key=trace.document_key,
            source=trace.source,
            source_id=trace.document_key.partition(":")[2],
            op=trace.op,
            version=trace.version,
            partition=trace.partition,
            offset=trace.offset,
            attempt=trace.attempt,
            action=trace.action,
            status=trace.status,
            steps=[
                TraceStepView(step=s.step, outcome=s.outcome, detail=s.detail, ms=s.ms)
                for s in trace.steps
            ],
            error=trace.error,
            started_at=trace.started_at,
            ms=trace.ms,
        )


class TracedDocumentView(_View):
    """One item in the Pipeline tab's list: its latest attempt and how many it has had."""

    document_key: str
    source: str
    traces: int
    last: TraceView


class StepTotalsView(_View):
    """One step over every attempt so far: how often it ran, failed, and its time."""

    step: str
    runs: int
    failed: int
    total_ms: float


class PipelineView(_View):
    """The worker as the inspector saw it (S12): every item's latest attempt, the totals
    per status (of the latest attempts) and per step (of every attempt)."""

    documents: list[TracedDocumentView]
    statuses: dict[TraceStatus, int]
    steps: list[StepTotalsView]
    attempts: int
    """Every attempt traced so far, failed ones included."""

    @classmethod
    def of(
        cls, latest: dict[str, tuple[LoggedTrace, int]], every: list[LoggedTrace]
    ) -> PipelineView:
        statuses = dict.fromkeys(TraceStatus, 0)
        for entry, _ in latest.values():
            statuses[entry.trace.status] += 1
        totals: dict[str, StepTotalsView] = {}
        for entry in every:
            for step in entry.trace.steps:
                before = totals.get(step.step) or StepTotalsView(
                    step=step.step, runs=0, failed=0, total_ms=0.0
                )
                totals[step.step] = StepTotalsView(
                    step=step.step,
                    runs=before.runs + 1,
                    failed=before.failed + (step.outcome is StepOutcome.FAILED),
                    total_ms=round(before.total_ms + step.ms, 3),
                )
        return cls(
            documents=[
                TracedDocumentView(
                    document_key=key,
                    source=entry.trace.source,
                    traces=count,
                    last=TraceView.of(entry),
                )
                for key, (entry, count) in sorted(latest.items())
            ],
            statuses=statuses,
            steps=sorted(totals.values(), key=lambda t: step_rank(t.step)),
            attempts=len(every),
        )


class DocumentTraceView(_View):
    """Every attempt at one item, newest first (S12)."""

    document_key: str
    traces: list[TraceView]


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
    kind: str
    """``chat`` writes text; ``embedding`` returns vectors (S10)."""
    dimensions: int | None
    """An embedding model's vector size; ``None`` for a chat model."""


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
                    kind=spec.kind,
                    dimensions=spec.dimensions,
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
    items: int
    """Texts the record covers: 1 for a chat call; for an embedding call (S10), the
    texts answered from the cache or sent in one request."""

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


# ------------------------------------------------------------- Lifecycle (S16)


class LifecycleHeldView(_View):
    """A page Q4 held back for this person, and the source's reason."""

    document_key: str
    verdict: AccessVerdict
    reason: str


class LifecycleProbeView(_View):
    """One question asked as one person at one moment: is the page it depends on cited?"""

    user_id: str
    question: str
    page: str
    cited: bool
    """The page the question depends on is among the cited passages."""
    pages: list[str]
    """Every page cited, each once, in citation order."""
    held_back: list[LifecycleHeldView]
    """Pages Q4 held back, each once: shown here only, never to an agent."""
    reads_nothing: bool
    reason: str
    """Q2's scope in plain words: who they read as."""

    @classmethod
    def of(cls, probed: Probed) -> LifecycleProbeView:
        result, probe = probed.result, probed.probe
        pages = list(dict.fromkeys(p.citation.document_key for p in result.released.passages))
        held: dict[str, LifecycleHeldView] = {}
        for withheld in result.released.withheld:
            access = withheld.access
            held.setdefault(
                access.document_key,
                LifecycleHeldView(
                    document_key=access.document_key, verdict=access.verdict, reason=access.reason
                ),
            )
        return cls(
            user_id=probe.user,
            question=probe.question,
            page=probe.page,
            cited=probe.page in pages,
            pages=pages,
            held_back=list(held.values()),
            reads_nothing=result.scope.reads_nothing,
            reason=result.scope.reason,
        )


class LifecycleActionView(_View):
    """One scripted change in a source, and whether it can be applied now."""

    id: str
    title: str
    actor: str
    source: str
    change: str
    caught_by: str
    listing: bool
    """Only a full listing sees it: the next round runs the listings."""
    expect: str
    after: list[str]
    applied: bool
    blocked: str
    """Why it can't be applied now (``already applied``, ``needs add_page first``), or ``""``."""

    @classmethod
    def of(cls, action: ActionInfo, panel: LifecyclePanel) -> LifecycleActionView:
        blocked = panel.available(action)
        return cls(
            id=action.id,
            title=action.title,
            actor=action.actor,
            source=action.source,
            change=action.change,
            caught_by=action.caught_by,
            listing=action.listing,
            expect=action.expect,
            after=list(action.after),
            applied=blocked == "already applied",
            blocked=blocked,
        )


class LifecycleTraceView(_View):
    """What the worker did with one event after a change: no content (S12)."""

    seq: int
    document_key: str
    version: str
    action: DiffAction | None
    status: TraceStatus
    steps: list[str]
    """The step names, in order: the route the event took."""
    failed_step: str | None

    @classmethod
    def of(cls, entry: LoggedTrace) -> LifecycleTraceView:
        trace = entry.trace
        return cls(
            seq=entry.seq,
            document_key=trace.document_key,
            version=trace.version,
            action=trace.action,
            status=trace.status,
            steps=[step.step for step in trace.steps],
            failed_step=trace.failed_step,
        )


class LifecycleStepView(_View):
    """One change applied from the screen: the questions before it, right after it (the
    worker had not run), and what the worker did since."""

    number: int
    action_id: str
    title: str
    applied_at: datetime
    before: list[LifecycleProbeView]
    right_after: list[LifecycleProbeView]
    traces: list[LifecycleTraceView]

    @classmethod
    def of(cls, step: Applied, panel: LifecyclePanel) -> LifecycleStepView:
        return cls(
            number=step.number,
            action_id=step.action.id,
            title=step.action.title,
            applied_at=step.applied_at,
            before=[LifecycleProbeView.of(p) for p in step.before],
            right_after=[LifecycleProbeView.of(p) for p in step.right_after],
            traces=[LifecycleTraceView.of(e) for e in panel.traces_of(step)],
        )


class LifecycleView(_View):
    """The story so far: the changes, what each did, and the questions answered now."""

    wired: bool
    """The sources are running and the actions can be applied."""
    actions: list[LifecycleActionView]
    steps: list[LifecycleStepView]
    """Applied changes, newest first."""
    now: list[LifecycleProbeView]
    watched: str
    links: list[str]
    """The entity IDs on the watched page's published chunks now."""
