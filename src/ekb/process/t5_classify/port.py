"""Port for T5 · Classify. Every classification option implements :class:`Classifier`.

A classifier reads one document (T3's :class:`~ekb.contracts.ParsedDocument`)
and its chunks (T4) and returns one :class:`Labels` per chunk, in order. It
reads the whole document, not each chunk alone, because some signals belong to
the document: the department its source files it under, or a "Restricted to the
compensation team" line that covers every section below it.

Like T4's header writer, a classifier returns labels, not chunks: the step
(:class:`~ekb.process.t5_classify.step.ClassifyStep`) puts them on the chunks,
so a classifier cannot change a chunk's text or set its ACL or vector.

**Content only, never permissions.** Labels come from what the document says,
not from who may read it. Who may read it is T8's job, and a permission change
takes T2's fast lane, which never runs T5: a label that depended on the ACL
would go stale there.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ekb.contracts import Chunk, ParsedDocument, PiiKind, Sensitivity


def passage(chunk: Chunk) -> str:
    """The text and generated context that share this chunk's sensitivity label."""
    return "\n\n".join(filter(None, (chunk.context_header, chunk.text)))


DEPARTMENTS = ("people", "finance", "sales", "operations", "product", "engineering", "data")
"""The departments of ShopFully / Flipp assumed in decisions.md §0, as labels:
People, Finance, Sales and account management, Client Experience / Operations,
Product, Engineering, Data / BI. A source's own label is kept as the source
writes it, even when it is not one of these."""


@dataclass(frozen=True)
class Labels:
    """What T5 says about one chunk."""

    sensitivity: Sensitivity
    department: str | None = None
    pii: tuple[PiiKind, ...] = ()
    why: str = ""
    """One line in plain words: why this sensitivity (shown in the demo and the UI)."""


@dataclass(frozen=True)
class Classification:
    """One document's labels, one per chunk, and what produced them."""

    labels: tuple[Labels, ...]
    classifier_id: str
    """For ``Chunk.classifier_id``: ``rules@1``, or the option, prompt version and model."""
    note: str = ""
    """In plain words: where the department came from, which model answered, or why
    only the rules did."""


@runtime_checkable
class Classifier(Protocol):
    """Labels every chunk of one document."""

    @property
    def classifier_id(self) -> str:
        """The option and its version, e.g. ``rules@1``."""
        ...

    def classify(self, document: ParsedDocument, chunks: tuple[Chunk, ...]) -> Classification: ...
