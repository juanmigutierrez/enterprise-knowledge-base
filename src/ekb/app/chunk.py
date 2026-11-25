"""T4 wired behind T3 (S6), with its context header (S8), T5 (S8), T6 (S9), T7 (S10) and
T8 (S11) behind it.

Three choices are made here, in the composition root, and nowhere else:

- **Which chunker.** ``structure_aware`` by hand, as S4 and S5 chose their
  options by hand. ``config/default.yaml`` already names it (card 4.2's
  starting choice) and the ``ci`` profile names ``fixed``; choosing from
  config comes with the pipeline (S12).
- **Which header writer** (S8): ``none`` or ``model``, its own port next to the
  chunker (``t4_header`` in the config, card 4.3). ``model`` needs the model
  gateway. Without one (the S6 wiring), chunks keep an empty header.
- **When T4 runs, and T5 to T8 after it.** Right after T3, on the same
  document, inside T2's ``downstream`` until S12 puts the steps on the bus. A
  record (T3's "not a document") is never chunked, labelled, linked, embedded
  or published; a record that defines an entity (an HR employee or team)
  updates the entity store instead (S9, ``ekb.app.entities``), and an identity
  user record updates the permission store's memberships (S11,
  ``ekb.app.permissions``).

T4 to T7 run *before* anything is kept, and T8 publishes only once they all
succeeded: if any of them fails (T7's model is down, say), nothing new is
published, no result is recorded, T2 records nothing, and the event comes back
(S4's rule).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from ekb.contracts import Chunk
from ekb.platform import registry
from ekb.platform.gateway.port import Completer
from ekb.process.t3_parse.step import ParseResult
from ekb.process.t4_chunk.header import HeaderWriter
from ekb.process.t4_chunk.options.fixed import FixedChunker
from ekb.process.t4_chunk.options.header_model import ModelHeaderWriter
from ekb.process.t4_chunk.options.header_none import NoHeader
from ekb.process.t4_chunk.options.structure_aware import StructureAwareChunker
from ekb.process.t4_chunk.port import Chunker
from ekb.process.t4_chunk.step import ChunkResult, ChunkStep
from ekb.process.t5_classify.step import ClassifyResult, ClassifyStep
from ekb.process.t6_entities.step import LinkResult, LinkStep
from ekb.process.t6_entities.sync import EntitySync, RecordResult
from ekb.process.t7_embed.step import EmbedResult, EmbedStep
from ekb.process.t8_permissions.step import PermissionResult, PermissionStep
from ekb.process.t8_permissions.sync import MembershipResult, MembershipSync

CHUNKERS = (StructureAwareChunker, FixedChunker)  # imported, so both are registered
HEADERS = (NoHeader, ModelHeaderWriter)
DEFAULT_CHUNKER = "structure_aware"
HEADER_OPTIONS = ("none", "model")
MODEL_HEADERS = frozenset({"model"})
"""Header options that call a model, and so need the gateway."""


def chunker(name: str = DEFAULT_CHUNKER, **params: int) -> Chunker:
    """A registered T4 option by name (``structure_aware``, ``fixed``) with its settings."""
    built = registry.create("t4_chunk", name, **params)
    if not isinstance(built, Chunker):
        raise TypeError(f"t4_chunk option {name!r} does not implement the Chunker port")
    return built


def header_writer(name: str, gateway: Completer | None = None) -> HeaderWriter:
    """A registered T4 header option by name (``none``, ``model``). ``model`` writes
    through ``gateway`` (its ``context_header`` task)."""
    if name in MODEL_HEADERS:
        if gateway is None:
            raise ValueError(f"the {name!r} header option needs the model gateway")
        built = registry.create("t4_header", name, gateway=gateway)
    else:
        built = registry.create("t4_header", name)
    if not isinstance(built, HeaderWriter):
        raise TypeError(f"t4_header option {name!r} does not implement the HeaderWriter port")
    return built


def chunk_step(
    name: str = DEFAULT_CHUNKER, *, header: HeaderWriter | None = None, **params: int
) -> ChunkStep:
    return ChunkStep(chunker(name, **params), header)


@dataclass(frozen=True)
class Processed:
    """What T3, T4 and T5 made of one event T2 sent on."""

    parse: ParseResult
    chunks: ChunkResult | None
    """``None`` when T3 found a structured record, not a document: nothing to chunk."""
    labels: ClassifyResult | None = None
    """T5's result (S8); ``None`` without T5, or with nothing to label."""
    links: LinkResult | None = None
    """T6's result (S9); ``None`` without T6, or with nothing to link."""
    record: RecordResult | None = None
    """For a record that defines an entity: what it did to the entity store (S9)."""
    vectors: EmbedResult | None = None
    """T7's result (S10); ``None`` without T7, or with nothing to embed."""
    published: PermissionResult | None = None
    """T8's result (S11): the chunks with their readers, as published. ``None`` without T8."""
    membership: MembershipResult | None = None
    """For an identity user record: what it did to the permission store (S11)."""

    @property
    def final(self) -> tuple[Chunk, ...]:
        """The chunks as the last step that ran left them: published if T8 ran."""
        if self.published is not None:
            return self.published.chunks
        if self.vectors is not None:
            return self.vectors.chunks
        if self.links is not None:
            return self.links.chunks
        if self.labels is not None:
            return self.labels.chunks
        return self.chunks.chunks if self.chunks is not None else ()


def chunk_after_t3(
    t4: ChunkStep,
    keep: Callable[[Processed], object],
    t5: ClassifyStep | None = None,
    t6: LinkStep | None = None,
    records: EntitySync | None = None,
    t7: EmbedStep | None = None,
    t8: PermissionStep | None = None,
    memberships: MembershipSync | None = None,
) -> Callable[[ParseResult], None]:
    """T3's ``keep`` until S12: chunk what T3 parsed, label it (with ``t5``), link
    it (with ``t6``), embed it (with ``t7``), publish it with its readers (with
    ``t8``), then hand every result on. A record that defines an entity goes to
    ``records`` instead, and an identity user record to ``memberships``.

    ``t8`` without ``t7`` refuses every document: a chunk with no vector is never
    published (the gate).

    Use it as the ``keep`` of :func:`ekb.app.parse.parse_after_t2`; its
    ``forget`` must drop the document's chunks as well as its parsed text, its
    cached model replies (:func:`ekb.app.classify.forgetter`) and the entity its
    record defined (:meth:`EntitySync.forget`).
    """

    def then(result: ParseResult) -> None:
        if result.parsed is None:
            event = result.event
            record = None
            if records is not None and records.handles(event.source):
                record = records.handle(event)
            membership = None
            if memberships is not None and memberships.handles(event.source):
                membership = memberships.handle(event)
            keep(Processed(result, None, record=record, membership=membership))
            return
        chunks = t4.handle(result.parsed)
        labels = t5.handle(chunks.document, chunks.chunks) if t5 is not None else None
        links = None
        if t6 is not None:
            links = t6.handle(chunks.document, labels.chunks if labels else chunks.chunks)
        linked = Processed(result, chunks, labels, links)
        vectors = t7.handle(chunks.document, linked.final) if t7 is not None else None
        embedded = replace(linked, vectors=vectors)
        published = None
        if t8 is not None:  # only after T4-T7 succeeded: a failure publishes nothing
            published = t8.publish(result.event, chunks.document, embedded.final)
        keep(replace(embedded, published=published))

    return then
