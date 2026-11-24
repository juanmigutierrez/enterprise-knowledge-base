"""Builders for T4 tests: parsed documents, from Markdown or block by block."""

from __future__ import annotations

from sources.mock_docs.seed import load_pages

from ekb.contracts import Block, BlockKind, ParsedDocument
from ekb.process.t3_parse.options.markdown import MarkdownParser
from ekb.process.t3_parse.port import MARKDOWN, SourceFile
from tests.conftest import REPO_ROOT

KEY = "hr_docs:HR-PL-01"
PAGES = load_pages(REPO_ROOT / "sources" / "synthetic_data" / "hr_docs")


def parsed(
    body: str, key: str = KEY, version: str = "1.1", title: str = "Policy"
) -> ParsedDocument:
    """A document as T3 parses it: the realistic input, headings and sections included."""
    file = SourceFile(key, version, title, "en", MARKDOWN, body.encode())
    return MarkdownParser().parse(file)


def page(page_id: str) -> ParsedDocument:
    """One of the twelve synthetic pages, parsed."""
    (found,) = [p for p in PAGES if p.id == page_id]
    return parsed(found.body, key=f"hr_docs:{found.id}", title=found.title)


def document(*blocks: Block, key: str = KEY) -> ParsedDocument:
    """A document built block by block, for shapes Markdown can't easily make."""
    return ParsedDocument(
        document_key=key,
        version="1.1",
        title="Policy",
        media_type=MARKDOWN,
        parser_id="test@1",
        blocks=blocks,
    )


def paragraph(text: str, *section: str) -> Block:
    return Block(kind=BlockKind.PARAGRAPH, text=text, section=section)


def words(n: int, word: str = "word") -> str:
    """``n`` tokens of text: ``word word word ...``."""
    return " ".join([word] * n)
