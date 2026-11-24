"""T3 for Markdown: a CommonMark parser that keeps headings, lists and tables.

The docs tool's pages are Markdown, so this is the parser that understands
them. It uses ``markdown-it-py`` (CommonMark, the Markdown standard, plus the
GitHub table syntax) rather than hand-written rules: nested lists, a ``|``
escaped inside a table cell or a heading written as ``Title`` over ``=====``
are easy to get wrong by hand, and a parsing mistake here becomes a wrong
chunk boundary in T4 and a worse search result in Q3.

The parser produces a token stream (``heading_open``, ``inline``,
``heading_close``, ``bullet_list_open`` ...). :class:`_Walker` reads it once,
in order, keeping track of three things:

- the **headings above** the current position, to give each block its section;
- the **lists it is inside**, to give each list item its depth and marker;
- the **table rows** collected so far, to emit a table as one block.

Raw HTML is not interpreted (``html: False``): it stays as text, so nothing a
page contains is silently dropped.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import markdown_it
from markdown_it import MarkdownIt
from markdown_it.token import Token

from ekb.contracts import Block, BlockKind, ParsedDocument
from ekb.platform.registry import register
from ekb.process.t3_parse.port import MARKDOWN, PLAIN_TEXT, SourceFile, decode_text

READS = frozenset({MARKDOWN, PLAIN_TEXT})


@register("t3_parse", "markdown")
class MarkdownParser:
    def __init__(self) -> None:
        self._md = MarkdownIt("commonmark", {"html": False}).enable("table")

    @property
    def parser_id(self) -> str:
        return f"markdown-it-py@{markdown_it.__version__}"

    def parse(self, source: SourceFile) -> ParsedDocument:
        text = decode_text(source, READS)
        return ParsedDocument(
            document_key=source.document_key,
            version=source.version,
            title=source.title,
            language=source.language,
            media_type=source.media_type,
            parser_id=self.parser_id,
            blocks=tuple(_Walker().walk(self._md.parse(text))),
        )


def inline_text(token: Token) -> str:
    """The text a reader sees: formatting marks and link targets removed, spaces collapsed."""
    parts: list[str] = []
    for child in token.children or []:
        if child.type == "image":
            parts.append(inline_text(child))
        elif child.type in ("text", "code_inline"):
            parts.append(child.content)
        elif child.type in ("softbreak", "hardbreak"):
            parts.append(" ")
        # strong_open, em_open, link_open, ...: formatting only, no text of their own
    return " ".join("".join(parts).split())


@dataclass
class _List:
    ordered: bool
    marker: str = ""
    """The marker of the item being read, until its first paragraph takes it."""


@dataclass
class _Walker:
    blocks: list[Block] = field(default_factory=list)
    headings: list[tuple[int, str]] = field(default_factory=list)  # (level, text), outermost first
    lists: list[_List] = field(default_factory=list)  # innermost last
    rows: list[tuple[str, ...]] | None = None  # set while inside a table
    row: list[str] = field(default_factory=list)
    context: list[str] = field(default_factory=list)  # what the next inline token belongs to

    @property
    def section(self) -> tuple[str, ...]:
        return tuple(text for _, text in self.headings)

    def walk(self, tokens: list[Token]) -> list[Block]:
        for token in tokens:
            self._token(token)
        return self.blocks

    def _token(self, token: Token) -> None:
        kind = token.type
        if kind == "heading_open":
            self.context.append(token.tag)  # "h1" ... "h6"
        elif kind in ("paragraph_open", "th_open", "td_open"):
            self.context.append(kind)
        elif kind in ("heading_close", "paragraph_close", "th_close", "td_close"):
            self.context.pop()
        elif kind == "inline":
            self._inline(token)
        elif kind in ("bullet_list_open", "ordered_list_open"):
            self.lists.append(_List(ordered=kind == "ordered_list_open"))
        elif kind in ("bullet_list_close", "ordered_list_close"):
            self.lists.pop()
        elif kind == "list_item_open":
            current = self.lists[-1]
            # an ordered item's info is its number as written ("3"), markup its "." or ")"
            current.marker = f"{token.info}{token.markup}" if current.ordered else "-"
        elif kind == "list_item_close":
            self.lists[-1].marker = ""
        elif kind == "table_open":
            self.rows = []
        elif kind == "tr_open":
            self.row = []
        elif kind == "tr_close" and self.rows is not None:
            self.rows.append(tuple(self.row))
        elif kind == "table_close":
            self._table()
        elif kind in ("fence", "code_block"):
            self._add(BlockKind.CODE, token.content.rstrip("\n"))
        # hr, blockquote_open/close, thead/tbody: no text of their own

    def _inline(self, token: Token) -> None:
        text = inline_text(token)
        where = self.context[-1] if self.context else "paragraph_open"
        if where in ("th_open", "td_open"):
            self.row.append(text)  # an empty cell is still a cell
        elif where.startswith("h"):
            self._heading(int(where[1:]), text)
        elif self.lists:
            current = self.lists[-1]
            self._add(BlockKind.LIST_ITEM, text, level=len(self.lists), marker=current.marker)
            current.marker = ""  # a second paragraph in the same item is a continuation
        else:
            self._add(BlockKind.PARAGRAPH, text)

    def _heading(self, level: int, text: str) -> None:
        if not text:
            return
        while self.headings and self.headings[-1][0] >= level:
            self.headings.pop()
        self._add(BlockKind.HEADING, text, level=level)
        self.headings.append((level, text))

    def _table(self) -> None:
        rows = tuple(self.rows or ())
        self.rows = None
        if any(any(cells) for cells in rows):
            text = "\n".join(" | ".join(cells) for cells in rows)
            self._add(BlockKind.TABLE, text, rows=rows)

    def _add(
        self,
        kind: BlockKind,
        text: str,
        *,
        level: int = 0,
        marker: str = "",
        rows: tuple[tuple[str, ...], ...] = (),
    ) -> None:
        if text.strip():
            self.blocks.append(
                Block(
                    kind=kind,
                    text=text,
                    section=self.section,
                    level=level,
                    marker=marker,
                    rows=rows,
                )
            )
