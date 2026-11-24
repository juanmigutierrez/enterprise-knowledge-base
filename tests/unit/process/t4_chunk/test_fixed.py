"""The fixed-window baseline: windows of tokens with an overlap, blind to structure."""

from __future__ import annotations

from itertools import pairwise

import pytest

from ekb.process.t4_chunk.options.fixed import DEFAULT_OVERLAP, FixedChunker
from ekb.process.t4_chunk.tokens import token_spans
from tests.unit.process.t4_chunk.conftest import PAGES, document, page, paragraph, parsed

NUMBERED = " ".join(f"t{n}" for n in range(100))  # 100 tokens, each one different


def _tokens(text: str) -> list[str]:
    return [text[a:b] for a, b in token_spans(text)]


def test_windows_hold_max_tokens_and_share_exactly_the_overlap() -> None:
    chunks = FixedChunker(max_tokens=40, overlap_tokens=10).chunk(document(paragraph(NUMBERED)))
    windows = [_tokens(c.text) for c in chunks]
    assert [len(w) for w in windows] == [40, 40, 40]  # 0-39, 30-69, 60-99
    assert windows[0][0] == "t0"
    assert windows[-1][-1] == "t99"  # the end is covered, and the last window isn't empty
    for before, after in pairwise(windows):
        assert before[-10:] == after[:10]
    assert [c.tokens for c in chunks] == [40, 40, 40]


def test_no_overlap_cuts_back_to_back() -> None:
    chunks = FixedChunker(max_tokens=50, overlap_tokens=0).chunk(document(paragraph(NUMBERED)))
    assert [_tokens(c.text)[0] for c in chunks] == ["t0", "t50"]


def test_windows_are_cut_out_of_the_clean_text_keeping_line_breaks() -> None:
    doc = parsed("# Leave\n\n## Booking\n1. Ask early.\n2. Wait.\n")
    (only,) = FixedChunker().chunk(doc)
    assert only.text == doc.text == "Leave\n\nBooking\n\n1. Ask early.\n2. Wait."
    small = FixedChunker(max_tokens=5, overlap_tokens=1).chunk(doc)
    assert all(c.text in doc.text for c in small)
    assert small[0].text == "Leave\n\nBooking\n\n1. Ask"  # cut wherever the count says


def test_its_chunks_have_no_section_on_purpose() -> None:
    chunks = FixedChunker(max_tokens=5, overlap_tokens=0).chunk(page("HR-PL-01"))
    assert all(c.section == () for c in chunks)


def test_a_short_page_is_one_chunk_at_the_default_size() -> None:
    """Every synthetic page is shorter than 400 tokens: the baseline doesn't cut them."""
    for synthetic in PAGES:
        (whole,) = FixedChunker().chunk(page(synthetic.id))
        assert whole.text == page(synthetic.id).text


def test_a_document_with_no_text_gives_no_chunks() -> None:
    assert FixedChunker().chunk(document()) == ()


def test_its_id_names_the_option_version_and_settings() -> None:
    chunker = FixedChunker()
    assert chunker.chunker_id == f"fixed@1(max_tokens=400,overlap_tokens={DEFAULT_OVERLAP})"
    assert (chunker.max_tokens, chunker.overlap_tokens) == (400, 50)


@pytest.mark.parametrize(
    ("max_tokens", "overlap", "problem"),
    [
        (0, 0, "max_tokens must be at least 1"),
        (50, 50, "overlap_tokens must be at least 0 and less than max_tokens"),
        (50, 60, "less than max_tokens"),
        (50, -1, "at least 0"),
    ],
)
def test_settings_that_would_never_move_forward_are_refused(
    max_tokens: int, overlap: int, problem: str
) -> None:
    with pytest.raises(ValueError, match=problem):
        FixedChunker(max_tokens=max_tokens, overlap_tokens=overlap)
