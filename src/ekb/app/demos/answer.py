"""S14 · Q4 + Q1: why a second permission check exists.

``ekb demo answer`` loads the four mock sources through the worker (S12), then
asks questions through the whole query path (``ekb.app.answer``): Q1 plans the
searches, Q2 + Q3 rank what the asker may read by the index's copies (S13), and
Q4 asks the **source** whether they may read each page *now* before anything is
released, removes personal data and numbers the citations. The story:

0. the options, Q1 to Q4;
1. a cited answer: every page behind alice's passages is confirmed by the docs
   tool and the identity provider, and each passage gets a citation;
2. a question in two parts: Q1 makes two searches, merged into one top 5;
3. **why a second check**, three times, each before the worker has caught up:
   a page's readers narrowed in the docs tool; bob removed from a group and
   olivia gone in the identity provider; a page deleted. Q2 and Q3 still find
   the pages (the copies are stale); Q4 asks the source and holds them back.
   After the worker runs, the pre-filter keeps them out on its own;
4. the docs tool does not answer: nothing is released, and the answer says some
   passages could not be checked, without naming them;
5. personal data: a payroll page's IBAN is removed from bob's passage; its email
   address and phone number stay.

It runs offline unless ``--models`` is given (then questions are embedded with
T7's model, as in ``ekb demo search``). Q1 to Q4 call no model.
"""

from __future__ import annotations

import tempfile
import textwrap
from collections.abc import Sequence
from contextlib import ExitStack
from pathlib import Path
from typing import Annotated

import httpx
import typer

from ekb.app.answer import KnowledgeQuery, access_check, build_query
from ekb.app.choices import Choices, choose_query
from ekb.app.demos._app import DockerFlag, demo_app
from ekb.app.demos._mocks import Echo, MockStartError, call, fail, mock_client, run_mock_command
from ekb.app.demos.ingest import demo_choices
from ekb.app.demos.search import DB, DOCS, HR, IDP, World
from ekb.app.infra import run_namespace
from ekb.app.search import encoder_note
from ekb.contracts import AccessVerdict, AnswerResult, Passage, PiiKind
from ekb.platform.config import load_settings
from ekb.platform.gateway.errors import GatewayError
from ekb.platform.gateway.gateway import ModelGateway

WIDTH = 92
DANA = {"X-Actor": "dana"}
LEAVE = "How many days of annual leave do I get?"
REMOTE = "How many days can I work remotely?"
TWO_PARTS = f"{LEAVE} {REMOTE}"
SALARY = "What are the salary bands for engineering in Italy?"
TRAVEL = "What is the hotel limit for business travel?"
PAYROLL = "Who do I ask about my payslip? Where was the corrected salary payment sent?"
REMOTE_PAGE, OLD_TRAVEL, PAY_GROUP, LEFT = "HR-PL-04", "HR-PL-99", "hr-compensation", "olivia"
PAYROLL_PAGE = {
    "id": "HR-PL-13",
    "title": "Payroll contacts, Italy",
    "language": "en",
    "department": "people",
    "acl": {"groups": ["hr-team"]},
    "body": (
        "# Payroll contacts, Italy\n\n"
        "## Who to ask\n"
        "Write to payroll@example.com or call +39 02 1234 5678 with questions about "
        "your payslip.\n\n"
        "## Example of a corrected payment\n"
        "The March salary of the example employee was sent again to IBAN "
        "IT60 X054 2811 1010 0000 0123 456 after the first transfer failed.\n"
    ),
}
"""Synthetic, as in ``ekb demo t5``: placeholder address and number, and the standard
Italian example IBAN (valid check digits, no real account)."""


class AnswerWorld(World):
    """S13's world (mocks, connectors, worker, stores) with the whole query path on top."""

    def __init__(
        self,
        docs: httpx.Client,
        hr: httpx.Client,
        idp: httpx.Client,
        db_path: Path,
        write: Choices,
        query: Choices,
        gateway: ModelGateway | None = None,
    ) -> None:
        super().__init__(docs, hr, idp, db_path, write, query, gateway)
        self.docs = docs
        self.knowledge = build_query(query, self.stores, access_check(docs, idp), self.encoder)

    def answer(self, user: str, question: str) -> AnswerResult:
        return self.knowledge.ask(user, question)


