"""Answer contract: what Q1 plans and what Q4 releases (S14).

The query steps never import each other, so the shapes they pass along live
here, next to S13's :mod:`ekb.contracts.retrieval`:

- :class:`QueryPlan`: **Q1**'s answer to "what should be searched?": one search
  for a simple question, several for a question with several parts.
- :class:`SourceAccess`: one document's **current** access for one person, as
  the source system answers it (Q4's second permission check, D8).
- :class:`Citation`, :class:`Redaction`, :class:`Passage`: what **Q4** releases:
  the text a person may read now, with personal data removed and a numbered
  citation.
- :class:`Released`: Q4's whole answer, including what it held back and why.
- :class:`Answer`: what leaves the knowledge base for an agent (the MCP tools,
  S15). :class:`AnswerResult`: everything the query path did, for the demo and
  the inspector.

**Only** :class:`Answer` **may leave.** It never names a document Q4 held back:
no title, no citation, no text, no count of denied or deleted pages. A held-back
page is as invisible as one the pre-filter kept out. The one thing it reports is
how many passages could not be *checked* (a source that did not answer), with no
names, so an agent can say "the answer may be incomplete" instead of "there is
no such policy". :class:`Released` and :class:`AnswerResult` hold the rest, for
the local tools that show everything to whoever runs them (D19).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from ekb.contracts.knowledge import PiiKind
from ekb.contracts.retrieval import Hit, Scope, SearchResult


class QueryPlan(BaseModel):
    """Q1's output: the searches one question becomes, in order."""

    model_config = ConfigDict(frozen=True)

    question: str
    searches: tuple[str, ...]
    """What Q2 + Q3 search for: the question itself, or its parts."""
    notes: tuple[str, ...] = ()
    """In plain words, e.g. ``split into 2 searches``."""
    planner_id: str = ""
    """Which Q1 option planned it, e.g. ``rules@1(max_searches=3)``."""


class AccessVerdict(StrEnum):
    """What the source says about one person and one document, right now."""

    ALLOWED = "allowed"
    """The source's readers admit this person today."""
    DENIED = "denied"
    """The source's readers no longer admit them, or the identity provider says
    they are unknown or inactive."""
    DELETED = "deleted"
    """The source no longer has the document."""
    UNAVAILABLE = "unavailable"
    """No answer: a timeout, an error, or no access check for this source. Unknown is
    treated as no (D8)."""

    @property
    def released(self) -> bool:
        return self is AccessVerdict.ALLOWED


class SourceAccess(BaseModel):
    """One document's current access for one person, as the source answered it."""

    model_config = ConfigDict(frozen=True)

    document_key: str
    verdict: AccessVerdict
    reason: str
    """In plain words, e.g. ``the docs tool's readers no longer include alice``."""
    title: str = ""
    """The document's title in the source now; set only when access is allowed."""
    content_hash: str | None = None
    """The source's content hash now, to tell whether the indexed text is current."""
    version: str = ""
    """The source's version label now, e.g. ``3.2``."""
    first_rev: int | None = None
    """First content revision of the source page's current lifetime."""
    checker_id: str = ""
    """Which access check answered, e.g. ``source@1(hr_docs)``."""


class Redaction(BaseModel):
    """Personal data removed from a released passage: the kind and how often, never the value."""

    model_config = ConfigDict(frozen=True)

    kind: PiiKind
    count: int


class Citation(BaseModel):
    """Where a released passage comes from, numbered in the order it is released."""

    model_config = ConfigDict(frozen=True)

    number: int
    """1 for the first passage released, then 2, 3 …: the ``[1]`` an answer refers to."""
    document_key: str
    title: str
    """The document's title as the source gave it in Q4's check."""
    section: tuple[str, ...] = ()
    """The headings the passage sits under, outermost first."""
    chunk_id: str
    document_version: str
    """The version of the text released (the indexed one)."""
    source_changed: bool = False
    """The source's text has changed since it was indexed: the passage may be out of date
    until the worker indexes the new version."""

    @property
    def label(self) -> str:
        """``[1] HR-PL-01 · Annual leave policy > How much leave you get``.

        The page's own heading usually repeats its title, so a first heading equal to
        the title is not written twice.
        """
        page = self.document_key.partition(":")[2] or self.document_key
        section = list(self.section)
        if section and section[0].casefold() == self.title.casefold():
            section = section[1:]
        trail = "".join(f" > {heading}" for heading in section)
        return f"[{self.number}] {page} · {self.title}{trail}"


class Passage(BaseModel):
    """One chunk Q4 released: its text with personal data removed, and its citation.

    It holds no :class:`~ekb.contracts.Chunk`: the chunk keeps the text as indexed,
    personal data included, and its readers, neither of which may leave.
    """

    model_config = ConfigDict(frozen=True)

    citation: Citation
    text: str
    """The chunk's text, with personal data replaced by markers such as ``[IBAN removed]``."""
    redactions: tuple[Redaction, ...] = ()


class Withheld(BaseModel):
    """A hit Q4 held back, and the source's answer. Never part of an :class:`Answer`."""

    model_config = ConfigDict(frozen=True)

    hit: Hit
    access: SourceAccess


class Released(BaseModel):
    """Q4's answer for one question and one person: what may be released, and what not."""

    model_config = ConfigDict(frozen=True)

    passages: tuple[Passage, ...] = ()
    withheld: tuple[Withheld, ...] = ()
    """Held back, with the reason: for the demo and the inspector only."""
    checked: tuple[SourceAccess, ...] = ()
    """The source's answer for each document asked about, in rank order: for the demo and
    the inspector only."""
    unchecked: int = 0
    """Of the held-back passages, those whose source did not answer: the only number an
    agent is told."""
    notes: tuple[str, ...] = ()
    """Agent-safe: names no held-back document."""
    postcheck_id: str = ""
    """Which Q4 option, its access check and its redactor, e.g.
    ``source_recheck@1(access=source@1(hr_docs), redact=rules@1)``."""


class Answer(BaseModel):
    """What the knowledge base hands an agent for one question: cited, checked passages."""

    model_config = ConfigDict(frozen=True)

    question: str
    user_id: str
    reason: str
    """Who they read as, in plain words (Q2's scope)."""
    agent_id: str = ""
    """The agent that asked for the person (S15); empty when no agent did (the demos and
    the inspector)."""
    searches: tuple[str, ...] = ()
    """What was searched (Q1's plan)."""
    passages: tuple[Passage, ...] = ()
    notes: tuple[str, ...] = ()
    """What the reader should know: a split question, a leg that did not run, passages
    that could not be checked. Never a held-back document."""
    options: tuple[str, ...] = ()
    """The Q1 to Q4 options that answered, e.g. ``rules@1(max_searches=3)``."""


class AnswerResult(BaseModel):
    """Everything the query path did for one question: Q1's plan, each search, Q4's release.

    For the demo and the inspector. Only :attr:`answer` may leave the knowledge base.
    """

    model_config = ConfigDict(frozen=True)

    plan: QueryPlan
    scope: Scope
    searches: tuple[SearchResult, ...]
    """Q2 + Q3 for each of the plan's searches, in order."""
    hits: tuple[Hit, ...]
    """What Q4 checked: the searches' hits merged, best first."""
    released: Released
    answer: Answer
    ms: float = 0.0
    """How long Q1 to Q4 took together."""
