"""Rules every source's T1 follows.

- **A delete carries nothing about the item**: no content, pointer, hash, ACL
  or person. It may say who deleted it. (The contract enforces this too.)
- **Structured records are stored as canonical JSON** (sorted keys, no extra
  spaces), and the hash is taken of exactly those bytes. The same record then
  always has the same hash, whatever order the source sent its fields in, so T2
  can skip repeats.
- **A change the connector doesn't understand is an error, not a skip.** A
  dropped change could be a delete or a revocation.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from ekb.contracts import Acl, ChangeEvent, ChangeOp

ALL_STAFF = Acl(groups=frozenset({"all-staff"}))
"""Record systems have no per-record permission list; directory-like data is company-wide."""


class UnknownChangeKindError(ValueError):
    """The source reported a change (kind, table, operation) the connector does not understand."""


def canonical_json(record: Mapping[str, Any]) -> bytes:
    return json.dumps(
        record, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    ).encode()


def content_hash(original: bytes) -> str:
    return f"sha256:{hashlib.sha256(original).hexdigest()}"


def delete_event(
    source: str,
    source_id: str,
    *,
    version: str,
    occurred_at: datetime,
    changed_by: str | None = None,
) -> ChangeEvent:
    return ChangeEvent(
        source=source,
        source_id=source_id,
        op=ChangeOp.DELETE,
        version=version,
        occurred_at=occurred_at,
        changed_by=changed_by,
    )


def record_upsert(
    source: str,
    source_id: str,
    *,
    version: str,
    occurred_at: datetime,
    acl: Acl,
    raw_pointer: str,
    original: bytes,
    person_id: str | None,
    changed_by: str | None,
) -> ChangeEvent:
    """An upsert for a structured record whose original is ``original`` (canonical JSON)."""
    return ChangeEvent(
        source=source,
        source_id=source_id,
        op=ChangeOp.UPSERT,
        version=version,
        occurred_at=occurred_at,
        acl=acl,
        raw_pointer=raw_pointer,
        content_hash=content_hash(original),
        person_id=person_id,
        changed_by=changed_by,
    )
