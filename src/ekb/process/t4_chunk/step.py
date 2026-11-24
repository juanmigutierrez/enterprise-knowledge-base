"""Running T4 for one parsed document, and checking what the chunker returned.

::

    parsed  = T3's result                  # blocks, each with its section
    chunks  = chunker.chunk(parsed)        # the option: structure_aware, fixed
    check(parsed, chunks)                  # the rules every option keeps

T4 runs right after T3, so only where T2 decided ``process``: new or changed
text. A permission change or an unchanged save never re-chunks a document,
and a delete removes its chunks with its parsed text (the wiring in
``ekb.app.chunk`` does that, until the pipeline takes over in S12).

**The step checks the chunker.** Every option promises the same things (see
``port.py``); the step makes sure, so a buggy option fails loudly here
instead of publishing a chunk with the wrong version or, worse, with an ACL
that T8 never resolved. That last check is the publication gate's first
line: nothing leaves T4 publishable.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ekb.contracts import Chunk, ParsedDocument
from ekb.process.t4_chunk.port import Chunker, chunk_id


@dataclass(frozen=True)
class ChunkResult:
    document: ParsedDocument
    chunks: tuple[Chunk, ...]
    chunker_id: str
    max_tokens: int
    reason: str

    @property
    def over_limit(self) -> int:
        """Chunks larger than ``max_tokens``: a block too big to split (a long table)."""
        return sum(1 for chunk in self.chunks if chunk.tokens > self.max_tokens)


def summary(chunks: Sequence[Chunk], max_tokens: int | None = None) -> str:
    """``4 chunks, 40-61 tokens``; with ``max_tokens``, also how many are over it."""
    if not chunks:
        return "0 chunks"
    sizes = [chunk.tokens for chunk in chunks]
    low, high = min(sizes), max(sizes)
    size = f"{low} tokens" if low == high else f"{low}-{high} tokens"
    text = f"{len(chunks)} chunk{'s' if len(chunks) != 1 else ''}, {size}"
    over = sum(1 for n in sizes if max_tokens is not None and n > max_tokens)
    return text + (f", {over} over the {max_tokens}-token limit" if over else "")


class ChunkStep:
    """T4 with one chunker."""

    def __init__(self, chunker: Chunker) -> None:
        self._chunker = chunker

    @property
    def chunker_id(self) -> str:
        return self._chunker.chunker_id

    @property
    def max_tokens(self) -> int:
        return self._chunker.max_tokens

    def handle(self, document: ParsedDocument) -> ChunkResult:
        chunks = self._chunker.chunk(document)
        check(document, chunks)
        return ChunkResult(
            document=document,
            chunks=chunks,
            chunker_id=self.chunker_id,
            max_tokens=self.max_tokens,
            reason=f"chunked with {self.chunker_id}: {summary(chunks, self.max_tokens)}",
        )


class ChunkError(ValueError):
    """A chunker broke one of the rules every option keeps."""


def check(document: ParsedDocument, chunks: Sequence[Chunk]) -> None:
    """Raise :class:`ChunkError` unless ``chunks`` are a proper cut of ``document``."""
    for position, chunk in enumerate(chunks):
        where = f"{document.document_key} v{document.version}, chunk {position}"
        if (chunk.document_key, chunk.document_version) != (
            document.document_key,
            document.version,
        ):
            raise ChunkError(
                f"{where} says it is from {chunk.document_key} v{chunk.document_version}"
            )
        if chunk.position != position or chunk.chunk_id != chunk_id(
            document.document_key, position
        ):
            raise ChunkError(f"{where} is numbered {chunk.position} ({chunk.chunk_id})")
        if not chunk.text.strip():
            raise ChunkError(f"{where} is empty")
        if chunk.sensitivity is not None or chunk.vector is not None or chunk.acl is not None:
            raise ChunkError(
                f"{where} already has a sensitivity, vector or ACL: only T5, T7 and T8 set "
                "them, so a chunk can never be published before its permissions are resolved"
            )
