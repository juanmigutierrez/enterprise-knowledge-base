"""The approximate token counter: words and punctuation marks, the same everywhere."""

from __future__ import annotations

import pytest

from ekb.process.t4_chunk.tokens import count_tokens, token_spans


@pytest.mark.parametrize(
    ("text", "tokens"),
    [
        ("", 0),
        ("   \n\t ", 0),
        ("leave", 1),
        ("25 working days", 3),
        ("32,000 EUR", 4),  # 32 , 000 EUR
        ("E2 | 40,000", 5),  # E2 | 40 , 000
        ("perché è così", 3),  # accented letters are letters
        ("Carried-over days", 4),  # Carried - over days
    ],
)
def test_a_token_is_a_word_or_a_punctuation_mark(text: str, tokens: int) -> None:
    assert count_tokens(text) == tokens


def test_spans_point_into_the_text_so_a_window_keeps_its_line_breaks() -> None:
    text = "Booking leave\n\n1. Request leave."
    spans = token_spans(text)
    assert [text[a:b] for a, b in spans] == [
        "Booking",
        "leave",
        "1",
        ".",
        "Request",
        "leave",
        ".",
    ]
    assert text[spans[1][0] : spans[3][1]] == "leave\n\n1."
