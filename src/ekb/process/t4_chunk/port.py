"""Port for T4 · Chunk. Every chunking option implements :class:`Chunker`.

A chunker reads a :class:`~ekb.contracts.ParsedDocument` (T3's blocks, each
with its section) and returns the document's chunks, in reading order. It is
pure: no I/O, and the same document always gives the same chunks with the
same IDs, so re-chunking an unchanged document changes nothing downstream.

Three rules every option keeps, and :class:`~ekb.process.t4_chunk.step.ChunkStep`
checks:

- every chunk belongs to the document and version it was cut from, and the
  chunks are numbered 0 to n-1 (``chunk_id`` is ``<document_key>#<position>``);
- no chunk is empty;
- a chunker never sets what later steps own: sensitivity (T5), the vector
  (T7), the ACL (T8). That is what keeps a chunk unpublishable until T8.

``max_tokens`` is the size a chunk should stay within. An option may go over
it when the only alternative is to cut a block in half (a long table); the
step reports how many chunks did.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ekb.contracts import Chunk, ParsedDocument
from ekb.process.t4_chunk.tokens import count_tokens


@runtime_checkable
class Chunker(Protocol):
    """Cuts one parsed document into chunks. Pure: no I/O."""

    @property
    def chunker_id(self) -> str: ...

    @property
    def max_tokens(self) -> int: ...

    def chunk(self, document: ParsedDocument) -> tuple[Chunk, ...]: ...


def chunk_id(document_key: str, position: int) -> str:
    """``hr_docs:HR-PL-01#2``: the third chunk of that document."""
    return f"{document_key}#{position}"


def new_chunk(
    document: ParsedDocument,
    position: int,
    text: str,
    section: tuple[str, ...],
    chunker_id: str,
) -> Chunk:
    """A chunk of ``document``, built the same way by every option."""
    return Chunk(
        document_key=document.document_key,
        document_version=document.version,
        chunk_id=chunk_id(document.document_key, position),
        text=text,
        section=section,
        position=position,
        tokens=count_tokens(text),
        chunker_id=chunker_id,
    )


def positive(value: int, name: str) -> int:
    """Settings are checked when the option is built, not when the first document fails."""
    if value < 1:
        raise ValueError(f"{name} must be at least 1, not {value}")
    return value
