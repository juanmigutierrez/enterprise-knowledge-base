"""Knowledge contract: what the processing steps produce and the stores hold."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from ekb.contracts.events import Acl


class Sensitivity(StrEnum):
    """How carefully a passage must be handled, from least to most strict.

    The order matters: when two signals disagree (a rule and a model, a
    document's marker and a chunk's own content), the stricter one wins,
    because a missed restriction is a leak and an extra one is only friction
    (card 4.9).
    """

    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"

    @property
    def rank(self) -> int:
        """0 for public up to 3 for restricted."""
        return list(Sensitivity).index(self)

    @classmethod
    def strictest(cls, first: Sensitivity, *others: Sensitivity) -> Sensitivity:
        """The strictest of the levels given."""
        return max((first, *others), key=lambda level: level.rank)


class PiiKind(StrEnum):
    """Kinds of personal data T5 flags on a chunk.

    The first four identify a person or their money on their own, so a chunk
    holding one is at least confidential. An email address, a phone number or
    a name is flagged but does not raise the chunk by itself: a policy giving
    the HR team's address is not confidential.
    """

    IBAN = "iban"
    PAYMENT_CARD = "payment_card"
    IT_FISCAL_CODE = "it_fiscal_code"
    """Codice fiscale."""
    ES_NATIONAL_ID = "es_national_id"
    """DNI or NIE."""
    EMAIL = "email"
    PHONE = "phone"
    PERSON_NAME = "person_name"
    """Only a model finds names; the rules don't (no name recognizer yet)."""

    @property
    def high_risk(self) -> bool:
        return self in _HIGH_RISK


_HIGH_RISK = frozenset(
    {PiiKind.IBAN, PiiKind.PAYMENT_CARD, PiiKind.IT_FISCAL_CODE, PiiKind.ES_NATIONAL_ID}
)


class Chunk(BaseModel):
    """A searchable passage of one document version.

    T4 cuts it (S6): ``text``, ``section``, ``position``, ``tokens`` and
    ``chunker_id``, then writes its context header (S8): ``context_header`` and
    ``header_id``. T5 labels it (S8): ``department``, ``sensitivity``, ``pii``
    and ``classifier_id``. The later steps fill in the rest: entities (T6),
    the vector (T7) and the ACL (T8).

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
    """One or two sentences placing the passage in its document, written for search
    (T4, S8). Kept apart from ``text``: it is added in front of the text when the
    chunk is embedded and indexed, never quoted in an answer. Empty when no header
    was written (the ``none`` option, or no model was available)."""
    header_id: str = ""
    """What wrote the header: ``none@1``, or the option, prompt version and model, e.g.
    ``model@1(header@1, groq:openai/gpt-oss-20b)``. Failover changes the model, so it
    is recorded per chunk: headers from one model can be rewritten as a batch."""
    department: str | None = None
    """Which department the passage belongs to (T5): the source's own label when it
    has one, otherwise a guess from the text. ``None`` when nothing points to one."""
    sensitivity: Sensitivity | None = None
    """Set by T5 on every chunk; one of the three fields the publication gate needs."""
    pii: tuple[PiiKind, ...] = ()
    """Kinds of personal data found in the passage (T5), sorted, each once."""
    classifier_id: str = ""
    """Which T5 option labelled it, e.g. ``rules@1``."""
    entity_ids: tuple[str, ...] = ()
    acl: Acl | None = None
    embedding_model: str | None = None
    vector: tuple[float, ...] | None = None

    @property
    def publishable(self) -> bool:
        return self.acl is not None and self.sensitivity is not None and self.vector is not None
