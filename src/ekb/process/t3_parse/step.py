"""Running T3 for one change event: read the original, open it, parse it.

::

    original = rawstore.get(event.raw_pointer)       # the exact bytes the event saw
    file     = reader(key, version, original)        # the source's envelope -> the file
    parsed   = parser.parse(file)                    # the option: Markdown, plain text ...

T3 runs only where T2 decided ``process`` (S12 wires it): an upsert whose
content is new or changed. Skips and permission-only changes never get here,
which is where T2's savings come from.

**It reads the raw store, not the source.** The source may have changed again
since the event; the stored original is exactly what this event's version
was, so T3 parses the version the event names.

**Records are not parsed.** A source with no original reader holds structured
records (HR records, database rows, identity users). T3 says so and returns
no document; those items go to the entity store (S9) and the metrics store
(S14b), never to chunks and vectors (D7).

**Errors are not skipped.** A missing original or one that cannot be parsed
raises: the event is not committed and comes back (the rule of S4). Sending
such events aside after a few tries (a dead-letter queue) is the pipeline's
job (S12).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass

from ekb.changelog.rawstore.port import RawStore
from ekb.contracts import ChangeEvent, ChangeOp, ParsedDocument
from ekb.process.t3_parse.originals import OriginalReader
from ekb.process.t3_parse.port import Parser


@dataclass(frozen=True)
class ParseResult:
    event: ChangeEvent
    parsed: ParsedDocument | None
    """``None`` when the item is not a document (a structured record)."""
    reason: str


def summary(parsed: ParsedDocument) -> str:
    """``14 blocks: 5 heading, 3 paragraph, 6 list_item``."""
    kinds = Counter(block.kind.value for block in parsed.blocks)
    detail = ", ".join(f"{n} {kind}" for kind, n in kinds.items())
    return f"{len(parsed.blocks)} blocks" + (f": {detail}" if detail else "")


class ParseStep:
    """T3 over the raw store, with one original reader per document source."""

    def __init__(
        self, parser: Parser, rawstore: RawStore, readers: Mapping[str, OriginalReader]
    ) -> None:
        self._parser = parser
        self._rawstore = rawstore
        self._readers = dict(readers)

    @property
    def parser_id(self) -> str:
        return self._parser.parser_id

    def handles(self, source: str) -> bool:
        """Whether ``source`` holds documents T3 parses."""
        return source in self._readers

    def handle(self, event: ChangeEvent) -> ParseResult:
        if event.op is not ChangeOp.UPSERT or event.raw_pointer is None:
            raise ValueError(
                f"T3 parses upserts only, not {event.op.value}: "
                "only an upsert points to an original"
            )
        reader = self._readers.get(event.source)
        if reader is None:
            return ParseResult(
                event,
                None,
                f"not a document: {event.source} holds structured records, queried not parsed (D7)",
            )
        original = self._rawstore.get(event.raw_pointer)
        parsed = self._parser.parse(reader(event.document_key, event.version, original))
        return ParseResult(event, parsed, f"parsed with {parsed.parser_id}: {summary(parsed)}")
