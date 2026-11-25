"""T2 contract: what the diff decided for one change event, and why.

T2 produces it; the worker pipeline (S12) routes it to the next step, and the
inspector shows it. It lives here, with the other shared models, because
those readers sit in layers that may not import ``ekb.process``.

=============  ==========================================================
``action``     what the rest of the pipeline does with the event
=============  ==========================================================
``process``    the content is new or changed: parse, chunk, ..., T8
``acl_only``   only the readers changed: the fast lane, straight to T8
``skip``       nothing new: a repeat, or a new version with the same text
``purge``      the item was deleted: remove it from every store
=============  ==========================================================

Two rules are part of the contract, so no T2 option can break them: a delete
is always purged (never skipped), and only an upsert, which points to its
original, can be processed.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, model_validator

from ekb.contracts.events import ChangeEvent, ChangeOp


class DiffAction(StrEnum):
    """What the steps after T2 must do with one event."""

    PROCESS = "process"
    ACL_ONLY = "acl_only"
    SKIP = "skip"
    PURGE = "purge"


class DiffDecision(BaseModel):
    """T2's verdict on one event, with the reason in plain language."""

    model_config = ConfigDict(frozen=True)

    action: DiffAction
    reason: str
    event: ChangeEvent
    previous_version: str | None = None
    """The version T2 compared against; ``None`` if the item had never been processed."""

    @model_validator(mode="after")
    def _check_action(self) -> Self:
        is_delete = self.event.op is ChangeOp.DELETE
        if is_delete != (self.action is DiffAction.PURGE):
            raise ValueError("a delete must be purged, and only a delete can be purged")
        if self.action is DiffAction.PROCESS and self.event.op is not ChangeOp.UPSERT:
            raise ValueError("only an upsert carries an original to process")
        return self
