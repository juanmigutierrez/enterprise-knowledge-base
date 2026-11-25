"""Builders for T8 tests: the twelve synthetic users, and chunks as T7 leaves them."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from sources.common.directory import Directory

from ekb.contracts import Acl, ChangeEvent, ChangeOp, Chunk, Membership, ParsedDocument
from ekb.process.t7_embed.options.fake import FakeEmbedder
from ekb.process.t7_embed.step import EmbedStep
from ekb.stores.permissions.options.in_memory import InMemoryPermissionStore
from tests.unit.process.t4_chunk.conftest import parsed
from tests.unit.process.t7_embed.conftest import labelled, synthetic

NOW = datetime(2026, 11, 25, tzinfo=UTC)
ALL_STAFF = Acl(groups=frozenset({"all-staff"}))
HR_ONLY = Acl(groups=frozenset({"hr-team"}))


def membership(user_id: str, *groups: str, active: bool = True, version: str = "0") -> Membership:
    return Membership(
        user_id=user_id,
        active=active,
        groups=frozenset(groups),
        source_key=f"identity:users/{user_id}",
        source_version=version,
    )


def directory_memberships() -> tuple[Membership, ...]:
    """The twelve users of ``directory.yaml``, as the identity sync would load them."""
    users = Directory.load().users
    return tuple(membership(uid, *u.groups, active=u.active) for uid, u in sorted(users.items()))


def loaded_store() -> InMemoryPermissionStore:
    store = InMemoryPermissionStore()
    for m in directory_memberships():
        store.put_membership(m)
    return store


def upsert(
    key: str = "hr_docs:HR-PL-01", acl: Acl = ALL_STAFF, version: str = "1.1"
) -> ChangeEvent:
    source, _, source_id = key.partition(":")
    return ChangeEvent(
        source=source,
        source_id=source_id,
        op=ChangeOp.UPSERT,
        version=version,
        occurred_at=NOW,
        acl=acl,
        raw_pointer=f"mem://raw/{key}@{version}",
        content_hash="sha256:abc",
    )


def acl_change(key: str, acl: Acl, version: str = "1.2") -> ChangeEvent:
    source, _, source_id = key.partition(":")
    return ChangeEvent(
        source=source,
        source_id=source_id,
        op=ChangeOp.ACL_CHANGE,
        version=version,
        occurred_at=NOW,
        acl=acl,
        content_hash="sha256:abc",
    )


def embed(document: ParsedDocument, chunks: tuple[Chunk, ...]) -> tuple[Chunk, ...]:
    return EmbedStep(FakeEmbedder(dimensions=8)).handle(document, chunks).chunks


def ready(page_id: str = "HR-PL-01") -> tuple[ParsedDocument, tuple[Chunk, ...]]:
    """A synthetic page cut, labelled and embedded: what T8 is given."""
    document, chunks = synthetic(page_id)
    return document, embed(document, chunks)


def ready_body(
    body: str, key: str = "hr_docs:X", version: str = "1.1"
) -> tuple[ParsedDocument, tuple[Chunk, ...]]:
    document = parsed(body, key=key, version=version)
    return document, embed(document, labelled(document))


def user_record(user_id: str, *groups: str, active: bool = True) -> bytes:
    """An identity user as the identity sync stores it (canonical JSON of the SCIM user)."""
    data = {
        "active": active,
        "displayName": user_id.title(),
        "groups": list(groups),
        "id": user_id,
        "location": "Milan",
        "userName": f"{user_id}@example.com",
    }
    return json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
