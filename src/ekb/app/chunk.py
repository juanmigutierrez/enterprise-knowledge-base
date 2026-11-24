"""T4 wired behind T3 (S6).

Two choices are made here, in the composition root, and nowhere else:

- **Which chunker.** ``structure_aware`` by hand, as S4 and S5 chose their
  options by hand. ``config/default.yaml`` already names it (card 4.2's
  starting choice) and the ``ci`` profile names ``fixed``; choosing from
  config comes with the pipeline (S12). The step's ``context_header``
  setting belongs to the header, which S8 adds as its own port next to the
  chunker: the chunkers take only their own settings.
- **When T4 runs.** Right after T3, on the same document, inside T2's
  ``downstream`` until S12 puts the steps on the bus. A record (T3's
  "not a document") is never chunked.

T4 runs *before* anything is kept: if it fails, neither T3's nor T4's result
is recorded, T2 records nothing, and the event comes back (S4's rule).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ekb.platform import registry
from ekb.process.t3_parse.step import ParseResult
from ekb.process.t4_chunk.options.fixed import FixedChunker
from ekb.process.t4_chunk.options.structure_aware import StructureAwareChunker
from ekb.process.t4_chunk.port import Chunker
from ekb.process.t4_chunk.step import ChunkResult, ChunkStep

CHUNKERS = (StructureAwareChunker, FixedChunker)  # imported, so both are registered
DEFAULT_CHUNKER = "structure_aware"


def chunker(name: str = DEFAULT_CHUNKER, **params: int) -> Chunker:
    """A registered T4 option by name (``structure_aware``, ``fixed``) with its settings."""
    built = registry.create("t4_chunk", name, **params)
    if not isinstance(built, Chunker):
        raise TypeError(f"t4_chunk option {name!r} does not implement the Chunker port")
    return built


def chunk_step(name: str = DEFAULT_CHUNKER, **params: int) -> ChunkStep:
    return ChunkStep(chunker(name, **params))


@dataclass(frozen=True)
class Processed:
    """What T3 and T4 made of one event T2 sent on."""

    parse: ParseResult
    chunks: ChunkResult | None
    """``None`` when T3 found a structured record, not a document: nothing to chunk."""


def chunk_after_t3(
    t4: ChunkStep, keep: Callable[[Processed], object]
) -> Callable[[ParseResult], None]:
    """T3's ``keep`` until S12: chunk what T3 parsed, then hand both results on.

    Use it as the ``keep`` of :func:`ekb.app.parse.parse_after_t2`; its
    ``forget`` must drop the document's chunks as well as its parsed text.
    """

    def then(result: ParseResult) -> None:
        chunks = t4.handle(result.parsed) if result.parsed is not None else None
        keep(Processed(result, chunks))  # only after T4: a failure keeps nothing

    return then
