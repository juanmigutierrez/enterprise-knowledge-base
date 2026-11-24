"""The Markdown option: structure survives, formatting goes, and bad input fails loudly."""

from __future__ import annotations

import pytest
from sources.mock_docs.models import PageCreate
from sources.mock_docs.seed import load_pages

from ekb.contracts import Block, BlockKind
from ekb.process.t3_parse.options.markdown import MarkdownParser
from ekb.process.t3_parse.port import PLAIN_TEXT, ParseError
from tests.conftest import REPO_ROOT
from tests.unit.process.t3_parse.conftest import source_file

PARSER = MarkdownParser()


def _blocks(body: str) -> tuple[Block, ...]:
    return PARSER.parse(source_file(body)).blocks


def _shape(blocks: tuple[Block, ...]) -> list[tuple[str, str, tuple[str, ...]]]:
    return [(b.kind.value, b.text, b.section) for b in blocks]


def test_every_block_knows_the_headings_above_it() -> None:
    body = (
        "# Policy\n\nIntro.\n\n## Booking\n\nAsk early.\n\n### Long trips\n\nOne month.\n\n"
        "## Carry over\n\nFive days.\n"
    )
    assert _shape(_blocks(body)) == [
        ("heading", "Policy", ()),
        ("paragraph", "Intro.", ("Policy",)),
        ("heading", "Booking", ("Policy",)),
        ("paragraph", "Ask early.", ("Policy", "Booking")),
        ("heading", "Long trips", ("Policy", "Booking")),
        ("paragraph", "One month.", ("Policy", "Booking", "Long trips")),
        # A heading closes every section at its level or deeper.
        ("heading", "Carry over", ("Policy",)),
        ("paragraph", "Five days.", ("Policy", "Carry over")),
    ]
    assert [b.level for b in _blocks(body) if b.kind is BlockKind.HEADING] == [1, 2, 3, 2]


def test_text_before_any_heading_has_no_section() -> None:
    (block,) = _blocks("Just a note.\n")
    assert (block.kind, block.section) == (BlockKind.PARAGRAPH, ())


def test_list_items_keep_their_depth_and_marker() -> None:
    body = "* first\n  * nested\n* second\n\n3. third step\n4. fourth step\n\n1) other style\n"
    items = [(b.text, b.level, b.marker) for b in _blocks(body)]
    assert items == [
        ("first", 1, "-"),  # every bullet is "-", whatever character the author typed
        ("nested", 2, "-"),
        ("second", 1, "-"),
        ("third step", 1, "3."),  # numbered lists keep the author's numbers
        ("fourth step", 1, "4."),
        ("other style", 1, "1)"),
    ]
    assert {b.kind for b in _blocks(body)} == {BlockKind.LIST_ITEM}


def test_a_second_paragraph_in_a_list_item_continues_it() -> None:
    body = "- first point\n\n  more about it\n- second point\n"
    assert [(b.text, b.marker) for b in _blocks(body)] == [
        ("first point", "-"),
        ("more about it", ""),
        ("second point", "-"),
    ]


def test_a_table_is_one_block_with_its_cells() -> None:
    body = (
        "## Bands\n\n| Level | Max | Note |\n|---|---|---|\n| E1 | 42,000 | a \\| b |\n"
        "| E2 | 54,000 | |\n"
    )
    _, table = _blocks(body)
    assert table.kind is BlockKind.TABLE
    assert table.section == ("Bands",)
    assert table.rows == (
        ("Level", "Max", "Note"),  # header row first
        ("E1", "42,000", "a | b"),  # an escaped pipe stays inside its cell
        ("E2", "54,000", ""),  # an empty cell is still a cell
    )
    assert table.text == "Level | Max | Note\nE1 | 42,000 | a | b\nE2 | 54,000 | "


def test_inline_formatting_is_removed_and_the_words_stay() -> None:
    body = (
        "Get **25 days**, _not_ `more`; see the [HR portal](https://hr.example) "
        "and ![a chart](c.png)\nnext line.\n"
    )
    (block,) = _blocks(body)
    assert block.text == "Get 25 days, not more; see the HR portal and a chart next line."


