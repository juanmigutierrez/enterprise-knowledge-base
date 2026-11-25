"""Retrieval contract: what the query steps hand each other (S13).

The query steps Q1 to Q4 never import each other (an import-linter contract),
so the shapes they pass along live here, next to the chunk they carry:

- :class:`Scope`: **Q2**'s answer to "who is asking?": the principals the
  person reads with *now* (their user ID and current groups), or ``None`` for
  an unknown or inactive person, who reads nothing.
- :class:`Hit`: one chunk **Q3** kept, with the fused score and where each leg
  (keywords, vectors) ranked it, so anyone can see *why* it is there.
- :class:`Retrieval`: Q3's answer: the hits, best first, and plain-language
  notes (a leg that did not run, chunks it could not compare).
- :class:`SearchResult`: both, for one question asked by one person.

**Nothing here describes what the asker may not read.** Q2 filters before Q3
ranks, so Q3 never sees such a chunk and cannot report it. Only the inspector,
which shows everything to whoever runs it locally, counts what was withheld.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from ekb.contracts.knowledge import Chunk
from ekb.contracts.permissions import Principals


class Scope(BaseModel):
    """Who is asking, as a search filters them: Q2's output, Q3's input."""

    model_config = ConfigDict(frozen=True)

    user_id: str
    principals: Principals | None
    """Their user ID and groups now; ``None``: unknown or inactive, they read nothing."""
    reason: str
    """In plain words, e.g. ``reads as alice + all-staff, it-employees`` or
    ``inactive (a leaver): reads nothing``."""
    prefilter_id: str
    """Which Q2 option decided, e.g. ``acl@1``."""

    @property
    def reads_nothing(self) -> bool:
        return self.principals is None


class Leg(StrEnum):
    """The two ways Q3 ranks the chunks a person may read (card 4.5)."""

    KEYWORD = "keyword"
    """BM25 over the words: exact terms, codes, names."""
    VECTOR = "vector"
    """Cosine similarity of vectors: meaning, paraphrases, other languages."""


class Hit(BaseModel):
    """One chunk Q3 kept, and where it came from."""

    model_config = ConfigDict(frozen=True)

    chunk: Chunk
    rank: int
    """1 for the best, after fusion (and reranking)."""
    score: float
    """The fused score: reciprocal rank fusion of the legs that found it."""
    keyword_rank: int | None = None
    """Where the keyword leg ranked it (1 = best); ``None`` if it did not find it."""
    keyword_score: float | None = None
    """Its BM25 score."""
    vector_rank: int | None = None
    """Where the vector leg ranked it; ``None`` if it did not find it or did not run."""
    vector_score: float | None = None
    """Its cosine similarity to the question."""

    @property
    def legs(self) -> tuple[Leg, ...]:
        """The legs that found it."""
        found = ((Leg.KEYWORD, self.keyword_rank), (Leg.VECTOR, self.vector_rank))
        return tuple(leg for leg, rank in found if rank is not None)


class Retrieval(BaseModel):
    """Q3's answer for one question and one scope: the hits, best first."""

    model_config = ConfigDict(frozen=True)

    question: str
    user_id: str
    hits: tuple[Hit, ...] = ()
    searched: int = 0
    """Chunks the scope admits, the only ones either leg ranked."""
    keyword_found: int = 0
    """Candidates the keyword leg returned."""
    vector_found: int = 0
    """Candidates the vector leg returned (0 when it did not run)."""
    vector_leg: bool = False
    """Whether the question was embedded and compared with the chunks' vectors."""
    notes: tuple[str, ...] = ()
    """What a reader should know: a leg that did not run, chunks it could not compare."""
    retriever_id: str = ""
    """Which Q3 option, its settings and its reranker, e.g.
    ``hybrid_rrf@1(top_k=5, candidates=20, rrf_k=60, rerank=none@1)``."""


class SearchResult(BaseModel):
    """One question asked by one person: Q2's scope, then Q3's retrieval."""

    model_config = ConfigDict(frozen=True)

    scope: Scope
    retrieval: Retrieval
    ms: float = 0.0
    """How long Q2 and Q3 took together."""
