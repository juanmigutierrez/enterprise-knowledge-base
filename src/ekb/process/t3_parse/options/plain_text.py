"""T3 baseline: plain text, no structure.

This is what a simple text extractor gives you (card 4.1's "plain text
extraction"): the words, in order, split into paragraphs at blank lines, and
nothing else. Markdown symbols stay as they are (``## Booking leave`` is just
a line of text), no block knows its section, and a table is a paragraph of
pipes.

It exists to be measured against: the first parsing experiment (card 4.1, P1)
compares retrieval with and without structure, and the ``ci`` profile uses it
because it needs nothing.
"""

from __future__ import annotations

import re

from ekb.contracts import Block, BlockKind, ParsedDocument
from ekb.platform.registry import register
from ekb.process.t3_parse.port import MARKDOWN, PLAIN_TEXT, SourceFile, decode_text

READS = frozenset({MARKDOWN, PLAIN_TEXT})
_BLANK_LINE = re.compile(r"\n[ \t]*\n")


@register("t3_parse", "plain_text")
class PlainTextParser:
    @property
    def parser_id(self) -> str:
        return "plain_text@1"

    def parse(self, source: SourceFile) -> ParsedDocument:
        text = decode_text(source, READS).replace("\r\n", "\n").replace("\r", "\n")
        paragraphs = (part.strip() for part in _BLANK_LINE.split(text))
        return ParsedDocument(
            document_key=source.document_key,
            version=source.version,
            title=source.title,
            language=source.language,
            department=source.department,
            media_type=source.media_type,
            parser_id=self.parser_id,
            blocks=tuple(Block(kind=BlockKind.PARAGRAPH, text=p) for p in paragraphs if p),
        )
