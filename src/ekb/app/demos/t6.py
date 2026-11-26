"""S9 · T6 Entities: how documents get linked to people and teams.

``ekb demo t6`` connects the HR record API (S1b) and the docs tool (S2) to the
change log (S3). T2 (S4) decides every event. The HR records go to the
**entity store**: every employee becomes a person under their identity user ID,
every team a team. The pages go through T3, T4 and T5 (S5, S6, S8) and then
**T6**, which links each chunk to the people and teams it names by exact
match. The demo shows each link with the words behind it and the near misses
the rules leave alone on purpose. Then dana adds a contacts page, an employee
leaves (found by the full listing), and the page is edited and deleted.

No model is called (no context header, the ``rules`` classifier), so it runs
offline: T6's first tier needs none.
"""

from __future__ import annotations

import textwrap
from collections import Counter
from contextlib import ExitStack
from typing import Annotated

import httpx
import typer

from ekb.app.changelog import ChangeLog
from ekb.app.chunk import Processed, chunk_after_t3, chunk_step
from ekb.app.classify import classify_step, forgetter
from ekb.app.demos._app import demo_app
from ekb.app.demos._mocks import Echo, MockStartError, call, fail, mock_client
from ekb.app.demos._serve import serving
from ekb.app.diff import diff_step, run_t2
from ekb.app.entities import entity_store, entity_sync, link_step
from ekb.app.parse import parse_after_t2, parse_step
from ekb.changelog.bus.port import Delivery
from ekb.connect.docs_api import DocsApi
from ekb.connect.hr_api import HrApi
from ekb.connect.identity_api import IdentityApi
from ekb.connect.options.docs_full_listing import DocsFullListingConnector
from ekb.connect.options.docs_webhook import DocsWebhookConnector
from ekb.connect.options.hr_api_delta import HrApiDeltaConnector
from ekb.connect.options.hr_api_full_listing import HrApiFullListingConnector
from ekb.connect.people import PersonMap, load_people
from ekb.connect.receiver import DOCS_WEBHOOK_PATH, create_receiver_app
from ekb.contracts import Chunk, DiffAction, EntityKind, EntityMention
from ekb.process.t2_diff.step import DiffResult
from ekb.process.t6_entities.step import summary as links_summary

DOCS, HR, IDP = "sources.mock_docs", "sources.mock_hr_api", "sources.mock_idp"
DOCS_SOURCE, HR_SOURCE = "hr_docs", "hr_api"
CONTACTS, LEAVER, LEAVER_ID, LEAVER_NAME = "HR-PL-12", "E-1009", "person:lucia", "Lucía Navarro"
DANA, BOB = ({"X-Actor": name} for name in ("dana", "bob"))
WIDTH = 88

NEAR_MISSES = (
    ("people", "a bare word: 'People' is also a team's name"),
    ("your manager", "a role, not a name"),
    ("compensation team", "a team word, but no team of that name"),
    ("Engineering", "a team's name with no team word: left to the model tier"),
)
"""Words in the seed pages that look like a person or a team, and why T6 leaves them."""

CONTACTS_PAGE = {
    "id": CONTACTS,
    "title": "HR contacts",
    "language": "en",
    "department": "people",
    "acl": {"groups": ["all-staff"]},
    "body": (
        "# HR contacts\n\n"
        "## Milan\n"
        "Bruno Moretti leads the People team. For payroll questions, write to "
        "daniela.ferri@example.com.\n\n"
        "## Madrid\n"
        "Lucia Navarro answers questions about Spanish contracts. Marco covers when she "
        "is away.\n"
    ),
}
"""Synthetic: names and addresses from the synthetic HR records. "Lucia" is written
without its accent and "Marco" alone, on purpose."""


