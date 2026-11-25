"""The inspector's own copies: every event, and every T2 decision, in arrival order.

Since S3 the inspector is a consumer group (``inspector``) on the event bus,
like any worker: ``ekb inspector`` drains the group into this log after every
sync round. It keeps the events it has read so the UI can page through them
with a cursor and show each item's history, and it records where each event
sits in the bus (partition and offset).

It no longer says what each source holds: the full listings ask the document
registry for that (S3).

Since S4 it also keeps T2's decisions (:class:`DecisionLog`): ``ekb inspector``
hands it every result T2 produces, so the UI can show what was processed,
fast-tracked, skipped or purged, and why.

Since S5 it keeps T3's latest output per document (:class:`ParsedStore`). A
purge drops it: deleted content is never shown, parsed or not.

Since S6 it keeps T4's latest chunks per document (:class:`ChunkStore`), with
the same rule: a purge drops them. Since S9 they carry T6's links, with the
words behind each one.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import UTC, datetime

from ekb.changelog.bus.port import Delivery
from ekb.contracts import (
    ChangeEvent,
    Chunk,
    DiffAction,
    DiffDecision,
    EntityMention,
    ParsedDocument,
)


@dataclass(frozen=True)
class LoggedEvent:
    seq: int  # arrival order in this log, 1, 2, 3 ...
    received_at: datetime
    partition: int
    offset: int
    event: ChangeEvent


class EventLog:
    def __init__(self) -> None:
        self._entries: list[LoggedEvent] = []
        self._lock = threading.Lock()

    def append(self, delivery: Delivery) -> None:
        """A bus handler: ``drain(bus, "inspector", log.append)``."""
        with self._lock:
            self._entries.append(
                LoggedEvent(
                    seq=len(self._entries) + 1,
                    received_at=datetime.now(UTC),
                    partition=delivery.partition,
                    offset=delivery.offset,
                    event=delivery.event,
                )
            )

    @property
    def cursor(self) -> int:
        with self._lock:
            return len(self._entries)

    def entries(
        self,
        *,
        since: int = 0,
        source: str | None = None,
        document_key: str | None = None,
    ) -> list[LoggedEvent]:
        with self._lock:
            entries = self._entries[since:]
        return [
            e
            for e in entries
            if (source is None or e.event.source == source)
            and (document_key is None or e.event.document_key == document_key)
        ]

    def latest(self) -> dict[str, tuple[LoggedEvent, int]]:
        """Per item: its latest event and how many events it has had."""
        latest: dict[str, tuple[LoggedEvent, int]] = {}
        for entry in self.entries():
            key = entry.event.document_key
            count = latest[key][1] + 1 if key in latest else 1
            latest[key] = (entry, count)
        return latest


@dataclass(frozen=True)
class LoggedDecision:
    seq: int  # order in which T2 decided, 1, 2, 3 ...
    decided_at: datetime
    decision: DiffDecision
    purged: int  # originals removed from the raw store (deletes only)


class DecisionLog:
    """T2's decisions, oldest first. ``append`` is T2's ``downstream`` in the inspector."""

    def __init__(self) -> None:
        self._entries: list[LoggedDecision] = []
        self._lock = threading.Lock()

    def append(self, decision: DiffDecision, purged: int = 0) -> None:
        with self._lock:
            self._entries.append(
                LoggedDecision(
                    seq=len(self._entries) + 1,
                    decided_at=datetime.now(UTC),
                    decision=decision,
                    purged=purged,
                )
            )

    @property
    def cursor(self) -> int:
        with self._lock:
            return len(self._entries)

    def entries(
        self,
        *,
        since: int = 0,
        source: str | None = None,
        action: DiffAction | None = None,
        document_key: str | None = None,
    ) -> list[LoggedDecision]:
        with self._lock:
            entries = self._entries[since:]
        return [
            e
            for e in entries
            if (source is None or e.decision.event.source == source)
            and (action is None or e.decision.action is action)
            and (document_key is None or e.decision.event.document_key == document_key)
        ]

    def counts(self, upto: int | None = None) -> dict[DiffAction, int]:
        """How many decisions of each kind, over the first ``upto`` (default: all)."""
        counts = dict.fromkeys(DiffAction, 0)
        for entry in self.entries()[:upto]:
            counts[entry.decision.action] += 1
        return counts


@dataclass(frozen=True)
class ParsedEntry:
    document: ParsedDocument
    lifetime: int
    parsed_at: datetime
    runs: int
    """How many times T3 parsed this item: an edit adds one, a skip or a fast lane doesn't."""


