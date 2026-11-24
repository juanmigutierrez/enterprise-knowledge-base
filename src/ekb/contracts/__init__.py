"""Shared data contracts. The only thing every layer is allowed to import.

Changing a model here is a contract change: bump ``schema_version`` (and,
once connectors in other languages exist, re-export their JSON schemas).
"""

from ekb.contracts.diff import DiffAction, DiffDecision
from ekb.contracts.events import Acl, ChangeEvent, ChangeOp
from ekb.contracts.knowledge import Chunk, Sensitivity

__all__ = [
    "Acl",
    "ChangeEvent",
    "ChangeOp",
    "Chunk",
    "DiffAction",
    "DiffDecision",
    "Sensitivity",
]