class Story:
    """The wiring: HR API and docs connectors -> change log -> T2 -> store / T3-T6."""

    def __init__(self, docs: httpx.Client, hr: httpx.Client, idp: httpx.Client, echo: Echo) -> None:
        self.docs, self.hr, self.echo = docs, hr, echo
        self.changelog = ChangeLog.in_memory(partitions=3)
        self.store = entity_store()
        self.records = entity_sync(self.changelog, self.store)
        self.t6 = link_step(self.store)
        self.processed: dict[str, Processed] = {}
        self.decided: list[tuple[DiffResult, Processed | None]] = []
        self._pending: Processed | None = None
        then = chunk_after_t3(chunk_step(), self._keep, classify_step(), self.t6, self.records)
        forget = forgetter(None, self._drop, self.records.forget)
        self.t2 = diff_step(
            self.changelog, parse_after_t2(parse_step(self.changelog), then, forget)
        )

        people = PersonMap(load_people(IdentityApi(idp), HrApi(hr)))
        sink, store = self.changelog.publish, self.changelog.store_original
        api = DocsApi(docs)
        known_docs = self.changelog.known(DOCS_SOURCE)
        self.webhook = DocsWebhookConnector(api, sink, store, people, known=known_docs)
        self.listing = DocsFullListingConnector(api, sink, store, people, known=known_docs)
        self.hr_delta = HrApiDeltaConnector(HrApi(hr), sink, store, people)
        self.hr_listing = HrApiFullListingConnector(
            HrApi(hr), sink, store, people, known=self.changelog.known(HR_SOURCE)
        )

    def _keep(self, processed: Processed) -> None:
        self._pending = processed
        if processed.chunks is not None:
            self.processed[processed.parse.event.document_key] = processed

    def _drop(self, document_key: str) -> None:
        self.processed.pop(document_key, None)

    def run(self) -> list[tuple[DiffResult, Processed | None]]:
        """T2 drains its group; the store or T3-T6 run on whatever T2 sends on."""
        new: list[tuple[DiffResult, Processed | None]] = []

        def seen(_: Delivery, result: DiffResult) -> None:
            new.append((result, self._pending))
            self._pending = None

        run_t2(self.changelog, self.t2, seen)
        self.decided.extend(new)
        return new

    def page(self, source_id: str) -> Processed:
        return self.processed[f"{DOCS_SOURCE}:{source_id}"]

    def kept(self) -> list[Chunk]:
        return [chunk for processed in self.processed.values() for chunk in processed.final]


def _say(echo: Echo, text: str, indent: str = "   ") -> None:
    for line in textwrap.wrap(text, width=WIDTH, initial_indent=indent, subsequent_indent=indent):
        echo(line)


def _mentions(processed: Processed) -> list[tuple[Chunk, tuple[EntityMention, ...]]]:
    links = processed.links
    if links is None:
        return []
    return list(zip(links.chunks, links.mentions, strict=True))


def _show_links(echo: Echo, processed: Processed, *, all_chunks: bool = False) -> None:
    for chunk, mentions in _mentions(processed):
        if not mentions and not all_chunks:
            continue
        if not mentions:
            echo(f"   {chunk.chunk_id:<20} no links")
        for n, m in enumerate(mentions):
            where = chunk.chunk_id if n == 0 else ""
            echo(f"   {where:<20} {m.entity_id:<13} {m.rule:<5} '{m.text}'")


# ------------------------------------------------------------------------ scenes


def run_t6_demo(
    docs: httpx.Client, hr: httpx.Client, idp: httpx.Client, echo: Echo = typer.echo
) -> Story:
    """The whole story against the HR API and the docs tool. Returns it for inspection."""
    for client in (docs, hr, idp):
        call(client, "POST", "/reset")
    story = Story(docs, hr, idp, echo)

    echo("S9 · T6 Entities: how documents get linked to people and teams\n")
    _say(
        echo,
        "0. T6 writes onto each chunk the IDs of the people and teams it names, so the access "
        "layer can later find 'everything about the People team'. The names come from the HR "
        "record API, through the change log, into the entity store. Exact match only: no model.",
        indent="",
    )
    echo(f"   Linker: {story.t6.linker_id}. Store: in memory.\n")

    _entities(story)
    _initial_load(story)
    _why(story)
    _near_misses(story)
    with serving(create_receiver_app(story.webhook.receive)) as receiver:
        hook = call(docs, "POST", "/subscriptions", json={"url": receiver.url + DOCS_WEBHOOK_PATH})
        try:
            _contacts(story)
            _leaver(story)
            _delete(story)
        finally:
            call(docs, "DELETE", f"/subscriptions/{hook['id']}")
    _summary(story)
    return story