def test_code_keeps_its_text_as_written() -> None:
    (block,) = _blocks("```yaml\nleave:\n  days: 25\n```\n")
    assert (block.kind, block.text) == (BlockKind.CODE, "leave:\n  days: 25")


def test_image_description_keeps_visible_words_without_markup_or_link_targets() -> None:
    (block,) = _blocks("![**HR** [guide](internal-target)](picture.png)")
    assert block.text == "HR guide"


def test_raw_html_is_kept_as_text_not_interpreted() -> None:
    (block,) = _blocks('<div class="x">Hidden?</div>\n')
    assert block.text == '<div class="x">Hidden?</div>'


def test_setext_headings_and_windows_line_endings() -> None:
    blocks = _blocks("Policy\r\n======\r\n\r\nLine one\r\nline two\r\n")
    assert _shape(blocks) == [
        ("heading", "Policy", ()),
        ("paragraph", "Line one line two", ("Policy",)),
    ]


@pytest.mark.parametrize("body", ["", "   \n\n", "#\n\n---\n"])
def test_a_body_with_no_text_gives_no_blocks(body: str) -> None:
    assert _blocks(body) == ()


def test_the_result_carries_the_files_metadata_and_the_parser() -> None:
    parsed = PARSER.parse(source_file("# Annual leave policy\n"))
    assert (parsed.document_key, parsed.version, parsed.title, parsed.language) == (
        "hr_docs:HR-PL-01",
        "1.1",
        "Annual leave policy",
        "en",
    )
    assert parsed.media_type == "text/markdown"
    assert parsed.parser_id.startswith("markdown-it-py@")


def test_the_same_file_always_gives_the_same_result() -> None:
    body = "# A\n\n- b\n- c\n\n| x | y |\n|---|---|\n| 1 | 2 |\n"
    assert PARSER.parse(source_file(body)) == MarkdownParser().parse(source_file(body))


def test_plain_text_files_are_read_too() -> None:
    (block,) = PARSER.parse(source_file("Hello.", media_type=PLAIN_TEXT)).blocks
    assert block.text == "Hello."


def test_bytes_that_are_not_utf8_are_an_error() -> None:
    with pytest.raises(ParseError, match="not UTF-8"):
        PARSER.parse(source_file(b"\xff\xfe caf\xe9"))


def test_a_format_it_does_not_read_is_an_error() -> None:
    with pytest.raises(ParseError, match="cannot parse 'application/pdf'"):
        PARSER.parse(source_file(b"%PDF-1.7", media_type="application/pdf"))


PAGES = load_pages(REPO_ROOT / "sources" / "synthetic_data" / "hr_docs")


@pytest.mark.parametrize("page", PAGES, ids=[p.id for p in PAGES])
def test_every_synthetic_page_parses_with_its_structure(page: PageCreate) -> None:
    parsed = PARSER.parse(source_file(page.body, title=page.title))
    first, *rest = parsed.blocks
    assert (first.kind, first.level, first.text) == (BlockKind.HEADING, 1, page.title)
    assert rest
    assert all(b.section for b in rest)  # everything below the title sits in a section
    assert not any("**" in b.text for b in parsed.blocks)  # no Markdown left in the text


def test_the_salary_bands_keep_both_tables_whole() -> None:
    (page,) = [p for p in PAGES if p.id == "HR-CMP-01"]
    tables = [b for b in PARSER.parse(source_file(page.body)).blocks if b.kind is BlockKind.TABLE]
    assert [len(t.rows) for t in tables] == [4, 4]
    assert all(len(row) == 4 for t in tables for row in t.rows)
    assert tables[0].section[-1] == "Engineering \N{EN DASH} Italy (gross annual, EUR)"
    assert tables[0].rows[2] == ("E2", "40,000", "47,000", "54,000")


def test_all_twelve_synthetic_pages_are_checked() -> None:
    assert len(PAGES) == 12
