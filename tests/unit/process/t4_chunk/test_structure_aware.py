"""The structure-aware option: one chunk per section, split between blocks, nothing lost."""

from __future__ import annotations

import pytest
from sources.mock_docs.models import PageCreate

from ekb.contracts import Block, BlockKind
from ekb.process.t4_chunk.options.structure_aware import StructureAwareChunker
from ekb.process.t4_chunk.tokens import count_tokens
from tests.unit.process.t4_chunk.conftest import (
    PAGES,
    document,
    page,
    paragraph,
    parsed,
    words,
)

CHUNKER = StructureAwareChunker()

LEAVE = """# Annual leave policy

## How much leave you get
- Full-time employees get **25 working days**.
- Part-time employees get the same, in proportion.

## Booking leave
1. Request leave in the HR portal.
2. Your manager approves within three working days.

## Carrying leave over
You may carry up to 5 unused days.
"""


def test_each_section_is_one_chunk_with_its_heading_first_and_its_trail() -> None:
    chunks = CHUNKER.chunk(parsed(LEAVE))
    assert [c.section for c in chunks] == [
        ("Annual leave policy", "How much leave you get"),
        ("Annual leave policy", "Booking leave"),
        ("Annual leave policy", "Carrying leave over"),
    ]
    assert chunks[1].text == (
        "Booking leave\n\n"
        "1. Request leave in the HR portal.\n"
        "2. Your manager approves within three working days."
    )
    assert [c.chunk_id for c in chunks] == [f"hr_docs:HR-PL-01#{n}" for n in range(3)]
    assert [c.position for c in chunks] == [0, 1, 2]
    assert all(c.tokens == count_tokens(c.text) for c in chunks)
    assert {(c.document_key, c.document_version) for c in chunks} == {("hr_docs:HR-PL-01", "1.1")}


def test_a_heading_with_no_text_of_its_own_gives_no_chunk_but_stays_in_the_trails() -> None:
    chunks = CHUNKER.chunk(parsed(LEAVE))
    assert not any("Annual leave policy" in c.text for c in chunks)
    assert all(c.section[0] == "Annual leave policy" for c in chunks)


def test_a_title_with_text_under_it_is_a_chunk_of_its_own() -> None:
    (intro, rule) = CHUNKER.chunk(parsed("# Salary bands\n\nRestricted.\n\n## Review\nIn April.\n"))
    assert (intro.section, intro.text) == (("Salary bands",), "Salary bands\n\nRestricted.")
    assert (rule.section, rule.text) == (("Salary bands", "Review"), "Review\n\nIn April.")


def test_text_before_any_heading_forms_a_chunk_with_no_section() -> None:
    (intro, rule) = CHUNKER.chunk(parsed("Read this first.\n\n# Rule\nBe kind.\n"))
    assert (intro.section, intro.text) == ((), "Read this first.")
    assert rule.section == ("Rule",)


def test_nested_sections_get_their_own_chunks_and_full_trails() -> None:
    body = (
        "# Travel\n\n## Flights\nEconomy.\n\n"
        "### Long haul\nPremium economy.\n\n## Hotels\nFour stars.\n"
    )
    chunks = CHUNKER.chunk(parsed(body))
    assert [c.section for c in chunks] == [
        ("Travel", "Flights"),
        ("Travel", "Flights", "Long haul"),
        ("Travel", "Hotels"),
    ]


def test_blocks_that_change_section_without_a_heading_never_share_a_chunk() -> None:
    """A parser may give sections without heading blocks (Docling might): the rule holds."""
    doc = document(
        paragraph("Two days.", "Remote", "Rule"), paragraph("A laptop.", "Remote", "Kit")
    )
    chunks = CHUNKER.chunk(doc)
    assert [(c.section, c.text) for c in chunks] == [
        (("Remote", "Rule"), "Two days."),
        (("Remote", "Kit"), "A laptop."),
    ]


def test_small_sections_are_not_merged() -> None:
    chunks = CHUNKER.chunk(parsed("# A\n\n## B\nOne.\n\n## C\nTwo.\n"))
    assert [c.text for c in chunks] == ["B\n\nOne.", "C\n\nTwo."]


def test_a_long_section_is_split_between_blocks_never_inside_one() -> None:
    blocks = [paragraph(words(30, f"p{n}"), "Policy", "Rules") for n in range(5)]
    heading = Block(kind=BlockKind.HEADING, text="Rules", section=("Policy",), level=2)
    chunker = StructureAwareChunker(max_tokens=70)
    chunks = chunker.chunk(document(heading, *blocks))
    # 1 + 30 + 30 = 61 tokens fit; a third paragraph would make 91.
    assert [c.tokens for c in chunks] == [61, 60, 30]
    assert all(c.section == ("Policy", "Rules") for c in chunks)
    assert chunks[0].text.startswith("Rules\n\n")
    assert not chunks[1].text.startswith("Rules")  # the heading is not repeated
    texts = "\n\n".join(c.text for c in chunks)
    assert all(b.text in texts for b in blocks)  # every paragraph whole, exactly once
    assert all(texts.count(b.text) == 1 for b in blocks)


