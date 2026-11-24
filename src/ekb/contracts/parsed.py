"""T3 contract: a document as clean text with its structure kept.

T3 produces it; T4 (S6) splits it into chunks, and the inspector shows it. It
lives here, with the other shared models, because those readers sit in
layers that may not import ``ekb.process``.

A parsed document is a **flat list of blocks** in reading order. Each block
knows its ``section``: the headings it sits under, outermost first. For the
annual leave policy, the step "Request leave in the HR portal ..." is a list
item with ``section = ("Annual leave policy", "Booking leave")``.

Why flat, with the trail on every block, rather than a nested tree:

- **T4 can split anywhere and still say where a passage came from.** A chunk
  made of blocks from one section inherits that section's trail, which is
  what its context header needs ("Annual leave policy > Booking leave").
- **Tables stay whole.** A table is one block, with its cells kept as rows,
  so a chunker never cuts a row in half or separates a number from its
  column name.
- **Easy to read and test.** One block per line, in order.

Inline formatting (``**25 working days**``, ``code`` marks, link targets) is
removed: what is left is the text a reader sees.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, model_validator


class BlockKind(StrEnum):
    """What a block is in the document's structure."""

    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST_ITEM = "list_item"
    TABLE = "table"
    CODE = "code"


class Block(BaseModel):
    """One structural unit of a document, in reading order."""

    model_config = ConfigDict(frozen=True)

    kind: BlockKind
    text: str
    """Clean text. For a table: one line per row, cells separated by `` | ``."""
    section: tuple[str, ...] = ()
    """The headings above this block, outermost first. A heading's own text is not in it."""
    level: int = 0
    """Heading: 1-6. List item: nesting depth, 1 for a top-level item. Otherwise 0."""
    marker: str = ""
    """List item: ``-`` for a bullet, ``3.`` for the third step of a numbered list."""
    rows: tuple[tuple[str, ...], ...] = ()
    """Table: its cells, header row first."""

    @model_validator(mode="after")
    def _check_shape(self) -> Self:
        if not self.text.strip():
            raise ValueError("a block must have text: empty blocks are dropped by the parser")
        if self.kind is BlockKind.HEADING and not 1 <= self.level <= 6:
            raise ValueError("a heading's level is 1 to 6")
        if self.kind is BlockKind.LIST_ITEM and self.level < 1:
            raise ValueError("a list item's level is its nesting depth, from 1")
        if (self.kind is BlockKind.TABLE) != bool(self.rows):
            raise ValueError("a table has rows, and only a table has rows")
        return self


class ParsedDocument(BaseModel):
    """One version of one document, parsed. Same original and parser: same result."""

    model_config = ConfigDict(frozen=True)

    document_key: str
    version: str
    """The version of the original that was parsed; chunks keep it as ``document_version``."""
    title: str
    language: str | None = None
    media_type: str
    """What the parser read, e.g. ``text/markdown``."""
    parser_id: str
    """Which parser, and which version of it, produced this. Like T7's model ID: a
    parser change is visible, so re-parsing can be planned instead of mixed."""
    blocks: tuple[Block, ...] = ()

    @property
    def text(self) -> str:
        """The whole document as clean text: blocks in order, list items kept together."""
        lines: list[str] = []
        previous: Block | None = None
        for block in self.blocks:
            together = (
                previous is not None
                and previous.kind is BlockKind.LIST_ITEM
                and block.kind is BlockKind.LIST_ITEM
            )
            if previous is not None and not together:
                lines.append("")
            lines.append(_line(block))
            previous = block
        return "\n".join(lines)


def _line(block: Block) -> str:
    if block.kind is BlockKind.LIST_ITEM:
        indent = "  " * (block.level - 1)
        return f"{indent}{block.marker} {block.text}" if block.marker else f"{indent}  {block.text}"
    return block.text
