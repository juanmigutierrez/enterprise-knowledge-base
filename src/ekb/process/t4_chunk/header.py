"""Port for T4's context header. Every header option implements :class:`HeaderWriter`.

A chunk cut out of its document loses what was around it. ``HR-PL-01#3``, "You
may carry up to 5 unused days into the next year...", never says "annual leave"
or "policy", so a search for "can I move my vacation days?" finds little to
match. The **context header** is one or two sentences that put the chunk back in
its document ("From the Annual leave policy: carrying unused leave over to the
next year"). It is kept in ``Chunk.context_header``, apart from the text, and is
added in front of the text when the chunk is embedded and indexed (S10, S11).

A header writer reads the whole document and its chunks, and returns **one
string per chunk**, nothing else: the step (:class:`~ekb.process.t4_chunk.step.ChunkStep`)
puts them on the chunks, so a writer can never change a chunk's text, position
or anything a later step owns.

Writing a header is an enrichment, not a gate: a writer that cannot write one
(no model available, a reply that doesn't parse) returns empty headers and says
why, and the document goes on. The publication gate does not depend on it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from ekb.contracts import Chunk, ParsedDocument


class HeaderOutcome(StrEnum):
    """What happened when the headers of one document were written."""

    NONE = "none"
    """The option writes no header (``none``): every header is empty, on purpose."""
    WRITTEN = "written"
    """A model wrote them now: a request was made."""
    CACHED = "cached"
    """The gateway already had exactly this call's reply: no request."""
    FAILED = "failed"
    """No model could write them: every header is empty, ``note`` says why."""


@dataclass(frozen=True)
class Headers:
    """One document's headers, in chunk order."""

    texts: tuple[str, ...]
    """One per chunk; ``""`` where there is none."""
    header_id: str
    """What wrote them, for ``Chunk.header_id``: the option, and the model if one did."""
    outcome: HeaderOutcome
    note: str = ""
    """In plain words: who wrote them, or why there are none."""

    @property
    def missing(self) -> int:
        """Chunks left without a header."""
        return sum(1 for text in self.texts if not text)


@runtime_checkable
class HeaderWriter(Protocol):
    """Writes a context header for every chunk of one document."""

    @property
    def header_id(self) -> str:
        """The option and its version, e.g. ``model@1(header@1)``."""
        ...

    def write(self, document: ParsedDocument, chunks: tuple[Chunk, ...]) -> Headers: ...


NO_HEADER = "no header"
"""What a ``header_id`` ends with when the writer failed (``model@1(header@1, no header)``)."""


def header_failed(header_id: str) -> bool:
    """Whether chunks with this ``header_id`` were meant to get a header and did not.

    ``none`` writes no header on purpose; only a failed writer marks its ID. A
    reconciliation run (S16) re-processes such documents when a model may answer.
    """
    return header_id.endswith(f", {NO_HEADER})")


def clean(text: str, max_words: int | None = None) -> str:
    """One line: whitespace collapsed, and at most ``max_words`` words."""
    words = text.split()
    if max_words is not None and len(words) > max_words:
        words = [*words[:max_words], "…"]
    return " ".join(words)