def _say(echo: Echo, text: str, indent: str = "   ") -> None:
    for line in textwrap.wrap(text, width=WIDTH, initial_indent=indent, subsequent_indent=indent):
        echo(line)


def _page(key: str) -> str:
    return key.partition(":")[2] or key


def _verdicts(result: AnswerResult) -> str:
    """``2 allowed, 1 denied``: Q4's answers, one per page asked about."""
    checked = result.released.checked
    counts = {v: sum(1 for a in checked if a.verdict is v) for v in AccessVerdict}
    parts = [f"{n} {verdict.value}" for verdict, n in counts.items() if n]
    pages = len(checked)
    return f"asked the source about {pages} page{'s' if pages != 1 else ''}: {', '.join(parts)}"


REMOVED = {
    PiiKind.IBAN: "IBAN",
    PiiKind.PAYMENT_CARD: "card number",
    PiiKind.IT_FISCAL_CODE: "fiscal code",
    PiiKind.ES_NATIONAL_ID: "ID number",
}


def _passage(echo: Echo, passage: Passage, *, full: bool) -> None:
    citation = passage.citation
    changed = "  (source changed since indexing)" if citation.source_changed else ""
    echo(f"       {textwrap.shorten(citation.label, WIDTH - 7)}{changed}")
    text = " ".join(passage.text.split())
    if full:
        _say(echo, text, indent="           ")
    else:
        echo(f"           {textwrap.shorten(text, WIDTH - 11)}")
    if passage.redactions:
        removed = ", ".join(
            f"{r.count} {REMOVED.get(r.kind, r.kind.value)}" for r in passage.redactions
        )
        echo(f"           removed: {removed}")


def _show(echo: Echo, result: AnswerResult, *, full: str | None = None) -> None:
    """Q1 to Q4 for one question; passages of the document ``full`` are printed whole."""
    answer = result.answer
    echo(f'   {answer.user_id} asks "{answer.question}"')
    if len(result.plan.searches) > 1:
        searches = " · ".join(f'"{s}"' for s in result.plan.searches)
        _say(echo, f"Q1     {len(result.plan.searches)} searches: {searches}", indent="     ")
    pages = sorted({h.chunk.document_key for h in result.hits})
    listed = ", ".join(_page(key) for key in pages) or "none"
    _say(echo, f"Q2     {result.scope.reason}", indent="     ")
    echo(f"     Q3     {len(result.hits)} hits from {listed}")
    if result.hits:
        echo(f"     Q4     {_verdicts(result)}")
    count = len(answer.passages)
    echo(f"     answer {count} passage{'s' if count != 1 else ''}" + (":" if count else ""))
    for passage in answer.passages:
        _passage(echo, passage, full=passage.citation.document_key == full)
    for note in answer.notes:
        _say(echo, f"note: {note}", indent="     ")
    seen: set[str] = set()
    for item in result.released.withheld:
        key = item.access.document_key
        if key in seen:
            continue
        seen.add(key)
        chunks = sum(1 for w in result.released.withheld if w.access.document_key == key)
        _say(
            echo,
            f"inspector view: Q4 held back {_page(key)} ({chunks} chunk"
            f"{'s' if chunks != 1 else ''}): {item.access.verdict.value}, {item.access.reason}.",
            indent="     ",
        )


# ------------------------------------------------------------------------ scenes


