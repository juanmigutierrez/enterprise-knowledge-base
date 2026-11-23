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

    A chunk is only published to the search index once ``acl`` and
    ``sensitivity`` are set (T8); see the publication test in
    docs/option-evaluation.md 4.14.
    """

    model_config = ConfigDict(frozen=True)

    document_key: str
    document_version: str
    chunk_id: str
    text: str
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
