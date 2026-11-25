"""Ports for the HR assistant's answer: the writer and the answer cache (S15).

**The writer** (:class:`AnswerWriter`) turns a question and the passages Q4
released into a short answer whose claims point at those passages with ``[n]``.
It is only ever called with released passages, and never with none: an answer
with nothing to stand on is a fixed sentence, written without a model
(:data:`NOTHING_FOUND`). Whatever it writes, the citations it may use are the
passages' own numbers, nothing else (:func:`cited_numbers`).

**The cache** (:class:`AnswerCache`) keeps an answer with everything needed to
check it again: the hits Q4 checked, what it released, the source's answers,
and whether the asker's readable index is still the one it was written from.
It does not decide reuse: ``ekb.app.assistant`` asks Q4 again first.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from ekb.contracts import Hit, Passage, Scope, SourceAccess

NOTHING_FOUND = "I found nothing about this in the HR documents you can read."
"""The answer when no passage was released. It never says why: "not allowed" would tell
the person that something they may not read exists."""

_MARKER = re.compile(r"\[([0-9]+)\]")


def cited_numbers(text: str, allowed: Sequence[int]) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """The ``[n]`` markers in ``text``: those in ``allowed``, in first-use order, and the others."""
    seen = tuple(dict.fromkeys(int(n) for n in _MARKER.findall(text)))
    known = set(allowed)
    return tuple(n for n in seen if n in known), tuple(n for n in seen if n not in known)


def drop_markers(text: str, numbers: Sequence[int]) -> str:
    """``text`` without the ``[n]`` markers for ``numbers`` (citations that point nowhere)."""
    unknown = set(numbers)
    cleaned = _MARKER.sub(lambda m: "" if int(m.group(1)) in unknown else m.group(0), text)
    return re.sub(r"[ \t]+([.,;:!?])", r"\1", re.sub(r"[ \t]{2,}", " ", cleaned)).strip()


@dataclass(frozen=True)
class Draft:
    """A written answer: the text, the passage numbers it cites, and who wrote it."""

    text: str
    cited: tuple[int, ...]
    """Passage numbers the text refers to with ``[n]``, in first-use order."""
    writer_id: str
    notes: tuple[str, ...] = ()
    """Agent-safe, e.g. ``the answer model did not answer: quoted the passages instead``."""
    fallback: bool = False
    """Written by the stand-in because the configured writer failed: never cached."""


@runtime_checkable
class AnswerWriter(Protocol):
    """Writes the answer from released passages only."""

    @property
    def writer_id(self) -> str:
        """The option and its version, e.g. ``model@1(answer@1)``."""
        ...

    def write(self, question: str, passages: Sequence[Passage]) -> Draft: ...


@dataclass(frozen=True)
class CachedAnswer:
    """One answer, kept with everything a reuse must check again."""

    question: str
    scope: Scope
    """Who it was written for: their principals then."""
    hits: tuple[Hit, ...]
    """What Q4 checked: asked again, they must release exactly :attr:`passages`."""
    passages: tuple[Passage, ...]
    checked: tuple[SourceAccess, ...]
    """The source's answers then, one per document: their content hashes must not change."""
    draft: Draft
    searches: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    options: tuple[str, ...] = ()
    still_current: Callable[[], bool] = field(default=lambda: False, compare=False)
    """Whether the asker's readable index is still the one the answer was written from."""


@runtime_checkable
class AnswerCache(Protocol):
    """Answers kept for reuse. It keeps and forgets; it never decides that one is still true."""

    @property
    def cache_id(self) -> str: ...

    def get(self, key: str) -> CachedAnswer | None: ...

    def put(self, key: str, entry: CachedAnswer) -> None: ...

    def drop(self, key: str) -> None: ...

    def size(self) -> int: ...