def test_a_list_stays_together_when_it_fits() -> None:
    intro = words(40, "intro")
    body = f"# Policy\n\n## Steps\n{intro}\n\n1. One step.\n2. Two step.\n3. Three step.\n"
    chunks = StructureAwareChunker(max_tokens=45).chunk(parsed(body))
    assert [c.text.splitlines()[0] for c in chunks] == ["Steps", "1. One step."]
    assert chunks[1].text == "1. One step.\n2. Two step.\n3. Three step."


def test_a_list_moves_even_if_its_first_item_fits_the_current_chunk() -> None:
    body = "# Steps\n\nRead this policy first.\n\n- Ask HR.\n- File it."
    chunks = StructureAwareChunker(max_tokens=10).chunk(parsed(body))
    assert [c.text for c in chunks] == ["Steps\n\nRead this policy first.", "- Ask HR.\n- File it."]


def test_a_list_too_large_for_a_fresh_chunk_splits_between_items() -> None:
    body = "# Steps\n\n- Ask HR.\n- File it.\n- Tell HR."
    chunks = StructureAwareChunker(max_tokens=9).chunk(parsed(body))
    assert [c.text for c in chunks] == ["Steps\n\n- Ask HR.\n- File it.", "- Tell HR."]


def test_a_table_larger_than_the_limit_stays_whole_with_its_heading() -> None:
    rows = "\n".join(f"| E{n} | {n},000 | {n + 1},000 |" for n in range(1, 9))
    body = (
        f"# Bands\n\n## Italy\n| Level | Min | Max |\n|---|---|---|\n{rows}\n\n## Review\nApril.\n"
    )
    chunker = StructureAwareChunker(max_tokens=20)
    table, review = chunker.chunk(parsed(body))
    assert table.text.startswith("Italy\n\nLevel | Min | Max\nE1 | 1,000 | 2,000")
    assert table.text.endswith("E8 | 8,000 | 9,000")
    assert table.tokens > chunker.max_tokens  # over the limit rather than cut
    assert review.tokens <= chunker.max_tokens


def test_a_document_without_structure_is_split_by_size_only() -> None:
    """What the plain_text parser gives: paragraphs, no headings, no sections."""
    doc = document(*(paragraph(words(10, f"w{n}")) for n in range(4)))
    chunks = StructureAwareChunker(max_tokens=25).chunk(doc)
    assert [c.tokens for c in chunks] == [20, 20]
    assert all(c.section == () for c in chunks)


def test_a_document_with_no_text_gives_no_chunks() -> None:
    assert CHUNKER.chunk(document()) == ()
    assert CHUNKER.chunk(parsed("# Only a title\n")) == ()


def test_the_same_document_always_gives_the_same_chunks() -> None:
    assert CHUNKER.chunk(parsed(LEAVE)) == CHUNKER.chunk(parsed(LEAVE))


def test_its_id_names_the_option_version_and_limit() -> None:
    assert CHUNKER.chunker_id == "structure_aware@1(max_tokens=400)"
    assert StructureAwareChunker(max_tokens=120).max_tokens == 120
    assert {c.chunker_id for c in CHUNKER.chunk(parsed(LEAVE))} == {CHUNKER.chunker_id}


@pytest.mark.parametrize("max_tokens", [0, -5])
def test_a_limit_below_one_token_is_refused(max_tokens: int) -> None:
    with pytest.raises(ValueError, match="max_tokens must be at least 1"):
        StructureAwareChunker(max_tokens=max_tokens)


@pytest.mark.parametrize("synthetic", PAGES, ids=[p.id for p in PAGES])
def test_every_synthetic_page_is_cut_along_its_sections_and_loses_nothing(
    synthetic: PageCreate,
) -> None:
    doc = page(synthetic.id)
    chunks = CHUNKER.chunk(doc)
    assert chunks
    assert all(c.tokens <= CHUNKER.max_tokens for c in chunks)
    joined = "\n\n".join(c.text for c in chunks)
    for block in doc.blocks:
        if block.kind is BlockKind.HEADING:
            # a heading starts its section's chunk, or (with no text of its own) is in trails
            starts = any(c.text.splitlines()[0] == block.text for c in chunks)
            assert starts or all(block.text in c.section for c in chunks)
        else:
            assert joined.count(block.text) == 1, block.text  # every block, exactly once
    first_lines = [c.text.splitlines()[0] for c in chunks]
    assert all(c.section[-1] == first for c, first in zip(chunks, first_lines, strict=True))


def test_the_twelve_pages_give_48_chunks_and_the_salary_tables_stay_whole() -> None:
    assert sum(len(CHUNKER.chunk(page(p.id))) for p in PAGES) == 48
    italy = CHUNKER.chunk(page("HR-CMP-01"))[1]
    assert italy.section[-1] == "Engineering \N{EN DASH} Italy (gross annual, EUR)"
    assert "Level | Minimum | Midpoint | Maximum\nE1" in italy.text
    assert italy.text.endswith("E3 | 52,000 | 61,000 | 70,000")
