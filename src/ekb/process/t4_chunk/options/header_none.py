"""T4 header option ``none``: no context header.

The baseline of card 4.3 ("none"), and the ``ci`` profile's choice: it needs no
model, so tests and CI stay offline. Every chunk keeps an empty header.
"""

from __future__ import annotations

from ekb.contracts import Chunk, ParsedDocument
from ekb.platform.registry import register
from ekb.process.t4_chunk.header import HeaderOutcome, Headers


@register("t4_header", "none")
class NoHeader:
    @property
    def header_id(self) -> str:
        return "none@1"

    def write(self, document: ParsedDocument, chunks: tuple[Chunk, ...]) -> Headers:
        return Headers(
            texts=("",) * len(chunks),
            header_id=self.header_id,
            outcome=HeaderOutcome.NONE,
            note="no header: the none option writes none",
        )
