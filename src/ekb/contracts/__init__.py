"""Shared data contracts. The only thing every layer is allowed to import.

Changing a model here is a contract change: bump ``schema_version`` (and,
once connectors in other languages exist, re-export their JSON schemas).
"""

from ekb.contracts.diff import DiffAction, DiffDecision
from ekb.contracts.entities import Entity, EntityKind, EntityMention, entity_id
from ekb.contracts.events import Acl, ChangeEvent, ChangeOp
from ekb.contracts.knowledge import Chunk, PiiKind, Sensitivity
from ekb.contracts.parsed import Block, BlockKind, ParsedDocument, render_blocks
from ekb.contracts.permissions import (
    DocumentAcl,
    Membership,
    Principals,
    admits,
    readers_now,
)
from ekb.contracts.trace import (
    STEP_ORDER,
    StepOutcome,
    StepTrace,
    Trace,
    TraceStatus,
    step_rank,
)

__all__ = [
    "STEP_ORDER",
    "Acl",
    "Block",
    "BlockKind",
    "ChangeEvent",
    "ChangeOp",
    "Chunk",
    "DiffAction",
    "DiffDecision",
    "DocumentAcl",
    "Entity",
    "EntityKind",
    "EntityMention",
    "Membership",
    "ParsedDocument",
    "PiiKind",
    "Principals",
    "Sensitivity",
    "StepOutcome",
    "StepTrace",
    "Trace",
    "TraceStatus",
    "admits",
    "entity_id",
    "readers_now",
    "render_blocks",
    "step_rank",
]
