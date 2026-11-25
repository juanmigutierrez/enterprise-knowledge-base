"""The whole query path wired (S14): Q1 → Q2 → Q3 → Q4 over the worker's stores and the mocks.

Card 4.13's acceptance test, without the answer cache (S15): revoke in the source
while the index is stale, and the answer reveals neither the page, its title nor
its citation; a second person who still may read it gets it cited; with the
source timing out, nothing is released and the answer says so.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sources.mock_docs.app import create_app as docs_app
from sources.mock_hr_api.app import create_app as hr_api_app
from sources.mock_hr_db.db import create_database
from sources.mock_idp.app import create_app as idp_app

from ekb.app.answer import MEMBERSHIP_CHANGED, KnowledgeQuery, access_check, build_query
from ekb.app.choices import choose, choose_query
from ekb.app.demos.answer import AnswerWorld
from ekb.app.search import Search
from ekb.contracts import (
    AccessVerdict,
    AnswerResult,
    Hit,
    QueryPlan,
    Released,
    Retrieval,
    Scope,
    SearchResult,
    SourceAccess,
)
from ekb.platform.config import load_settings
from ekb.stores.search.options.in_memory import InMemorySearchStore
from tests.http import as_httpx
from tests.unit.stores.search.test_ranking import ALICE, BOB, chunk

pytestmark = pytest.mark.filterwarnings(
    "ignore:You should not use the 'timeout' argument with the TestClient"
)
DANA = {"X-Actor": "dana"}
REMOTE = "How many days can I work remotely?"
REMOTE_PAGE = "hr_docs:HR-PL-04"


@pytest.fixture
def world(tmp_path: Path) -> Iterator[AnswerWorld]:
    create_database(tmp_path / "hr.sqlite")
    settings = load_settings(profile="local")
    with (
        TestClient(docs_app()) as docs,
        TestClient(hr_api_app()) as hr,
        TestClient(idp_app()) as idp,
    ):
        built = AnswerWorld(
            as_httpx(docs),
            as_httpx(hr),
            as_httpx(idp),
            tmp_path / "hr.sqlite",
            choose(settings, offline=True),
            choose_query(settings),
        )
        built.sync(listings=True)
        yield built


def pages(result: AnswerResult) -> set[str]:
    return {p.citation.document_key for p in result.answer.passages}


def leaks(result: AnswerResult, *needles: str) -> list[str]:
    """What of ``needles`` the agent-facing answer mentions anywhere."""
    agent = result.answer.model_dump_json()
    return [needle for needle in needles if needle in agent]


def test_a_cited_answer_names_the_options_and_numbers_its_passages(world: AnswerWorld) -> None:
    result = world.answer("alice", "How many days of annual leave do I get?")
    answer = result.answer
    assert [p.citation.number for p in answer.passages] == [1, 2, 3, 4, 5]
    assert answer.passages[0].citation.label == (
        "[1] HR-PL-01 · Annual leave policy > How much leave you get"
    )
    assert answer.searches == ("How many days of annual leave do I get?",)
    assert answer.reason == "reads as alice + all-staff, it-employees"
    assert answer.options == (
        "rules@1(max_searches=3)",
        "acl@1",
        "hybrid_rrf@1(top_k=5, candidates=20, rrf_k=60, rerank=none@1)",
        "source_recheck@1(access=source@1(hr_docs, timeout=2s), redact=rules@1)",
    )
    assert answer.notes == ("keyword leg only: no embedding model for questions",)
    assert result.released.withheld == ()
    assert result.ms > 0


def test_a_revocation_in_the_source_is_enforced_before_the_index_knows(
    world: AnswerWorld,
) -> None:
    """Card 4.13: the index is stale, Q2 + Q3 still find the page; Q4 asks the source."""
    world.docs.put("/pages/HR-PL-04/acl", json={"groups": ["hr-team"]}, headers=DANA)

    alice = world.answer("alice", REMOTE)
    assert REMOTE_PAGE in {h.chunk.document_key for h in alice.hits}  # the stale copy
    assert REMOTE_PAGE not in pages(alice)
    assert leaks(alice, "HR-PL-04", "Remote work", "Milan office", "remotely up to") == []
    assert {w.access.verdict for w in alice.released.withheld} == {AccessVerdict.DENIED}

    bob = world.answer("bob", REMOTE)  # hr-team: still a reader
    assert bob.answer.passages[0].citation.document_key == REMOTE_PAGE

    world.sync(listings=True)  # the worker copies the new readers: the pre-filter is enough
    after = world.answer("alice", REMOTE)
    assert REMOTE_PAGE not in {h.chunk.document_key for h in after.hits}
    assert after.released.withheld == ()


def test_a_source_that_times_out_releases_nothing_and_says_so(world: AnswerWorld) -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("no answer", request=request)

    with httpx.Client(
        base_url="http://docs.invalid", transport=httpx.MockTransport(timeout)
    ) as down:
        knowledge = build_query(world.query, world.stores, access_check(down, world.idp))
        result = knowledge.ask("alice", REMOTE)
    assert result.answer.passages == ()
    assert result.released.unchecked == len(result.hits) == 5
    assert (
        "5 passages could not be checked against the source, which did not answer: "
        "the answer may be incomplete"
    ) in result.answer.notes
    assert leaks(result, "HR-PL-", "Remote work") == []
    # The source is back: the same person gets the cited policy.
    assert REMOTE_PAGE in pages(world.answer("alice", REMOTE))


def test_identity_changes_not_yet_synced_are_enforced_by_the_identity_provider(
    world: AnswerWorld,
) -> None:
    world.idp.delete("/scim/v2/Groups/hr-compensation/members/bob", headers=DANA)
    world.idp.post("/scim/v2/Users/olivia/deactivate", headers=DANA)

    bob = world.answer("bob", "salary bands for engineering in Italy")
    assert bob.scope.principals is not None
    assert "hr-compensation" in bob.scope.principals.groups  # the permission store's copy
    assert "hr_docs:HR-CMP-01" in {h.chunk.document_key for h in bob.hits}
    assert "hr_docs:HR-CMP-01" not in pages(bob)
    assert leaks(bob, "HR-CMP-01", "Salary bands", "52,000") == []

    olivia = world.answer("olivia", "How many days of annual leave do I get?")
    assert olivia.hits  # Q2 still reads her copied, active record
    assert olivia.answer.passages == ()
    assert olivia.answer.notes == ("keyword leg only: no embedding model for questions",)


def test_a_page_deleted_in_the_source_is_never_released_before_the_purge(
    world: AnswerWorld,
) -> None:
    world.docs.delete("/pages/HR-PL-99", headers=DANA)
    result = world.answer("alice", "What is the hotel limit for business travel?")
    assert "hr_docs:HR-PL-99" in {h.chunk.document_key for h in result.hits}
    assert "hr_docs:HR-PL-99" not in pages(result)
    assert {w.access.verdict for w in result.released.withheld} == {AccessVerdict.DELETED}
    # HR-PL-06 itself says it replaces HR-PL-99, so the page's ID may appear in its text.
    assert leaks(result, "hr_docs:HR-PL-99", "Business travel policy (2024", "€120") == []


def test_a_question_in_two_parts_is_two_searches_merged_into_one_top_5(world: AnswerWorld) -> None:
    result = world.answer("alice", f"How many days of annual leave do I get? {REMOTE}")
    assert result.plan.searches == ("How many days of annual leave do I get?", REMOTE)
    assert len(result.searches) == 2
    assert len(result.hits) == 5
    assert {"hr_docs:HR-PL-01", REMOTE_PAGE} <= pages(result)
    assert result.answer.notes[0] == "split into 2 searches, one per part of the question"
    assert [h.rank for h in result.hits] == [1, 2, 3, 4, 5]


@dataclass
class Changing:
    """A Q2 whose answer changes between two calls: a group change mid-question."""

    calls: int = 0
    prefilter_id: str = "test@1"

    def scope(self, user_id: str) -> Scope:
        self.calls += 1
        principals = BOB if self.calls <= 3 else ALICE
        return Scope(user_id=user_id, principals=principals, reason="r", prefilter_id="test@1")


class Fixed:
    """A Q3 returning one hit, whatever it is asked."""

    retriever_id = "fixed@1"

    def retrieve(self, question: str, scope: Scope) -> Retrieval:
        hit = Hit(chunk=chunk("hr_docs:A", question), rank=1, score=1.0)
        return Retrieval(question=question, user_id=scope.user_id, hits=(hit,))


class Recorder:
    postcheck_id = "recorder@1"

    def __init__(self) -> None:
        self.hits: list[tuple[Hit, ...]] = []

    def release(self, question: str, scope: Scope, hits: Sequence[Hit]) -> Released:
        self.hits.append(tuple(hits))
        return Released(postcheck_id=self.postcheck_id)


class TwoSearches:
    planner_id = "two@1"

    def plan(self, question: str) -> QueryPlan:
        return QueryPlan(question=question, searches=("first", "second"), planner_id="two@1")


def test_a_membership_change_between_two_searches_releases_nothing() -> None:
    q4 = Recorder()
    knowledge = KnowledgeQuery(
        TwoSearches(), Search(Changing(), Fixed()), q4, InMemorySearchStore()
    )
    result = knowledge.ask("bob", "first? second?")
    assert q4.hits == [()]
    assert MEMBERSHIP_CHANGED in result.answer.notes
    assert all(isinstance(r, SearchResult) for r in result.searches)


def test_recreated_page_cannot_release_its_previous_lifetimes_chunks(world: AnswerWorld) -> None:
    page_id = "HR-PL-04"
    old = world.docs.get(f"/pages/{page_id}").json()
    assert world.docs.delete(f"/pages/{page_id}", headers=DANA).is_success
    replacement = {k: old[k] for k in ("id", "language", "department", "acl")}
    replacement.update(title="Replacement policy", body="A new policy on flexible hours.")
    created = world.docs.post("/pages", json=replacement, headers=DANA)
    assert created.is_success
    assert created.json()["first_rev"] > old["rev"]
    result = world.answer("alice", REMOTE)
    assert REMOTE_PAGE in {h.chunk.document_key for h in result.hits}
    assert REMOTE_PAGE not in pages(result)
    assert leaks(result, "HR-PL-04", "Milan office", "Replacement policy") == []
    world.sync(listings=True)
    assert REMOTE_PAGE in pages(world.answer("alice", "flexible hours"))


def test_purge_during_source_reads_discards_the_pending_answer(world: AnswerWorld) -> None:
    class PurgeDuringCheck:
        checker_id = "purge@1"

        def check(self, user_id: str, document_keys: Sequence[str]) -> dict[str, SourceAccess]:
            answers = access_check(world.docs, world.idp).check(user_id, document_keys)
            assert world.stores.search.remove(REMOTE_PAGE) > 0
            return answers

    knowledge = build_query(world.query, world.stores, PurgeDuringCheck())
    result = knowledge.ask("alice", REMOTE)
    assert result.answer.passages == ()
    assert result.hits == ()
    assert "searchable content changed: search again" in result.answer.notes


def test_q3_diagnostics_stay_out_of_the_agent_answer(world: AnswerWorld) -> None:
    class Diagnostic(Fixed):
        def retrieve(self, question: str, scope: Scope) -> Retrieval:
            return Retrieval(
                question=question,
                user_id=scope.user_id,
                notes=(
                    "1 of 12 chunks were embedded with another model",
                    "keyword leg only: the embedding model did not answer (provider detail)",
                ),
            )

    normal = world.knowledge
    knowledge = KnowledgeQuery(
        normal.q1, Search(normal.search.q2, Diagnostic()), normal.q4, normal.index
    )
    result = knowledge.ask("alice", REMOTE)
    assert result.answer.notes == ("keyword leg only: the embedding model did not answer",)
    assert "provider detail" in result.searches[0].retrieval.notes[1]
