"""T1 contract: the single change event that every connector emits.

Whatever the source (webhook, delta API, CDC, nightly listing), a change is
reduced to this shape before it enters the change log. Everything downstream
depends on this model, never on a source-specific payload.
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

    schema_version: int = 1
    source: str
    source_id: str
    op: ChangeOp
    version: str
    occurred_at: datetime
    acl: Acl = Acl()
    raw_pointer: str | None = None
    content_hash: str | None = None

    @property
    def document_key(self) -> str:
        """Partition key: events for one document are processed in order."""
        return f"{self.source}:{self.source_id}"

    @model_validator(mode="after")
    def _check_op_fields(self) -> Self:
        if self.op is ChangeOp.UPSERT and self.raw_pointer is None:
            raise ValueError("an upsert must point to the stored original (raw_pointer)")
        if self.op is ChangeOp.DELETE and self.raw_pointer is not None:
            raise ValueError("a delete must not point to content")
        return self
