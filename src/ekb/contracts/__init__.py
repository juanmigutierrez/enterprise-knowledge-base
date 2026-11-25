"""Shared data contracts. The only thing every layer is allowed to import.

Changing a model here is a contract change: bump ``schema_version`` (and,
once connectors in other languages exist, re-export their JSON schemas).
"""

from ekb.contracts.diff import DiffAction, DiffDecision
from ekb.contracts.entities import Entity, EntityKind, EntityMention, entity_id
from ekb.contracts.events import Acl, ChangeEvent, ChangeOp
from ekb.contracts.knowledge import Chunk, PiiKind, Sensitivity
from ekb.contracts.parsed import Block, BlockKind, ParsedDocument, render_blocks

__all__ = [
    "Acl",
    "Block",
    "BlockKind",
    "ChangeEvent",
    "ChangeOp",
    "Chunk",
    "DiffAction",
    "DiffDecision",
    "Entity",
    "EntityKind",
    "EntityMention",
    "ParsedDocument",
    "PiiKind",
    "Sensitivity",
    "entity_id",
    "render_blocks",
]