def _entities(story: Story) -> None:
    echo = story.echo
    echo("1. Who the company knows: HR records -> change log -> T2 -> entity store.")
    echo(f"   HR delta sync() -> {story.hr_delta.sync()} events (the first poll: every record)")
    results = [p.record for _, p in story.run() if p is not None and p.record is not None]
    added = sum(1 for r in results if r.entity is not None)
    echo(f"   {added} entities added:")
    people, teams = story.store.entities(EntityKind.PERSON), story.store.entities(EntityKind.TEAM)
    for person in people:
        email = person.aliases[0] if person.aliases else ""
        echo(f"   {person.entity_id:<14} {person.name:<15} {email:<27} {person.team_id or ''}")
    echo("   " + "  ".join(f"{t.entity_id} {t.name}" for t in teams))
    bruno = next(p for p in people if p.name == "Bruno Moretti")
    _say(
        echo,
        f"The HR system calls Bruno {bruno.source_key.rpartition('/')[2]}; his entity ID is "
        f"{bruno.entity_id}, the identity provider's user ID, the same one the ACLs use (D3).",
    )
    echo("")


def _initial_load(story: Story) -> None:
    echo = story.echo
    echo("2. The pages: full listing -> T2 -> T3 -> T4 -> T5 -> T6.")
    echo(f"   docs full listing sync() -> {story.listing.sync()} events")
    for _, processed in story.run():
        if processed is None or processed.links is None:
            continue
        source_id = processed.parse.event.source_id
        echo(f"   {source_id:<10} {links_summary(processed.final)}")
    kept = story.kept()
    linked = sum(1 for chunk in kept if chunk.entity_ids)
    named = sum(1 for chunk in kept if any(e.startswith("person:") for e in chunk.entity_ids))
    echo(f"   {linked} of {len(kept)} chunks are linked; {named} of them to a person.")
    if not named:
        _say(echo, "No seed page names a person: they name teams, roles and offices.")
    echo("")


def _why(story: Story) -> None:
    echo = story.echo
    echo("3. Every link, with the words behind it:")
    for _, processed in sorted(story.processed.items()):
        _show_links(echo, processed)
    _say(
        echo,
        "A team counts only next to a team word: 'People team', 'Head of People', "
        "'equipo de People'. 'Head of People' links the team, not Bruno: turning a role into "
        "a person is the model tier's job (card 4.10).",
    )
    echo("")


def _near_misses(story: Story) -> None:
    echo = story.echo
    echo("4. Near misses: words that look like a person or a team, and are not linked.")
    for phrase, why in NEAR_MISSES:
        chunk = next((c for c in story.kept() if phrase in c.text and not _names(c, phrase)), None)
        if chunk is not None:
            echo(f"   {chunk.chunk_id:<20} '{phrase}': {why}")
    _say(
        echo,
        "Precision comes first (card 4.10): a wrong link files a page under the wrong person "
        "or team, a missed one can still be found later by a model.",
    )
    echo("")


def _names(chunk: Chunk, phrase: str) -> bool:
    """Whether the chunk's links come from ``phrase`` itself (then it is no near miss)."""
    return phrase == "Engineering" and any(e == "team:T-ENG" for e in chunk.entity_ids)


def _contacts(story: Story) -> None:
    echo = story.echo
    echo(f"5. dana creates {CONTACTS} '{CONTACTS_PAGE['title']}' naming people (synthetic):")
    call(story.docs, "POST", "/pages", json=CONTACTS_PAGE, headers=DANA)
    story.webhook.sync()
    story.run()
    _show_links(echo, story.page(CONTACTS), all_chunks=True)
    _say(
        echo,
        "A full name finds its person whatever the case or accents ('Lucia' is Lucía), and so "
        "does a work email. 'Marco' alone is not linked: a first name could be anyone.",
    )
    echo("")


