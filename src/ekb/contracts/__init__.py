"""Shared data contracts. The only thing every layer is allowed to import.

Changing a model here is a contract change: bump ``schema_version`` (and,
once connectors in other languages exist, re-export their JSON schemas).
"""

from ekb.contracts.agent import AssistantAnswer, Caller, Identity
from ekb.contracts.answer import (
    AccessVerdict,
    Answer,
    AnswerResult,
    Citation,
    Passage,
    QueryPlan,
    Redaction,
    Released,
    SourceAccess,
    Withheld,
)
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
from ekb.contracts.retrieval import Hit, Leg, Retrieval, Scope, SearchResult
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
    "AccessVerdict",
    "Acl",
    "Answer",
    "AnswerResult",
    "AssistantAnswer",
    "Block",
    "BlockKind",
    "Caller",
    "ChangeEvent",
    "ChangeOp",
    "Chunk",
    "Citation",
    "DiffAction",
    "DiffDecision",
    "DocumentAcl",
    "Entity",
    "EntityKind",
    "EntityMention",
    "Hit",
    "Identity",
    "Leg",
    "Membership",
    "ParsedDocument",
    "Passage",
    "PiiKind",
    "Principals",
    "QueryPlan",
    "Redaction",
    "Released",
    "Retrieval",
    "Scope",
    "SearchResult",
    "Sensitivity",
    "SourceAccess",
    "StepOutcome",
    "StepTrace",
    "Trace",
    "TraceStatus",
    "Withheld",
    "admits",
    "entity_id",
    "readers_now",
    "render_blocks",
    "step_rank",
]
