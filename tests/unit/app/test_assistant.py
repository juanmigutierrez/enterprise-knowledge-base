"""The HR assistant behind the MCP tools (S15): answers from released passages, reused safely.

Card 4.13's acceptance test, now with the answer cache (D16 #10): warm the cache,
then revoke, delete, edit, remove a group, or take the source down while the
index is stale. A cached answer is never served unless Q4 confirms every page
it cites for the person asking now, and the fresh answer reveals nothing of what
Q4 held back. The writer only ever sees released passages.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sources.mock_docs.app import create_app as docs_app
from sources.mock_hr_api.app import create_app as hr_api_app
from sources.mock_hr_db.db import create_database
from sources.mock_idp.app import create_app as idp_app

from ekb.access.answer.port import NOTHING_FOUND, Draft
from ekb.access.auth.options.static_tokens import IssuedToken, StaticTokens, new_token
from ekb.app.assistant import AssistantResult, build_assistant, cache_key, reused_note
from ekb.app.choices import choose, choose_assistant, choose_query
from ekb.app.demos.mcp import Outage
from ekb.app.gateway import build_gateway
from ekb.app.system import KnowledgeSystem, connect_hint
from ekb.contracts import AssistantAnswer, Caller, Hit, Passage, Released, Scope
from ekb.platform.config import load_settings
from ekb.platform.gateway.models import GatewayReply
from tests.http import as_httpx

pytestmark = pytest.mark.filterwarnings(
    "ignore:You should not use the 'timeout' argument with the TestClient"
)
DANA = {"X-Actor": "dana"}
LEAVE = "How many days of annual leave do I get?"
SALARY = "What are the salary bands for engineering in Italy?"
TRAVEL = "What is the hotel limit for business travel?"
LEAVE_PAGE, SALARY_PAGE, OLD_TRAVEL = "hr_docs:HR-PL-01", "hr_docs:HR-CMP-01", "hr_docs:HR-PL-99"


def caller(user: str, agent: str = "hr-assistant") -> Caller:
    return Caller(user_id=user, agent_id=agent, token_id=f"static:{user}@{agent}")


ALICE, BOB, CARLA, OLIVIA = caller("alice"), caller("bob"), caller("carla"), caller("olivia")


@dataclass
class World:
    system: KnowledgeSystem
    docs: httpx.Client
    idp: httpx.Client
    outage: Outage

    def ask(self, who: Caller, question: str = LEAVE) -> AssistantResult:
        return self.system.assistant.answer(who, question)


@pytest.fixture
def world(tmp_path: Path) -> Iterator[World]:
    create_database(tmp_path / "hr.sqlite")
    settings = load_settings(profile="local")
    with (
        TestClient(docs_app()) as docs,
        TestClient(hr_api_app()) as hr,
        TestClient(idp_app()) as idp,
    ):
        outage = Outage(as_httpx(docs))
        with outage.client() as access_docs:
            system = KnowledgeSystem(
                as_httpx(docs),
                as_httpx(hr),
                as_httpx(idp),
                tmp_path / "hr.sqlite",
                write=choose(settings, offline=True),
                query=choose_query(settings),
                assistant=choose_assistant(settings, offline=True),
                tokens=StaticTokens(issued=[IssuedToken("alice", "hr-assistant", new_token())]),
                access_docs=access_docs,
            )
            system.sync(listings=True)
            yield World(system, as_httpx(docs), as_httpx(idp), outage)


def pages(reply: AssistantAnswer) -> set[str]:
    return {c.document_key for c in reply.citations}


def leaks(reply: AssistantAnswer, *needles: str) -> list[str]:
    """What of ``needles`` the agent-facing reply mentions anywhere."""
    text = reply.model_dump_json()
    return [needle for needle in needles if needle in text]


# ------------------------------------------------------------------ a fresh answer


def test_a_fresh_answer_cites_released_passages_and_is_kept(world: World) -> None:
    outcome = world.ask(ALICE)
    reply = outcome.reply
    assert not reply.cached
    assert outcome.kept
    assert reply.user_id == "alice"
    assert reply.agent_id == "hr-assistant"
    assert reply.answer.startswith("From the HR documents you can read:")
    assert "[1]" in reply.answer
    assert [c.number for c in reply.citations] == [1, 2, 3]
    assert LEAVE_PAGE in pages(reply)
    assert reply.options[-1] == "extractive@1(quotes=3)"
    assert world.system.assistant.cache.size() == 1


def test_nothing_released_means_a_fixed_sentence_no_writer_and_nothing_kept(world: World) -> None:
    @dataclass
    class Spy:
        writer_id: str = "spy@1"
        seen: list[Sequence[Passage]] = field(default_factory=list)

        def write(self, question: str, passages: Sequence[Passage]) -> Draft:
            self.seen.append(passages)
            return Draft(text="spy", cited=(), writer_id=self.writer_id)

    spy = Spy()
    assistant = replace(world.system.assistant, writer=spy)
    world.idp.post("/scim/v2/Users/olivia/deactivate", headers=DANA)
    world.system.sync(listings=True)
    outcome = assistant.answer(OLIVIA, LEAVE)
    assert outcome.reply.answer == NOTHING_FOUND
    assert outcome.reply.citations == ()
    assert "nothing was searched: olivia reads nothing" in outcome.reply.notes
    assert not outcome.kept
    assert spy.seen == []


def test_the_writer_never_sees_what_q4_held_back(world: World) -> None:
    """The index still lists HR-PL-01 for alice; the source says no: its text reaches no prompt."""

    @dataclass
    class Spy:
        writer_id: str = "spy@1"
        seen: list[Passage] = field(default_factory=list)

        def write(self, question: str, passages: Sequence[Passage]) -> Draft:
            self.seen.extend(passages)
            return Draft(text="ok [1]", cited=(1,), writer_id=self.writer_id)

    spy = Spy()
    assistant = replace(world.system.assistant, writer=spy)
    world.docs.put(f"/pages/{LEAVE_PAGE[8:]}/acl", json={"groups": ["hr-team"]}, headers=DANA)
    outcome = assistant.answer(ALICE, LEAVE)
    assert outcome.result is not None
    assert LEAVE_PAGE in {h.chunk.document_key for h in outcome.result.hits}  # the stale copy
    assert spy.seen
    assert LEAVE_PAGE not in {p.citation.document_key for p in spy.seen}
    assert all("25 working days" not in p.text for p in spy.seen)


def test_revocation_while_writing_withholds_the_draft_and_citations(world: World) -> None:
    class SlowWriter:
        writer_id = "slow@1"

        def write(self, question: str, passages: Sequence[Passage]) -> Draft:
            assert LEAVE_PAGE in {p.citation.document_key for p in passages}
            world.docs.put("/pages/HR-PL-01/acl", json={"groups": ["hr-team"]}, headers=DANA)
            return Draft(text="private draft [1]", cited=(1,), writer_id=self.writer_id)

    assistant = replace(world.system.assistant, writer=SlowWriter())
    outcome = assistant.answer(ALICE, LEAVE)
    assert "try again" in outcome.reply.answer
    assert outcome.reply.citations == ()
    assert leaks(outcome.reply, "private draft", "HR-PL-01", "Annual leave policy") == []
    assert assistant.cache.size() == 0


def test_index_change_during_cache_recheck_discards_the_draft(world: World) -> None:
    assistant = world.system.assistant
    assert world.ask(ALICE).kept
    original = assistant.knowledge.q4

    class RevokingCheck:
        postcheck_id = original.postcheck_id
        changed = False

        def release(self, question: str, scope: Scope, hits: Sequence[Hit]) -> Released:
            result = original.release(question, scope, hits)
            if not self.changed:
                self.changed = True
                world.docs.put("/pages/HR-PL-01/acl", json={"groups": ["hr-team"]}, headers=DANA)
                world.system.sync(listings=True)
            return result

    assistant = replace(assistant, knowledge=replace(assistant.knowledge, q4=RevokingCheck()))
    outcome = assistant.answer(ALICE, LEAVE)
    assert not outcome.reused
    assert "during the source check" in outcome.discarded
    assert LEAVE_PAGE not in pages(outcome.reply)


# ------------------------------------------------------------------------- reuse


def test_a_cached_answer_is_reused_only_after_q4_asks_the_source_again(world: World) -> None:
    first = world.ask(ALICE)
    again = world.ask(ALICE, "  how many DAYS of annual leave do i get?  ")
    assert again.reused
    assert again.result is None  # Q1 to Q3 and the writer did not run
    assert again.recheck is not None
    assert {a.document_key for a in again.recheck.checked} >= pages(first.reply)
    assert again.reply.answer == first.reply.answer
    assert again.reply.citations == first.reply.citations
    assert reused_note(2) in again.reply.notes


def test_another_person_never_gets_someone_else_s_cached_answer(world: World) -> None:
    world.ask(ALICE)
    carla = world.ask(CARLA)
    assert not carla.reused
    assert carla.reply.user_id == "carla"
    other_agent = world.ask(caller("alice", "roadmap-agent"))
    assert not other_agent.reused


def test_a_revocation_in_the_source_drops_the_cached_answer(world: World) -> None:
    """Card 4.13 with the cache warm: revoke while the index is stale."""
    assert LEAVE_PAGE in pages(world.ask(ALICE).reply)
    world.docs.put("/pages/HR-PL-01/acl", json={"groups": ["hr-team"]}, headers=DANA)

    after = world.ask(ALICE)
    assert not after.reused
    assert after.discarded.startswith("HR-PL-01 is denied now")
    assert LEAVE_PAGE not in pages(after.reply)
    assert leaks(after.reply, "HR-PL-01", "Annual leave policy", "25 working days") == []
    assert all("HR-PL-01" not in note for note in after.reply.notes)

    bob = world.ask(BOB)  # hr-team: still a reader, cited
    assert LEAVE_PAGE in pages(bob.reply)


def test_a_source_that_does_not_answer_serves_no_cached_answer(world: World) -> None:
    world.ask(ALICE)
    world.outage.down = True
    down = world.ask(ALICE)
    assert not down.reused
    assert down.discarded.endswith("could not be checked against the source")
    assert down.reply.answer == NOTHING_FOUND
    assert down.reply.citations == ()
    assert any("could not be checked against the source" in n for n in down.reply.notes)
    assert not down.kept
    world.outage.down = False
    back = world.ask(ALICE)
    assert not back.reused  # the incomplete answer was not kept
    assert back.kept


def test_a_deleted_page_drops_the_cached_answer(world: World) -> None:
    first = world.ask(ALICE, TRAVEL)
    assert OLD_TRAVEL in pages(first.reply)
    world.docs.delete("/pages/HR-PL-99", headers=DANA)
    after = world.ask(ALICE, TRAVEL)
    assert not after.reused
    assert after.discarded.startswith("HR-PL-99 is deleted now")
    assert OLD_TRAVEL not in pages(after.reply)
    # HR-PL-06's own text names HR-PL-99 ("replaces the 2024 travel policy"): that is allowed.
    assert leaks(after.reply, "superseded", "Business travel policy (2024") == []


def test_a_group_removed_in_the_identity_provider_drops_the_cached_answer(world: World) -> None:
    assert SALARY_PAGE in pages(world.ask(BOB, SALARY).reply)
    world.idp.delete("/scim/v2/Groups/hr-compensation/members/bob", headers=DANA)
    after = world.ask(BOB, SALARY)  # the permission store still lists the group
    assert not after.reused
    assert after.discarded.startswith("HR-CMP-01 is denied now")
    assert SALARY_PAGE not in pages(after.reply)
    assert leaks(after.reply, "HR-CMP-01", "Salary bands", "42,000") == []


def test_an_edited_page_drops_the_cached_answer(world: World) -> None:
    world.ask(ALICE)
    page = world.docs.get("/pages/HR-PL-01").json()
    body = page["body"].replace("25 working days", "26 working days")
    world.docs.put("/pages/HR-PL-01", json={"body": body}, headers=DANA)
    after = world.ask(ALICE)
    assert not after.reused
    assert after.discarded == "HR-PL-01 changed in the source since the answer was written"
    marked = [c for c in after.reply.citations if c.document_key == LEAVE_PAGE]
    assert marked
    assert all(c.source_changed for c in marked)


def test_a_change_in_what_the_person_may_read_drops_the_cached_answer(world: World) -> None:
    world.ask(ALICE)
    world.docs.put("/pages/HR-PL-01/acl", json={"groups": ["hr-team"]}, headers=DANA)
    world.system.sync(listings=True)  # the worker copies the new readers into the index
    after = world.ask(ALICE)
    assert not after.reused
    assert after.recheck is None  # dropped before asking the source: the index moved on
    assert "changed in the index" in after.discarded


def test_an_answer_written_by_the_fallback_is_not_kept(world: World) -> None:
    @dataclass
    class Failing:
        writer_id: str = "model@1(answer@1)"

        def write(self, question: str, passages: Sequence[Passage]) -> Draft:
            return Draft(text="quoted [1]", cited=(1,), writer_id="x", fallback=True)

    assistant = replace(world.system.assistant, writer=Failing())
    first = assistant.answer(ALICE, LEAVE)
    assert not first.kept
    assert not assistant.answer(ALICE, LEAVE).reused


def test_a_deleted_page_never_comes_back_through_the_model_s_cache(world: World) -> None:
    """The gateway's cache keeps a reply that quoted the page (S16 forgets it), but its prompt
    cannot be rebuilt once the page is purged, so it is never served."""
    gateway = build_gateway(load_settings(profile="ci").gateway)
    prompts: list[str] = []

    class Recording:
        def complete(self, task: str, prompt: str, **kwargs: object) -> GatewayReply:
            prompts.append(prompt)
            return gateway.complete(task, prompt, **kwargs)  # type: ignore[arg-type]

    settings = load_settings(profile="local")
    model = choose_assistant(settings).with_options(answer_cache="in_memory")
    assistant = build_assistant(model, world.system.knowledge, Recording())
    first = assistant.answer(ALICE, TRAVEL)
    assert first.reply.options[-1] == "model@1(answer@1, fake:fake-small)"
    assert "superseded" in prompts[0]
    world.docs.delete("/pages/HR-PL-99", headers=DANA)
    world.system.sync(listings=True)  # the worker purges it
    after = assistant.answer(ALICE, TRAVEL)
    assert not after.reused
    assert "superseded" not in prompts[-1]
    assert OLD_TRAVEL not in pages(after.reply)
    assert leaks(after.reply, "superseded", "Business travel policy (2024") == []


# ------------------------------------------------------------ whoami, search, keys


def test_whoami_and_search_run_as_the_caller(world: World) -> None:
    who = world.system.assistant.whoami(ALICE)
    assert who.reads_as == "reads as alice + all-staff, it-employees"
    assert who.groups == ("all-staff", "it-employees")
    assert not who.reads_nothing
    nobody = world.system.assistant.whoami(caller("mallory"))
    assert nobody.reads_nothing
    assert nobody.groups == ()
    answer = world.system.assistant.search(BOB, SALARY)
    assert answer.agent_id == "hr-assistant"
    assert answer.passages[0].citation.document_key == SALARY_PAGE


def test_the_cache_key_ignores_spacing_and_case_but_not_who_or_what(world: World) -> None:
    scope = world.system.knowledge.search.q2.scope("alice")
    options = world.system.assistant.options
    key = cache_key(ALICE, scope, LEAVE, options)
    assert cache_key(ALICE, scope, "  " + LEAVE.upper() + " ", options) == key
    assert cache_key(caller("alice", "roadmap-agent"), scope, LEAVE, options) != key
    assert cache_key(ALICE, scope, "Another question?", options) != key
    assert cache_key(ALICE, scope, LEAVE, (*options[:-1], "model@1")) != key
    fewer = Scope(
        user_id="alice",
        principals=scope.principals.model_copy(update={"groups": frozenset({"all-staff"})})
        if scope.principals is not None
        else None,
        reason="",
        prefilter_id="acl@1",
    )
    assert cache_key(ALICE, fewer, LEAVE, options) != key
    assert LEAVE not in key


def test_the_system_reports_its_options_tokens_and_latest_answers(world: World) -> None:
    lines = world.system.describe()
    assert "auth static_tokens: static_tokens@1(hr-assistant, 1 user)" in lines
    assert "answer model -> extractive (offline: no model): extractive@1(quotes=3)" in lines
    assert "answer_cache in_memory: in_memory@1(max=1000)" in lines
    assert [t.user_id for t in world.system.issued] == ["alice"]
    assert world.system.last_answer("alice") is None
    world.ask(ALICE)
    last = world.system.last_answer("alice")
    assert last is not None
    assert last.kept
    assert connect_hint("http://x/mcp", "t") == (
        'claude mcp add --transport http ekb http://x/mcp --header "Authorization: Bearer t"'
    )


def test_a_model_writer_without_the_gateway_is_refused(world: World) -> None:
    model = choose_assistant(load_settings(profile="local"))
    with pytest.raises(ValueError, match="needs the model gateway"):
        build_assistant(model, world.system.knowledge)