def run_answer_demo(
    docs: httpx.Client,
    hr: httpx.Client,
    idp: httpx.Client,
    db_path: Path,
    echo: Echo = typer.echo,
    *,
    question: str | None = None,
    users: Sequence[str] = ("alice",),
    write: Choices | None = None,
    query: Choices | None = None,
    gateway: ModelGateway | None = None,
) -> AnswerWorld:
    """The whole story against the four mocks, or one ``question`` asked by ``users``."""
    for client in (docs, hr, idp):
        call(client, "POST", "/reset")
    settings = load_settings()
    write = write or demo_choices(settings, models=False)[0]
    query = query or choose_query(settings)
    world = AnswerWorld(docs, hr, idp, db_path, write, query, gateway)

    echo("S14 · Q4 + Q1: why a second permission check exists\n")
    _options(world, echo)
    world.sync(listings=True)
    if question is not None:
        echo(f"1. Your question, through Q1 to Q4 ({len(world.stores.search.chunks())} chunks):")
        for user in users:
            _show(echo, world.answer(user, question))
        return world
    _cited(world, echo)
    _two_parts(world, echo)
    _narrowed(world, echo)
    _identity(world, echo)
    _deleted(world, echo)
    _source_down(world, echo)
    _personal_data(world, echo)
    return world


def _options(world: AnswerWorld, echo: Echo) -> None:
    _say(
        echo,
        "0. Q1 plans the searches, Q2 + Q3 rank what the asker may read by the index's "
        "copies of the permissions (S13), and Q4 asks the source before anything is "
        "released. The options come from config/:",
        indent="",
    )
    echo(f"   profile {world.query.profile}:")
    for choice in world.query.steps:
        echo(f"     {choice.describe()}")
    echo(f"     access check {world.knowledge.q4.postcheck_id}")
    _say(echo, f"{encoder_note(world.write, world.encoder)}.", indent="     ")
    echo("")


def _cited(world: AnswerWorld, echo: Echo) -> None:
    echo(f"1. A cited answer ({len(world.stores.search.chunks())} chunks loaded by the worker):")
    _show(echo, world.answer("alice", LEAVE))
    _say(
        echo,
        "Q4 read alice from the identity provider and each page from the docs tool: all "
        "confirmed, so every passage is released with a citation. This is what an agent "
        "receives (S15): the passages, the citations and the notes. Nothing else.",
    )
    echo("")


def _two_parts(world: AnswerWorld, echo: Echo) -> None:
    echo("2. A question in two parts:")
    _show(echo, world.answer("alice", TWO_PARTS))
    _say(
        echo,
        "Q1 made one search per part; their rankings were merged by rank, as Q3 merges "
        "its two legs: each part's best passages reach the top 5, and a passage both "
        "searches found ranks higher. Q4 checked the merged top 5.",
    )
    echo("")


def _narrowed(world: AnswerWorld, echo: Echo) -> None:
    echo(f"3. Why a second check. dana narrows {REMOTE_PAGE}'s readers to hr-team in the docs")
    echo("   tool; the worker has not run yet, so the index still lists all-staff:")
    call(world.docs, "PUT", f"/pages/{REMOTE_PAGE}/acl", json={"groups": ["hr-team"]}, headers=DANA)
    _show(echo, world.answer("alice", REMOTE))
    _show(echo, world.answer("bob", REMOTE))
    world.sync(listings=True)
    after = world.answer("alice", REMOTE)
    found = REMOTE_PAGE in {_page(h.chunk.document_key) for h in after.hits}
    _say(
        echo,
        f"Q3 found {REMOTE_PAGE} for alice by the stale copy; the docs tool said no, and the "
        "agent's answer does not mention it. bob, in hr-team, still gets it cited. Once the "
        f"worker has copied the new readers, Q3 {'still finds' if found else 'no longer finds'} "
        f"{REMOTE_PAGE} for alice: the pre-filter keeps it out on its own.",
    )
    echo("")


def _identity(world: AnswerWorld, echo: Echo) -> None:
    echo(f"4. dana removes bob from {PAY_GROUP} and deactivates {LEFT} in the identity provider;")
    echo("   the identity sync has not run, so the permission store still has the old groups:")
    call(world.idp, "DELETE", f"/scim/v2/Groups/{PAY_GROUP}/members/bob", headers=DANA)
    call(world.idp, "POST", f"/scim/v2/Users/{LEFT}/deactivate", headers=DANA)
    _show(echo, world.answer("bob", SALARY))
    _show(echo, world.answer(LEFT, LEAVE))
    world.sync(listings=True)
    _say(
        echo,
        "Q2 read the copied groups, so Q3 still ranked the salary bands for bob and "
        f"olivia's pages for her. Q4 read both people from the identity provider itself: "
        f"bob is no longer in {PAY_GROUP}, olivia has left. Nothing they may no longer read "
        "was released. The worker has now synced both changes.",
    )
    echo("")


