"""Port for Q4 · Post-check. Every option implements :class:`PostCheck`.

**Why a second permission check.** Q2's pre-filter uses copies: the readers T8
copied from the source, and the groups the identity sync last copied. Copies
are always a little behind (D8): a page whose readers were narrowed a minute
ago, a person removed from a group, a page deleted, all still look readable
until the worker catches up. So before any text leaves, Q4 asks the source, for
the person asking and each document behind Q3's hits: *can they read it now?*

- **Fail closed.** Anything but a clear yes holds the passage back: a denial, a
  deletion, a timeout, an error, a source with no access check (D8: "a denial,
  deletion, timeout or unknown result excludes that document").
- **Narrow only.** Q4 checks what Q3 found; it never adds a passage. A page the
  source has just opened to someone appears once the worker copies the new
  readers.
- **Silent about what it holds back.** A denied or deleted page is as invisible
  to the asker as one the pre-filter kept out. Only "N passages could not be
  checked" is said, with no names, because the source did not answer.

What is released goes through a :class:`Redactor`, and each passage gets a
numbered :class:`~ekb.contracts.Citation`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ekb.contracts import Hit, Redaction, Released, Scope, SourceAccess


@runtime_checkable
class SourceAccessCheck(Protocol):
    """The source's current answer: may this person read these documents now?"""

    @property
    def checker_id(self) -> str:
        """The check and its version, e.g. ``source@1(hr_docs)``."""
        ...

    def check(self, user_id: str, document_keys: Sequence[str]) -> Mapping[str, SourceAccess]:
        """One answer per document key. It should not raise: a source that does not answer
        is ``unavailable``. A key missing from the answer is treated as unavailable."""
        ...


@dataclass(frozen=True)
class Redacted:
    """Text with personal data replaced by markers, and what was removed (never the values)."""

    text: str
    redactions: tuple[Redaction, ...] = ()


@runtime_checkable
class Redactor(Protocol):
    """Removes personal data from text about to be released."""

    @property
    def redactor_id(self) -> str:
        """The option and its version, e.g. ``rules@1``."""
        ...

    def redact(self, text: str) -> Redacted: ...


@runtime_checkable
class PostCheck(Protocol):
    """Decides, passage by passage, what Q3 found may be released to this person now."""

    @property
    def postcheck_id(self) -> str:
        """The option, its access check and its redactor."""
        ...

    def release(self, question: str, scope: Scope, hits: Sequence[Hit]) -> Released: ...
