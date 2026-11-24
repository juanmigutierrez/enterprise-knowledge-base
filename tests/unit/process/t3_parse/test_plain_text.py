"""The plain-text baseline: words and paragraphs, no structure."""

from __future__ import annotations

import pytest

from ekb.contracts import BlockKind
from ekb.process.t3_parse.options.plain_text import PlainTextParser
from ekb.process.t3_parse.port import ParseError
from tests.unit.process.t3_parse.conftest import source_file

PARSER = PlainTextParser()


def test_paragraphs_split_at_blank_lines_and_markdown_stays() -> None:
    body = "# Policy\n\n## Booking\nAsk **early**.\n\n- one\n- two\n"
    blocks = PARSER.parse(source_file(body)).blocks
    assert [b.text for b in blocks] == ["# Policy", "## Booking\nAsk **early**.", "- one\n- two"]
    assert {b.kind for b in blocks} == {BlockKind.PARAGRAPH}
    assert all(b.section == () for b in blocks)  # no block knows its section


def test_windows_line_endings_and_whitespace_only_lines_count_as_blank() -> None:
    blocks = PARSER.parse(source_file("one\r\n \t\r\ntwo\rthree\n")).blocks
    assert [b.text for b in blocks] == ["one", "two\nthree"]


def test_an_empty_body_gives_no_blocks() -> None:
    assert PARSER.parse(source_file("\n \n")).blocks == ()


def test_its_parser_id_and_errors() -> None:
    assert PARSER.parse(source_file("x")).parser_id == "plain_text@1"
    with pytest.raises(ParseError, match="not UTF-8"):
        PARSER.parse(source_file(b"\xff"))
