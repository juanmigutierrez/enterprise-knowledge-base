"""Knowledge contract: what the processing steps produce and the stores hold."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from ekb.contracts.events import Acl


class Sensitivity(StrEnum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


class Chunk(BaseModel):
    """A searchable passage of one document version.

    T4 cuts it (S6): ``text``, ``section``, ``position``, ``tokens`` and
    ``chunker_id``. The later steps fill in the rest: the context header (T4,
    S8), department and sensitivity (T5), entities (T6), the vector (T7) and
    the ACL (T8).

    A chunk is only published to the search index once ``acl``,
    ``sensitivity`` and ``vector`` are set; see the publication test in
    docs/option-evaluation.md 4.14.

    ``chunk_id`` is ``<document_key>#<position>`` (``hr_docs:HR-PL-01#2``). It
    names a place in a document, so ``document_version`` says which version's
    text it holds: a new version replaces the document's whole set of chunks.
    """

    model_config = ConfigDict(frozen=True)

    document_key: str
    document_version: str
    chunk_id: str
    text: str
    section: tuple[str, ...] = ()
    """The headings this passage sits under, outermost first (T3's trail). Empty
    when the chunker ignores structure: the fixed-window baseline does."""
    position: int = 0
    """Where it sits in the document, from 0: one version's chunks are 0 to n-1."""
    tokens: int = 0
    """Its size, as the chunker counted it (an approximation until a model's
    tokenizer is used; see ``ekb.process.t4_chunk.tokens``)."""
    chunker_id: str = ""
    """Which chunker, version and settings cut it, e.g.
    ``structure_aware@1(max_tokens=400)``. Like T3's ``parser_id`` and T7's model
    ID: a change of chunker is visible, so re-chunking can be planned."""
    context_header: str = ""
    department: str | None = None
    sensitivity: Sensitivity | None = None
    entity_ids: tuple[str, ...] = ()
    acl: Acl | None = None
    embedding_model: str | None = None
    vector: tuple[float, ...] | None = None

    @property
    def publishable(self) -> bool:
        return self.acl is not None and self.sensitivity is not None and self.vector is not None
