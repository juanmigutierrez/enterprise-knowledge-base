"""Shared passage quoting for the offline writer and model fallback."""

from __future__ import annotations

import re
from collections.abc import Sequence

from ekb.access.answer.port import NOTHING_FOUND, Draft
from ekb.contracts import Passage

VERSION = 1
MAX_QUOTES = 3
MAX_WORDS = 40
INTRO = "From the HR documents you can read:"

_LIST_MARK = re.compile(r"^\s*(?:[-*+]\s+(?:\[[ xX]\]\s+)?|\d+[.)]\s+|#+\s+)")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def opening(passage: Passage, max_words: int = MAX_WORDS) -> str:
    """The passage's first sentence, without its heading or list markers."""
    headings = {h.casefold() for h in (*passage.citation.section, passage.citation.title)}
    lines = [_LIST_MARK.sub("", line).strip() for line in passage.text.splitlines()]
    body = [line for line in lines if line and line.casefold() not in headings]
    text = " ".join(" ".join(body).split())
    sentence = _SENTENCE_END.split(text, maxsplit=1)[0] if text else ""
    words = sentence.split()
    if len(words) > max_words:
        return " ".join(words[:max_words]) + " …"
    return sentence


class PassageQuoter:
    def __init__(self, *, max_quotes: int = MAX_QUOTES, max_words: int = MAX_WORDS) -> None:
        self._max_quotes = max_quotes
        self._max_words = max_words

    @property
    def writer_id(self) -> str:
        return f"extractive@{VERSION}(quotes={self._max_quotes})"

    def write(self, question: str, passages: Sequence[Passage]) -> Draft:
        lines: list[str] = []
        cited: list[int] = []
        quoted: set[str] = set()
        for passage in passages:
            if len(cited) == self._max_quotes:
                break
            quote = opening(passage, self._max_words)
            if not quote or quote.casefold() in quoted:
                continue
            quoted.add(quote.casefold())
            number = passage.citation.number
            lines.append(f"- {quote} [{number}]")
            cited.append(number)
        if not lines:
            return Draft(text=NOTHING_FOUND, cited=(), writer_id=self.writer_id)
        return Draft(text="\n".join([INTRO, *lines]), cited=tuple(cited), writer_id=self.writer_id)