def _deleted(world: AnswerWorld, echo: Echo) -> None:
    echo(f"5. dana deletes {OLD_TRAVEL} in the docs tool; the worker has not purged it yet:")
    call(world.docs, "DELETE", f"/pages/{OLD_TRAVEL}", headers=DANA)
    _show(echo, world.answer("alice", TRAVEL))
    world.sync(listings=True)
    _say(
        echo,
        f"The index still held {OLD_TRAVEL}; the docs tool answered 404, so Q4 held it back "
        "as deleted. The worker has now purged it.",
    )
    echo("")


def _source_down(world: AnswerWorld, echo: Echo) -> None:
    echo("6. The docs tool does not answer (here, a connection that times out):")

    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("no answer", request=request)

    with httpx.Client(
        base_url="http://docs.invalid", transport=httpx.MockTransport(timeout)
    ) as down:
        knowledge: KnowledgeQuery = build_query(
            world.query, world.stores, access_check(down, world.idp), world.encoder
        )
        _show(echo, knowledge.ask("alice", LEAVE))
    _say(
        echo,
        "Unknown is no: nothing could be confirmed, so nothing was released. The agent is "
        "told how many passages could not be checked, never which pages, so it can say the "
        "answer may be incomplete instead of saying there is no such policy.",
    )
    echo("")


def _personal_data(world: AnswerWorld, echo: Echo) -> None:
    page = PAYROLL_PAGE
    echo(f"7. Personal data. dana creates {page['id']} '{page['title']}' for hr-team (synthetic):")
    call(world.docs, "POST", "/pages", json=page, headers=DANA)
    world.sync(listings=True)
    _show(echo, world.answer("bob", PAYROLL), full=f"hr_docs:{page['id']}")
    _say(
        echo,
        "The IBAN identifies a person's bank account, so Q4 replaced it with a marker, for "
        "every asker. The email address and phone number stay: an HR page that gives the "
        "payroll team's contacts must stay usable. The index keeps the text as it is; only "
        "what is released changes.",
    )


# -------------------------------------------------------------------------- CLI


@demo_app.command("answer")
def answer(
    question: Annotated[
        str | None, typer.Argument(help="Your own question (default: the scripted story).")
    ] = None,
    user: Annotated[
        list[str] | None,
        typer.Option("--user", help="Who asks your question; repeat for several (default: alice)."),
    ] = None,
    models: Annotated[
        bool,
        typer.Option("--models", help="Embed questions with the profile's T7 model (bge-m3)."),
    ] = False,
    docker: DockerFlag = False,
    url: Annotated[str | None, typer.Option(help="A running mock docs service.")] = None,
    hr_url: Annotated[str | None, typer.Option(help="A running mock HR API.")] = None,
    idp_url: Annotated[str | None, typer.Option(help="A running mock identity provider.")] = None,
) -> None:
    """S14 · Q4 + Q1: cited answers, re-checked against the source before release."""
    try:
        with run_namespace(docker) as namespace:
            settings = load_settings()
            write, gateway, note = demo_choices(
                settings, models, docker=docker, namespace=namespace
            )
            if note:
                typer.secho(f"Note: {note}.\n", fg="yellow")
            with ExitStack() as stack:
                docs = stack.enter_context(mock_client(DOCS, url))
                hr = stack.enter_context(mock_client(HR, hr_url))
                idp = stack.enter_context(mock_client(IDP, idp_url))
                db_path = Path(stack.enter_context(tempfile.TemporaryDirectory())) / "hr.sqlite"
                run_mock_command(DB, ["init", "--path", str(db_path)])
                run_answer_demo(
                    docs,
                    hr,
                    idp,
                    db_path,
                    question=question,
                    users=tuple(user or ("alice",)),
                    write=write,
                    query=choose_query(settings),
                    gateway=gateway,
                )
    except (MockStartError, httpx.HTTPError, LookupError, GatewayError, ValueError, OSError) as exc:
        raise fail(exc) from exc
