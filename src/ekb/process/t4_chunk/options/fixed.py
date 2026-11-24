"""T4 baseline: fixed windows of tokens, blind to structure (card 4.2's "fixed with overlap").

This is what a simple pipeline does: take the document's text, cut it every
``max_tokens`` tokens, and let consecutive windows share ``overlap_tokens``
tokens so a sentence cut at a boundary is whole in at least one of them.
Headings, lists and tables are just text here: a window can start in the
middle of a numbered step, or put a table's rows in one chunk and its header
row in the one before.

It exists to be measured against: card 4.2's first experiment compares
retrieval with structure-aware chunks and with these. The ``ci`` profile
names it because it needs nothing.

Windows are cut out of the document's clean text (``ParsedDocument.text``,
the same text the structure-aware chunks are made of) at token boundaries,
so line breaks are kept. Its chunks have no section, on purpose.
"""

from __future__ import annotations

from ekb.contracts import Chunk, ParsedDocument
from ekb.platform.registry import register
from ekb.process.t4_chunk.port import new_chunk, positive
from ekb.process.t4_chunk.tokens import token_spans

DEFAULT_OVERLAP = 50


@register("t4_chunk", "fixed")
class FixedChunker:
    def __init__(self, max_tokens: int = 400, overlap_tokens: int = DEFAULT_OVERLAP) -> None:
        self._max_tokens = positive(max_tokens, "max_tokens")
        if not 0 <= overlap_tokens < max_tokens:
            raise ValueError(
                f"overlap_tokens must be at least 0 and less than max_tokens ({max_tokens}), "
                f"not {overlap_tokens}: otherwise a window would never move forward"
            )
        self._overlap = overlap_tokens

    @property
    def chunker_id(self) -> str:
        return f"fixed@1(max_tokens={self._max_tokens},overlap_tokens={self._overlap})"

    @property
    def max_tokens(self) -> int:
        return self._max_tokens

    @property
    def overlap_tokens(self) -> int:
        return self._overlap

    def chunk(self, document: ParsedDocument) -> tuple[Chunk, ...]:
        text = document.text
        spans = token_spans(text)
        chunks: list[Chunk] = []
        start = 0
        while start < len(spans):
            end = min(start + self._max_tokens, len(spans))
            window = text[spans[start][0] : spans[end - 1][1]]
            chunks.append(new_chunk(document, len(chunks), window, (), self.chunker_id))
            if end == len(spans):
                break
            start = end - self._overlap
        return tuple(chunks)
