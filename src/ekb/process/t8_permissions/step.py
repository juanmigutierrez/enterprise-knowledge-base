"""Running T8: put the readers on the chunks, check the publication gate, publish.

Two ways in, one per kind of change T2 sends on::

    T2 process  → T3 … T7 → publish(event, document, chunks)
                              resolution = resolver.resolve(event, memberships)
                              chunks     = with_readers(chunks, record, event)   # acl on each
                              index.publish(key, chunks)                       # the gate, again
                              permissions.put_readers(record)

    T2 acl_only ──────────→ apply_readers(event)                           # the fast lane
                              index.set_readers(record)                        # text, vector stay
                              permissions.put_readers(record)

**The step checks, the resolver only answers.** Chunks must come from the
event's own version, carry a sensitivity (T5) and a vector (T7), and no
readers yet (a new version is a new set of chunks). After the readers are on,
every chunk must be publishable; the search store checks again at the door.

**Readers before content.** On an edit (``process``) the wiring calls
:meth:`PermissionStep.readers_first` *before* T3 runs: if the same save also
removed a reader, the chunks already published lose that reader at once, even
if T7's model is down and the new text has to wait. If the change adds any user or group,
T8 withdraws the old text until the replacement passes the publication gate.

**What T8 does not do: look up groups.** The chunks keep the source's groups;
who is in them is looked up when someone asks (:func:`ekb.contracts.admits`).
So a group change in the identity provider reaches search on the next request
and rewrites no chunk. ``readers`` in the result lists who could read the
document now, for people to look at.

The search store is written first, then the permission store. If the second
write fails, T2 records nothing and the event comes back; both writes are safe
to repeat.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from ekb.contracts import (
    Acl,
    ChangeEvent,
    ChangeOp,
    Chunk,
    DocumentAcl,
    ParsedDocument,
    readers_now,
)
from ekb.process.t8_permissions.port import PermissionResolver, Resolution
from ekb.stores.permissions.port import PermissionStore
from ekb.stores.search.port import SearchStore


class T8Action(StrEnum):
    """What one T8 call did."""

    PUBLISH = "publish"
    """A version's chunks went into the search store, with their readers."""
    READERS = "readers"
    """New readers on the chunks already published (the fast lane, or readers first)."""
    UNCHANGED = "unchanged"
    """Readers first on an edit whose readers were already the ones published."""


@dataclass(frozen=True)
class PermissionResult:
    """What T8 did for one event."""

    event: ChangeEvent
    action: T8Action
    record: DocumentAcl
    """The readers now recorded for the document."""
    previous: DocumentAcl | None
    """The readers recorded before, if any."""
    resolution: Resolution
    chunks: tuple[Chunk, ...]
    """The document's chunks in the search store now, with their readers."""
    replaced: int
    """``publish``: chunks of the previous version dropped. ``readers``: chunks updated."""
    readers: tuple[str, ...]
    """The active users who could read the document now."""
    reason: str


class T8Error(ValueError):
    """T8 was given chunks or an event it must not publish."""


