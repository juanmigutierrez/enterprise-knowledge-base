"""Port for T3 · Parse. Every parsing option implements :class:`Parser`.

T3 turns a document's file into clean text with its structure kept: headings,
paragraphs, list items, tables (a :class:`~ekb.contracts.ParsedDocument`).
Structure matters because T4 (S6) cuts chunks along it: a chunk that knows it
belongs to "Annual leave policy > Booking leave" is found for the right
questions, and a table kept whole still says which number is the maximum.

A parser reads a :class:`SourceFile`: the file plus the little it needs to
know about it (key, version, title, language, media type). Getting the file
out of what a source stored is not the parser's job: the docs tool stores a
JSON page with the Markdown body inside, a SharePoint connector would store
the ``.docx`` itself. ``originals.py`` opens those envelopes, so every parser
(Markdown today, Docling later) works on the same input.

``parser_id`` is stored on every result, like T7's model ID: it says which
parser and which version of it produced the blocks, so a parser upgrade can be
planned as a re-parse instead of leaving two kinds of output side by side.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic.dataclasses import dataclass

from ekb.contracts import ParsedDocument

MARKDOWN = "text/markdown"
PLAIN_TEXT = "text/plain"


class ParseError(ValueError):
    """The original cannot be read as a document: not the expected format, or not text."""


@dataclass(frozen=True)
class SourceFile:
    """What a parser reads: one version of one document's file."""

    document_key: str
    version: str
    title: str
    language: str | None
    media_type: str
    content: bytes
    """The file's bytes. Text formats are UTF-8; a PDF parser (later) reads them as they are."""
    department: str | None = None
    """The source's own department label, if it has one. Passed through to the result."""


@runtime_checkable
class Parser(Protocol):
    """Turns a file into a :class:`~ekb.contracts.ParsedDocument`. Pure: no I/O."""

    @property
    def parser_id(self) -> str: ...

    def parse(self, source: SourceFile) -> ParsedDocument:
        """Raises :class:`ParseError` if the file is not in a format this parser reads."""
        ...


def decode_text(source: SourceFile, accepted: frozenset[str]) -> str:
    """The file as text, for parsers of text formats. Shared so every option fails the same way."""
    if source.media_type not in accepted:
        raise ParseError(
            f"{source.document_key} v{source.version}: cannot parse {source.media_type!r} "
            f"(reads {', '.join(sorted(accepted))})"
        )
    try:
        return source.content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ParseError(f"{source.document_key} v{source.version}: not UTF-8 text") from exc
