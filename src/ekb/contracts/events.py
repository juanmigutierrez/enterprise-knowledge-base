"""T1 contract: the single change event that every connector emits.

Whatever the source (webhook, delta API, CDC, nightly listing), a change is
reduced to this shape before it enters the change log. Everything downstream
depends on this model, never on a source-specific payload.

Every event is about one trackable item (``document_key``): a page, an HR
record, a database row or an identity user. Its events, in order, are that
item's history: what happened, when, and who did it.

Schema version 2 (S2a) added ``person_id`` and ``changed_by``. Both hold the
identity provider's user ID, the one canonical person ID (D3), never a
source's own ID such as ``E-1001``.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, model_validator


class ChangeOp(StrEnum):
    """What happened to the document in the source system."""

    UPSERT = "upsert"
    DELETE = "delete"
    ACL_CHANGE = "acl_change"  # permissions changed, content did not (fast lane)


class Acl(BaseModel):
    """Who may read the document, as canonical user and group IDs."""

    model_config = ConfigDict(frozen=True)

    users: frozenset[str] = frozenset()
    groups: frozenset[str] = frozenset()


class ChangeEvent(BaseModel):
    """One normalized change, keyed by ``source`` + ``source_id``."""

    model_config = ConfigDict(frozen=True)

    schema_version: int = 2
    original_lifetime: int = 0
    """Assigned at publication. A delete closes this lifetime of stored originals."""
    source: str
    source_id: str
    op: ChangeOp
    version: str
    occurred_at: datetime
    acl: Acl = Acl()
    raw_pointer: str | None = None
    content_hash: str | None = None
    person_id: str | None = None
    """The person this item is about (an HR record, a leave row, a user), if any."""
    changed_by: str | None = None
    """Who made the change, if the source says so. Unknown for deletes found by a listing."""

    @property
    def document_key(self) -> str:
        """Partition key: events for one document are processed in order."""
        return f"{self.source}:{self.source_id}"

    @model_validator(mode="after")
    def _check_op_fields(self) -> Self:
        if self.op is ChangeOp.UPSERT and self.raw_pointer is None:
            raise ValueError("an upsert must point to the stored original (raw_pointer)")
        if self.op is ChangeOp.DELETE and (
            self.raw_pointer is not None
            or self.content_hash is not None
            or self.acl != Acl()
            or self.person_id is not None
        ):
            raise ValueError(
                "a delete must not point to content or carry a hash, an ACL or a person"
            )
        return self