class PermissionStep:
    """T8 with one resolver, the permission store and the search store."""

    def __init__(
        self, resolver: PermissionResolver, permissions: PermissionStore, index: SearchStore
    ) -> None:
        self._resolver = resolver
        self._permissions = permissions
        self._index = index

    @property
    def resolver_id(self) -> str:
        return self._resolver.resolver_id

    def publish(
        self, event: ChangeEvent, document: ParsedDocument, chunks: Sequence[Chunk]
    ) -> PermissionResult:
        """A processed version, after T7: readers on every chunk, then into the search store."""
        check_input(event, document, chunks)
        resolution = self._resolver.resolve(event, self._permissions)
        record = self._record(event, resolution)
        out = with_readers(chunks, record, event)
        published = self._index.publish(event.document_key, out)
        previous = self._permissions.put_readers(record)
        readers = readers_now(record.acl, self._permissions.memberships())
        replaced = f" (replaced {published.replaced})" if published.replaced else ""
        return PermissionResult(
            event=event,
            action=T8Action.PUBLISH,
            record=record,
            previous=previous,
            resolution=resolution,
            chunks=out,
            replaced=published.replaced,
            readers=readers,
            reason=(
                f"published {published.published} chunks of v{event.version}{replaced}, "
                f"readers {describe(record.acl)}: {resolution.note}"
            ),
        )

    def apply_readers(
        self, event: ChangeEvent, *, processing_content: bool = False
    ) -> PermissionResult:
        """The fast lane: the event's readers onto the chunks already published.

        Nothing is parsed, cut or embedded: a vector comes from the text, not from
        who may read it. A document with nothing published yet (first seen as a
        permissions change) only gets its readers recorded.
        """
        if event.op is ChangeOp.DELETE:
            raise T8Error(f"{event.document_key}: a delete has no readers, it is purged")
        resolution = self._resolver.resolve(event, self._permissions)
        record = self._record(event, resolution)
        previous = self._permissions.readers(event.document_key)
        readers = readers_now(record.acl, self._permissions.memberships())
        published = self._index.chunks(event.document_key)
        withdrawn = 0
        if published:
            old = published[0]
            content_changed = processing_content or (
                event.content_hash is not None and event.content_hash != old.source_content_hash
            )
            old_acl = old.acl
            if content_changed and (
                old_acl is None
                or not record.acl.users <= old_acl.users
                or not record.acl.groups <= old_acl.groups
            ):
                withdrawn = self._index.remove(event.document_key)
        if previous is not None and (previous.acl, previous.resolver_id) == (
            record.acl,
            record.resolver_id,
        ):
            return PermissionResult(
                event=event,
                action=T8Action.UNCHANGED,
                record=previous,
                previous=previous,
                resolution=resolution,
                chunks=self._index.chunks(event.document_key),
                replaced=0,
                readers=readers,
                reason=f"readers unchanged ({describe(record.acl)})",
            )
        updated = withdrawn or self._index.set_readers(record)
        self._permissions.put_readers(record)
        change = readers_diff(previous.acl if previous else None, record.acl)
        where = f"on {updated} published chunks" if updated else "recorded (nothing published yet)"
        if withdrawn:
            where = f"recorded; withdrew {withdrawn} chunks until the replacement is published"
        return PermissionResult(
            event=event,
            action=T8Action.READERS,
            record=record,
            previous=previous,
            resolution=resolution,
            chunks=self._index.chunks(event.document_key),
            replaced=updated,
            readers=readers,
            reason=f"readers {change} {where}: {resolution.note}",
        )

    def readers_first(self, event: ChangeEvent) -> PermissionResult | None:
        """Readers before content, for an edit (T2 ``process``), before T3 runs.

        The fast lane for a document already published, so a reader removed in the
        same save loses access now. ``None`` for a document with no readers recorded
        yet: nothing of it is published, and T8 publishes it after T7.
        """
        if self._permissions.readers(event.document_key) is None:
            return None
        return self.apply_readers(event, processing_content=True)

    def _record(self, event: ChangeEvent, resolution: Resolution) -> DocumentAcl:
        return DocumentAcl(
            document_key=event.document_key,
            acl=resolution.acl,
            acl_version=event.version,
            occurred_at=event.occurred_at,
            resolver_id=resolution.resolver_id,
        )


def check_input(event: ChangeEvent, document: ParsedDocument, chunks: Sequence[Chunk]) -> None:
    """What T8 refuses to publish: another version's chunks, or chunks not ready for it."""
    if event.op is not ChangeOp.UPSERT:
        raise T8Error(f"{event.document_key}: only an upsert publishes chunks")
    if (document.document_key, document.version) != (event.document_key, event.version):
        raise T8Error(
            f"{event.document_key} v{event.version}: the document is "
            f"{document.document_key} v{document.version}"
        )
    for chunk in chunks:
        where = f"{event.document_key} v{event.version}, {chunk.chunk_id}"
        if (chunk.document_key, chunk.document_version) != (event.document_key, event.version):
            raise T8Error(f"{where} is from {chunk.document_key} v{chunk.document_version}")
        if chunk.sensitivity is None:
            raise T8Error(f"{where} has no sensitivity: T5 runs before T8")
        if chunk.vector is None:
            raise T8Error(f"{where} has no vector: T7 runs before T8")
        if chunk.acl is not None:
            raise T8Error(f"{where} already has readers: a new version is a new set")


def with_readers(
    chunks: Sequence[Chunk], record: DocumentAcl, event: ChangeEvent
) -> tuple[Chunk, ...]:
    """``chunks`` with ``record``'s readers on each; every one must then be publishable."""
    out = tuple(
        chunk.model_copy(
            update={
                "original_lifetime": event.original_lifetime,
                "source_content_hash": event.content_hash,
                "acl": record.acl,
                "acl_version": record.acl_version,
                "resolver_id": record.resolver_id,
            }
        )
        for chunk in chunks
    )
    for chunk in out:
        if not chunk.publishable:  # unreachable after check_input; the gate, stated once more
            raise T8Error(f"{chunk.chunk_id} is still not publishable")
    return out


def describe(acl: Acl) -> str:
    """``all-staff`` · ``hr-team, user:bob`` · ``nobody``: readers in a few words."""
    names = sorted(acl.groups) + [f"user:{u}" for u in sorted(acl.users)]
    return ", ".join(names) or "nobody"


def readers_diff(before: Acl | None, after: Acl) -> str:
    """``-hr-team +es-employees``: who gained and who lost access (users as ``user:``)."""

    def names(acl: Acl | None) -> set[str]:
        if acl is None:
            return set()
        return set(acl.groups) | {f"user:{u}" for u in acl.users}

    old, new = names(before), names(after)
    changes = [f"+{n}" for n in sorted(new - old)] + [f"-{n}" for n in sorted(old - new)]
    return " ".join(changes) or "no change"