class ParsedStore:
    """T3's latest output per document, and which items were records, not documents.

    ``record`` and ``drop`` are what ``ekb inspector`` calls after T2: ``record``
    for every item T3 ran on, ``drop`` for every purge.
    """

    def __init__(self) -> None:
        self._documents: dict[str, ParsedEntry] = {}
        self._records: dict[str, set[str]] = {}  # source -> keys of items that aren't documents
        self._lock = threading.Lock()

    def record(self, event: ChangeEvent, parsed: ParsedDocument | None) -> None:
        """Keep T3's result for ``event``'s item; ``None`` means a structured record."""
        key = event.document_key
        with self._lock:
            if parsed is None:
                self._records.setdefault(event.source, set()).add(key)
                return
            previous = self._documents.get(key)
            self._documents[key] = ParsedEntry(
                document=parsed,
                lifetime=event.original_lifetime,
                parsed_at=datetime.now(UTC),
                runs=previous.runs + 1 if previous else 1,
            )

    def drop(self, document_key: str) -> bool:
        """Forget a deleted item: its parsed text goes with its originals. True if any was kept."""
        source = document_key.partition(":")[0]
        with self._lock:
            had_record = document_key in self._records.get(source, set())
            self._records.get(source, set()).discard(document_key)
            return self._documents.pop(document_key, None) is not None or had_record

    def get(self, document_key: str) -> ParsedEntry | None:
        with self._lock:
            return self._documents.get(document_key)

    def entries(self, source: str | None = None) -> list[ParsedEntry]:
        """Parsed documents, sorted by key."""
        with self._lock:
            entries = sorted(self._documents.items())
        return [e for key, e in entries if source is None or key.partition(":")[0] == source]

    def records(self) -> dict[str, int]:
        """Per source, how many live items T3 found to be records, not documents."""
        with self._lock:
            return {source: len(keys) for source, keys in sorted(self._records.items()) if keys}


@dataclass(frozen=True)
class ChunkEntry:
    document_key: str
    version: str
    title: str
    chunker_id: str
    max_tokens: int
    chunks: tuple[Chunk, ...]
    lifetime: int
    chunked_at: datetime
    runs: int
    """How many times T4 chunked this item: an edit adds one, a skip or a fast lane doesn't."""
    header_note: str = ""
    """What T4's header writer did for this version (S8): who wrote, or why none."""
    classifier_note: str = ""
    """What T5 did for this version (S8): where the department came from, which model."""
    why: tuple[str, ...] = ()
    """Per chunk: why T5 gave it its sensitivity."""
    linker_note: str = ""
    """What T6 did for this version (S9): how many entities it matched against."""
    mentions: tuple[tuple[EntityMention, ...], ...] = ()
    """Per chunk: what named each entity T6 linked it to (S9)."""


class ChunkStore:
    """T4's latest chunks per document, with T5's labels on them from S8.

    ``record`` and ``drop`` are what ``ekb inspector`` calls after T3: ``record``
    for every document T4 chunked (and T5 labelled), ``drop`` for every purge, so
    a deleted page's chunks go with its parsed text and its originals.
    """

    def __init__(self) -> None:
        self._documents: dict[str, ChunkEntry] = {}
        self._lock = threading.Lock()

    def record(
        self,
        event: ChangeEvent,
        title: str,
        chunks: tuple[Chunk, ...],
        *,
        chunker_id: str,
        max_tokens: int,
        header_note: str = "",
        classifier_note: str = "",
        why: tuple[str, ...] = (),
        linker_note: str = "",
        mentions: tuple[tuple[EntityMention, ...], ...] = (),
    ) -> None:
        """Keep T4's chunks for ``event``'s document, replacing the previous version's."""
        key = event.document_key
        with self._lock:
            previous = self._documents.get(key)
            self._documents[key] = ChunkEntry(
                document_key=key,
                version=event.version,
                title=title,
                chunker_id=chunker_id,
                max_tokens=max_tokens,
                chunks=chunks,
                lifetime=event.original_lifetime,
                chunked_at=datetime.now(UTC),
                runs=previous.runs + 1 if previous else 1,
                header_note=header_note,
                classifier_note=classifier_note,
                why=why,
                linker_note=linker_note,
                mentions=mentions,
            )

    def drop(self, document_key: str) -> bool:
        """Forget a deleted document's chunks. True if any were kept."""
        with self._lock:
            return self._documents.pop(document_key, None) is not None

    def get(self, document_key: str) -> ChunkEntry | None:
        with self._lock:
            return self._documents.get(document_key)

    def entries(self, source: str | None = None) -> list[ChunkEntry]:
        """Chunked documents, sorted by key."""
        with self._lock:
            entries = sorted(self._documents.items())
        return [e for key, e in entries if source is None or key.partition(":")[0] == source]


class EntityLifetimes:
    """Which lifetime of its record each entity was read from (S9).

    A delete closes its record's lifetime as soon as it is published; T2's
    purge removes the entity a moment later. In between, the inspector already
    hides it, as it hides T3's and T4's output: an entity whose record's
    lifetime is no longer the one it was read from is a deleted record's.
    """

    def __init__(self) -> None:
        self._lifetimes: dict[str, int] = {}
        self._lock = threading.Lock()

    def record(self, source_key: str, lifetime: int) -> None:
        with self._lock:
            self._lifetimes[source_key] = lifetime

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return self._lifetimes.copy()
