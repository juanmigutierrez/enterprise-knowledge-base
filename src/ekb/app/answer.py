"""The whole query path (S14): Q1 → Q2 → Q3 → Q4, with the options the profile names.

S13 wired Q2 and Q3 (:class:`ekb.app.search.Search`). This adds the steps on
either side::

    (user, question) ─▶ Q1 plan ─▶ searches (the question, or its parts)
                     ─▶ each search: Q2 scope ─▶ Q3 retrieve      (S13)
                     ─▶ the searches' hits merged by rank, top k (one search: as is)
                     ─▶ Q4: the source's current answer per document
                            ─▶ hold back anything but a yes ─▶ remove personal data ─▶ cite
                     ─▶ Answer: what may leave, for an agent (S15's MCP tools)
                        AnswerResult: everything above, for the demo and the inspector

Chosen here, in the composition root, and nowhere else:

- **Which options.** :func:`ekb.app.choices.choose_query`: ``rewrite_when_needed``
  runs as its stand-in ``rules`` until card 4.8; the redactor is Q4's ``redact``
  setting, ``rules`` when absent.
- **Which access check.** The connector's
  (:class:`ekb.connect.access_check.SourceAccessChecker`): the docs tool's
  current readers and the identity provider's current groups, read with Q4's own
  short timeout. :func:`access_check` builds it from the two HTTP clients.
- **How searches merge.** Q3's own rule, reciprocal rank fusion over whole
  searches (:func:`ekb.access.query.q3_retrieve.fusion.merge`), cut to Q3's
  ``top_k``, so a split question still sends five passages to Q4.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from time import perf_counter

import httpx

from ekb.access.query.q1_plan.port import Planner
from ekb.access.query.q3_retrieve.fusion import merge
from ekb.access.query.q3_retrieve.options.hybrid_rrf import TOP_K
from ekb.access.query.q3_retrieve.port import QueryEncoder
from ekb.access.query.q4_postcheck.port import PostCheck, Redactor, SourceAccessCheck
from ekb.app.choices import Choices
from ekb.app.pipeline import Stores
from ekb.app.search import Search, build_search
from ekb.connect.access_check import SourceAccessChecker
from ekb.connect.docs_api import DocsApi
from ekb.connect.identity_api import IdentityApi
from ekb.contracts import Answer, AnswerResult, Hit, Released, SearchResult
from ekb.platform import registry
from ekb.stores.search.port import SearchStore

MEMBERSHIP_CHANGED = "group membership changed during search: search again"


@dataclass(frozen=True)
class KnowledgeQuery:
    """Q1, then Q2 + Q3 for each search, then Q4: one person, one question."""

    q1: Planner
    search: Search
    q4: PostCheck
    index: SearchStore
    top_k: int = TOP_K

    def ask(self, user_id: str, question: str) -> AnswerResult:
        start = perf_counter()
        plan = self.q1.plan(question)
        texts = plan.searches or (question,)
        initial = self.search.q2.scope(user_id)
        snapshot = self.index.ranking_snapshot(initial.principals)
        searches = tuple(self.search.ask(user_id, text) for text in texts)
        scope = searches[-1].scope
        notes = list(plan.notes)
        if any(result.scope.principals != scope.principals for result in searches):
            hits: tuple[Hit, ...] = ()
            notes.append(MEMBERSHIP_CHANGED)
        else:
            hits = self._merged(searches)
        # Q3 diagnostics can include pre-check corpus counts and provider errors.
        for result in searches:
            for note in result.retrieval.notes:
                if note.startswith("keyword leg only: the embedding model did not answer"):
                    notes.append("keyword leg only: the embedding model did not answer")
                elif note in (
                    "keyword leg only: no embedding model for questions",
                    "an empty question: nothing to search",
                    "searchable content changed: search again",
                    MEMBERSHIP_CHANGED,
                    f"nothing was searched: {user_id} reads nothing",
                ):
                    notes.append(note)
        released = self.q4.release(question, scope, hits)
        current = self.search.q2.scope(user_id)
        changed_scope = (
            initial.principals != current.principals or scope.principals != current.principals
        )
        if changed_scope or not snapshot.is_current():
            hits = ()
            released = Released(postcheck_id=self.q4.postcheck_id)
            notes.append(
                MEMBERSHIP_CHANGED if changed_scope else "searchable content changed: search again"
            )
        scope = current
        notes.extend(released.notes)
        answer = Answer(
            question=question,
            user_id=user_id,
            reason=scope.reason,
            searches=texts,
            passages=released.passages,
            notes=tuple(dict.fromkeys(notes)),
            options=(
                plan.planner_id,
                scope.prefilter_id,
                self.search.q3.retriever_id,
                released.postcheck_id,
            ),
        )
        return AnswerResult(
            plan=plan,
            scope=scope,
            searches=searches,
            hits=hits,
            released=released,
            answer=answer,
            ms=(perf_counter() - start) * 1000.0,
        )

    def _merged(self, searches: Sequence[SearchResult]) -> tuple[Hit, ...]:
        if len(searches) == 1:
            return searches[0].retrieval.hits
        return merge([result.retrieval.hits for result in searches])[: self.top_k]


def access_check(docs: httpx.Client, identity: httpx.Client) -> SourceAccessChecker:
    """The connector's current access check over the docs tool's and identity provider's APIs."""
    return SourceAccessChecker(DocsApi(docs), IdentityApi(identity))


def build_query(
    query: Choices,
    stores: Stores,
    access: SourceAccessCheck,
    encoder: QueryEncoder | None = None,
) -> KnowledgeQuery:
    """Q1 to Q4 as ``query`` (from :func:`~ekb.app.choices.choose_query`) names them,
    reading ``stores``, checking with ``access``; ``encoder`` embeds the questions."""
    search = build_search(query, stores, encoder)
    q1 = registry.create("q1_plan", query.option("q1_plan"), **query.params("q1_plan"))
    if not isinstance(q1, Planner):
        raise TypeError(f"q1_plan option {query.option('q1_plan')!r} is not a Planner")
    redactor = registry.create("q4_redact", query.option("q4_redact"), **query.params("q4_redact"))
    if not isinstance(redactor, Redactor):
        raise TypeError(f"q4_redact option {query.option('q4_redact')!r} is not a Redactor")
    q4 = registry.create(
        "q4_postcheck",
        query.option("q4_postcheck"),
        access=access,
        redactor=redactor,
        **query.params("q4_postcheck"),
    )
    if not isinstance(q4, PostCheck):
        raise TypeError(f"q4_postcheck option {query.option('q4_postcheck')!r} is not a PostCheck")
    top_k = int(query.params("q3_retrieve").get("top_k", TOP_K))
    return KnowledgeQuery(q1=q1, search=search, q4=q4, index=stores.search, top_k=top_k)
