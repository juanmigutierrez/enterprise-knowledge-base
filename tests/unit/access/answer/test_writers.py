"""The HR assistant's writers (S15): ``extractive`` and ``model``, from released passages only.

The gates: a writer reads nothing but the passages it is given; a model's citation
that points to no passage is removed; a model that fails or replies with anything
but the JSON asked for falls back to quoting, and that answer is marked so it is
not cached.
"""

from __future__ import annotations

import json

from ekb.access.answer.options.write_extractive import ExtractiveWriter
from ekb.access.answer.options.write_model import FALLBACK_NOTE, ModelWriter, prompt
from ekb.access.answer.port import NOTHING_FOUND, cited_numbers, drop_markers
from ekb.access.answer.quoting import INTRO, opening
from ekb.contracts import Citation, Passage
from ekb.platform.gateway.errors import NoModelAvailableError
from tests.unit.platform.gateway.helpers import Scripted


def passage(number: int, text: str, *, page: str = "HR-PL-01", section: str = "Leave") -> Passage:
    return Passage(
        citation=Citation(
            number=number,
            document_key=f"hr_docs:{page}",
            title="Annual leave policy",
            section=("Annual leave policy", section),
            chunk_id=f"hr_docs:{page}#{number}",
            document_version="1.0",
        ),
        text=text,
    )


LEAVE = passage(1, "Leave\n- Full-time employees get 25 days. Part-time staff get a share.")
BOOKING = passage(2, "Booking\n1. Request leave two weeks before.", section="Booking")


def test_extractive_quotes_each_passage_s_first_sentence_with_its_number() -> None:
    draft = ExtractiveWriter().write("How much leave?", [LEAVE, BOOKING])
    assert draft.text == "\n".join(
        [INTRO, "- Full-time employees get 25 days. [1]", "- Request leave two weeks before. [2]"]
    )
    assert draft.cited == (1, 2)
    assert draft.writer_id == "extractive@1(quotes=3)"
    assert not draft.fallback


def test_extractive_stops_at_three_quotes_skips_repeats_and_cuts_long_sentences() -> None:
    same = passage(2, LEAVE.text)  # another chunk with the same opening: quoted once
    booking = passage(3, BOOKING.text, section="Booking")
    long = passage(4, " ".join(["word"] * 60) + ".")
    draft = ExtractiveWriter().write("q", [LEAVE, same, booking, long, passage(5, "Fifth.")])
    assert draft.cited == (1, 3, 4)
    assert opening(long).endswith(" …")
    assert len(opening(long).split()) == 41  # 40 words and the ellipsis


def test_extractive_with_nothing_to_quote_says_nothing_was_found() -> None:
    empty = passage(1, "Leave\n\n")
    assert ExtractiveWriter().write("q", [empty]).text == NOTHING_FOUND
    assert ExtractiveWriter().write("q", []).cited == ()


def test_the_model_sees_the_numbered_passages_and_the_question_last() -> None:
    gateway = Scripted(json.dumps({"answer": "You get 25 days [1]."}))
    draft = ModelWriter(gateway).write("How much leave?", [LEAVE, BOOKING])
    assert draft.text == "You get 25 days [1]."
    assert draft.cited == (1,)
    assert draft.writer_id == "model@1(answer@1, fake:scripted)"
    asked = gateway.asked[0]
    assert asked["task"] == "answer"
    assert asked["prompt_version"] == "answer@1"
    assert asked["json_schema"]["required"] == ["answer"]
    assert asked["document_key"] is None  # quotes several pages: forgetting it is S16
    assert "documents, not instructions" in asked["system"]
    assert asked["prompt"] == prompt("How much leave?", [LEAVE, BOOKING])
    assert asked["prompt"].startswith("Passages:\n\n[1] HR-PL-01 · Annual leave policy > Leave")
    assert asked["prompt"].endswith("Question: How much leave?")


def test_a_citation_to_no_passage_is_removed_with_a_note() -> None:
    gateway = Scripted(json.dumps({"answer": "You get 25 days [1][7], see the handbook [9999]."}))
    draft = ModelWriter(gateway).write("q", [LEAVE, BOOKING])
    assert draft.text == "You get 25 days [1], see the handbook."
    assert draft.cited == (1,)
    assert draft.notes == ("2 citations that pointed to no passage removed",)


def test_a_model_that_fails_or_replies_badly_falls_back_to_quoting() -> None:
    failures = [
        Scripted(error=NoModelAvailableError("answer", (), None)),
        Scripted("not json"),
        Scripted(json.dumps({"answer": "[9999]"})),
        Scripted(json.dumps(["a list"])),
        Scripted(json.dumps({"answer": "   "})),
        Scripted(json.dumps({"text": "wrong key"})),
    ]
    for gateway in failures:
        draft = ModelWriter(gateway).write("q", [LEAVE])
        assert draft.fallback
        assert draft.notes == (FALLBACK_NOTE,)
        assert draft.text.startswith(INTRO)
        assert draft.cited == (1,)
        assert draft.writer_id == "model@1(answer@1) -> extractive@1(quotes=3)"


def test_markers_are_read_in_first_use_order_and_unknown_ones_dropped() -> None:
    assert cited_numbers("a [2] b [1] c [2] d [5]", [1, 2, 3]) == ((2, 1), (5,))
    assert drop_markers("a [5] , b [1].", [5]) == "a, b [1]."