def _leaver(story: Story) -> None:
    echo, contacts = story.echo, f"{DOCS_SOURCE}:{CONTACTS}"
    echo(f"6. {LEAVER_NAME} leaves: bob deletes her HR record ({LEAVER}).")
    call(story.hr, "DELETE", f"/employees/{LEAVER}", headers=BOB)
    echo(f"   HR delta sync()        -> {story.hr_delta.sync()} events (a delta never reports one)")
    echo(f"   HR full listing sync() -> {story.hr_listing.sync()} event")
    for result, _ in story.run():
        event, action = result.decision.event, result.decision.action
        then = "entity removed" if action is DiffAction.PURGE else "nothing to do"
        echo(f"   {event.source_id}: T2 {action.value} -> {then}")
    gone = story.store.get(LEAVER_ID) is None
    echo(f"   {LEAVER_ID} in the entity store: {'no' if gone else 'yes'}")
    still = [c.chunk_id for c in story.processed[contacts].final if LEAVER_ID in c.entity_ids]
    echo(f"   {CONTACTS} still links her on: {', '.join(still) or 'nothing'}")
    _say(
        echo,
        "Links are written when a page is processed. The page has not changed since, so its "
        "chunk keeps the ID, but the store no longer answers for it. This demo wires T6 by "
        "hand; the worker re-links such pages itself after its next drain (S16, ekb demo "
        "lifecycle).",
    )
    page = call(story.docs, "GET", f"/pages/{CONTACTS}")
    body = page["body"] + "\n## Toronto\nAsk the People team.\n"
    call(story.docs, "PUT", f"/pages/{CONTACTS}", json={"body": body}, headers=DANA)
    story.webhook.sync()
    story.run()
    echo(f"   dana edits {CONTACTS}; T6 links it again:")
    _show_links(echo, story.page(CONTACTS), all_chunks=True)
    _say(
        echo,
        "'Lucia Navarro' is still written on the page, but no entity answers to that name "
        "any more, so it is no link.",
    )
    echo("")


def _delete(story: Story) -> None:
    echo = story.echo
    echo(f"7. dana deletes {CONTACTS}:")
    call(story.docs, "DELETE", f"/pages/{CONTACTS}", headers=DANA)
    story.webhook.sync()
    for result, _ in story.run():
        event = result.decision.event
        echo(f"   {event.source_id} v{event.version}: T2 {result.decision.action.value}")
    left = f"{DOCS_SOURCE}:{CONTACTS}" in story.processed
    echo(f"   its chunks, and the links on them, kept: {'yes' if left else 'no'}")
    echo("")


def _summary(story: Story) -> None:
    echo = story.echo
    counts = Counter(result.decision.action for result, _ in story.decided)
    line = ", ".join(f"{a.value} {counts[a]}" for a in DiffAction)
    people = len(story.store.entities(EntityKind.PERSON))
    teams = len(story.store.entities(EntityKind.TEAM))
    kept = story.kept()
    linked = sum(1 for chunk in kept if chunk.entity_ids)
    publishable = sum(1 for chunk in kept if chunk.publishable)
    echo(f"8. T2 decided {len(story.decided)} events: {line}.")
    echo(f"   Entity store: {people} people, {teams} teams.")
    echo(f"   Linked chunks: {linked} of {len(kept)}.")
    echo(f"   Publishable chunks: {publishable} of {len(kept)}: links open no gate; there is")
    echo("   no vector (T7) or ACL (T8) yet, so none could be searched.")


# -------------------------------------------------------------------------- CLI


@demo_app.command("t6")
def t6(
    url: Annotated[
        str | None, typer.Option(help="A running mock docs service, e.g. http://127.0.0.1:8081.")
    ] = None,
    hr_url: Annotated[str | None, typer.Option(help="A running mock HR API (port 8082).")] = None,
    idp_url: Annotated[
        str | None, typer.Option(help="A running mock identity provider (port 8083).")
    ] = None,
) -> None:
    """S9 · T6 Entities: chunks linked to the people and teams they name."""
    try:
        with ExitStack() as stack:
            docs = stack.enter_context(mock_client(DOCS, url))
            hr = stack.enter_context(mock_client(HR, hr_url))
            idp = stack.enter_context(mock_client(IDP, idp_url))
            run_t6_demo(docs, hr, idp)
    except (MockStartError, httpx.HTTPError, LookupError, ValueError) as exc:
        raise fail(exc) from exc
