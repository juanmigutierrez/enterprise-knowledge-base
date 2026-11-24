"""T4 along the document's structure: one chunk per section (card 4.2's starting choice).

HR policies are written in sections, and a section is usually one topic:
"Booking leave", "Carrying leave over". Cutting there keeps each chunk about
one thing, so a question about carrying leave over matches one chunk well
instead of two chunks partly.

The rules, in order:

1. **A chunk never crosses a section.** The blocks under the same heading
   form a section; the next heading starts a new one. Its chunks keep the
   section's trail (``("Annual leave policy", "Carrying leave over")``), which
   S8's context header and the inspector use.
2. **The heading is the chunk's first line.** It is text a reader sees, and
   the fixed baseline contains it too. A heading with no text of its own under it (a page
   title followed directly by a subsection) gives no chunk: it lives on in the
   trail of the chunks below it.
3. **A section longer than ``max_tokens`` is split between blocks, never
   inside one.** Blocks are added in order while they fit; consecutive list
   items stay together whenever they fit. A continuation chunk keeps the
   section's trail but does not repeat the heading line.
4. **A table, or any single block, is never split.** One larger than
   ``max_tokens`` becomes a chunk of its own, over the limit: a table row cut
   from its header row no longer says which number is the maximum. The step
   counts these chunks so they are visible.
5. **Small sections are not merged.** A 17-token section stays its own chunk,
   so every chunk has exactly one section. Merging is a possible later
   setting, measured by card 4.2.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from itertools import groupby

from ekb.contracts import Block, BlockKind, Chunk, ParsedDocument, render_blocks
from ekb.platform.registry import register
from ekb.process.t4_chunk.port import new_chunk, positive
from ekb.process.t4_chunk.tokens import count_tokens

Section = tuple[tuple[str, ...], list[Block]]


@register("t4_chunk", "structure_aware")
class StructureAwareChunker:
    def __init__(self, max_tokens: int = 400) -> None:
        self._max_tokens = positive(max_tokens, "max_tokens")

    @property
    def chunker_id(self) -> str:
        return f"structure_aware@1(max_tokens={self._max_tokens})"

    @property
    def max_tokens(self) -> int:
        return self._max_tokens

    def chunk(self, document: ParsedDocument) -> tuple[Chunk, ...]:
        pieces = [
            (trail, part)
            for trail, blocks in sections(document.blocks)
            for part in self._fit(blocks)
        ]
        return tuple(
            new_chunk(document, position, render_blocks(part), trail, self.chunker_id)
            for position, (trail, part) in enumerate(pieces)
        )

    def _fit(self, blocks: list[Block]) -> Iterator[list[Block]]:
        """Split one section between blocks so each part stays within ``max_tokens``.

        A part is closed only once it holds some text: a heading is never left
        alone, and a block too big to fit anywhere gets a part of its own.
        """
        part: list[Block] = []
        size = 0
        for is_list, group in groupby(blocks, key=lambda b: b.kind is BlockKind.LIST_ITEM):
            items = [(block, count_tokens(render_blocks([block]))) for block in group]
            group_size = sum(n for _, n in items)
            if is_list and group_size <= self._max_tokens < size + group_size and _has_text(part):
                yield part
                part, size = [], 0
            for block, tokens in items:
                if size + tokens > self._max_tokens and _has_text(part):
                    yield part
                    part, size = [], 0
                part.append(block)
                size += tokens
        if part:
            yield part


def sections(blocks: Sequence[Block]) -> Iterator[Section]:
    """The document's sections in order: each one's trail and its blocks, heading first.

    A heading opens a section whose trail is the headings above it plus its
    own text. Text before any heading forms a section with an empty trail.
    Sections with nothing but their heading are left out.
    """
    trail: tuple[str, ...] | None = None
    group: list[Block] = []
    for block in blocks:
        if block.kind is BlockKind.HEADING:
            if _has_text(group) and trail is not None:
                yield trail, group
            trail, group = (*block.section, block.text), [block]
        elif block.section != trail:
            if _has_text(group) and trail is not None:
                yield trail, group
            trail, group = block.section, [block]
        else:
            group.append(block)
    if _has_text(group) and trail is not None:
        yield trail, group


def _has_text(blocks: Sequence[Block]) -> bool:
    return any(block.kind is not BlockKind.HEADING for block in blocks)
