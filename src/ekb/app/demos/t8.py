"""S11 · T8 Permissions: why nothing is searchable before permissions are set.

``ekb demo t8`` connects the identity provider (S1c) and the docs tool (S2) to
the change log (S3). The identity sync fills the **permission store** with who
is in which group; the pages run T2 to T8 (S4 to S10 with no model: no
context header, the ``rules`` classifier, fake vectors) and T8 publishes each
chunk to the **search store** with its page's readers. Then the story:

1. a new page, stopped between T7 and T8 (card 4.14's publication test): no one
   finds it, and the search store refuses it; T8 runs, and only its readers do;
2. who can read what, for all twelve users;
3. alice leaves a group: she loses three pages, and no chunk is rewritten;
4. olivia leaves the company: she reads nothing, though she keeps her groups;
5. lucia changes a page's readers: the fast lane, nothing embedded;
6. dana deletes a page: it is gone from search.

The demo runs T8 itself, right after T7, so it can stop the line between them;
the inspector and the tests run it inside ``chunk_after_t3(t8=...)``. It needs
no model, key or network: what the vectors mean does not matter here.
"""

from __future__ import annotations

import textwrap
from collections import Counter
from contextlib import ExitStack
from dataclasses import replace
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
from ekb.app.embed import embed_step
from ekb.app.parse import DOCUMENT_SOURCES, parse_after_t2, parse_step
from ekb.app.permissions import (
    membership_sync,
    permission_step,
    permission_store,
    readers_after_t2,
    search_store,
    unpublisher,
)
from ekb.changelog.bus.port import Delivery
from ekb.connect.docs_api import DocsApi
from ekb.connect.identity_api import IdentityApi
from ekb.connect.options.docs_full_listing import DocsFullListingConnector
from ekb.connect.options.docs_webhook import DocsWebhookConnector
from ekb.connect.options.identity_sync import IdentitySyncConnector
from ekb.connect.people import PersonMap, load_people
from ekb.connect.receiver import DOCS_WEBHOOK_PATH, create_receiver_app
from ekb.contracts import DiffAction
from ekb.process.t2_diff.step import DiffResult
from ekb.process.t8_permissions.step import PermissionResult, T8Action, describe
from ekb.stores.search.port import UnpublishableError

DOCS, IDP = "sources.mock_docs", "sources.mock_idp"
SOURCE = "hr_docs"
WIDTH = 88
USERS = (
    *("alice", "bob", "dana", "marco", "giulia", "luca"),  # Milan
    *("carla", "javier", "lucia"),  # Madrid
    *("emma", "noah", "olivia"),  # Toronto
)
"""The twelve synthetic users, by office, as ``directory.yaml`` lists them."""
DANA, LUCIA = ({"X-Actor": name} for name in ("dana", "lucia"))
NEW = "HR-PL-11"
NEW_PAGE = {
    "id": NEW,
    "title": "People team: handling a grievance",
    "language": "en",
    "department": "people",
    "acl": {"groups": ["hr-team"]},
    "body": (
        "# People team: handling a grievance\n\n"
        "## First contact\nAcknowledge the grievance in writing within two working days.\n\n"
        "## Hearing\nInvite the employee to a hearing; they may bring a colleague.\n"
    ),
}
"""Synthetic: a page only the People team (``hr-team``) may read."""
REGROUPED, LEFT, REVOKED, DELETED = "alice", "olivia", "HR-PL-05", "HR-PL-99"


class Story:
    """The wiring: identity sync + docs connectors -> change log -> T2 -> T3 ... T8."""

    def __init__(self, docs: httpx.Client, idp: httpx.Client, echo: Echo) -> None:
        self.docs, self.idp, self.echo = docs, idp, echo
        self.changelog = ChangeLog.in_memory(partitions=3)
        self.permissions = permission_store(current_lifetime=self.changelog.current_lifetime)
        self.index = search_store(current_lifetime=self.changelog.current_lifetime)
        self.t8 = permission_step(self.permissions, self.index)
        self.memberships = membership_sync(self.changelog, self.permissions)
        self.paused = False
        self.held: list[Processed] = []
        self.published: list[PermissionResult] = []
        self.fast: list[PermissionResult] = []
        self.decided: list[tuple[DiffResult, Processed | None]] = []
        self.removed: dict[str, int] = {}
        self._pending: Processed | None = None
        unpublish = unpublisher(self.index, self.permissions, self.memberships)

        def forget(document_key: str) -> int:
            self.removed[document_key] = unpublish(document_key)
            return 0

        # T8 is run by _keep, right after T7, so the story can stop the line between them.
        then = chunk_after_t3(
            chunk_step(),
            self._keep,
            classify_step(),
            t7=embed_step("fake"),
            memberships=self.memberships,
        )
        parse = parse_after_t2(parse_step(self.changelog), then, forgetter(None, forget))
        self.t2 = diff_step(
            self.changelog, readers_after_t2(self.t8, parse, DOCUMENT_SOURCES, self.fast.append)
        )
        sink, store = self.changelog.publish, self.changelog.store_original
        people = PersonMap(load_people(IdentityApi(idp)))
        api, known = DocsApi(docs), self.changelog.known(SOURCE)
        self.identity = IdentitySyncConnector(IdentityApi(idp), sink, store, people)
        self.webhook = DocsWebhookConnector(api, sink, store, people, known=known)
        self.listing = DocsFullListingConnector(api, sink, store, people, known=known)

    def _keep(self, processed: Processed) -> None:
        self._pending = processed
        if processed.vectors is None or processed.chunks is None:
            return  # a record: an identity user filled the memberships
        if self.paused:
            self.held.append(processed)
            return
        self._pending = self._publish(processed)

    def _publish(self, processed: Processed) -> Processed:
        document = processed.parse.parsed  # a document: only those reach T7
        if document is None:
            raise LookupError(f"{processed.parse.event.document_key} is not a document")
        result = self.t8.publish(processed.parse.event, document, processed.final)
        self.published.append(result)
        return replace(processed, published=result)

    def resume(self) -> list[Processed]:
        """T8 on everything that waited after T7."""
        done = [self._publish(processed) for processed in self.held]
        self.held.clear()
        return done

    def run(self) -> list[tuple[DiffResult, Processed | None]]:
        """T2 drains its group; T3 to T8 run on whatever T2 sends on."""
        new: list[tuple[DiffResult, Processed | None]] = []

        def seen(_: Delivery, result: DiffResult) -> None:
            new.append((result, self._pending))
            self._pending = None

        run_t2(self.changelog, self.t2, seen)
        self.decided.extend(new)
        return new

    def pages_for(self, user: str) -> set[str]:
        """The pages ``user`` would find something of now."""
        chunks = self.index.readable_by(self.permissions.principals(user))
        return {chunk.document_key.removeprefix(f"{SOURCE}:") for chunk in chunks}

    def embedded(self, decided: list[tuple[DiffResult, Processed | None]]) -> int:
        return sum(p.vectors.sent for _, p in decided if p is not None and p.vectors is not None)


def _say(echo: Echo, text: str, indent: str = "   ") -> None:
    for line in textwrap.wrap(text, width=WIDTH, initial_indent=indent, subsequent_indent=indent):
        echo(line)


def _people(names: list[str] | tuple[str, ...]) -> str:
    return ", ".join(names) or "no one"


def _count(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


# ------------------------------------------------------------------------ scenes


def run_t8_demo(docs: httpx.Client, idp: httpx.Client, echo: Echo = typer.echo) -> Story:
    """The whole story against the docs tool and the identity provider. Returns it."""
    call(docs, "POST", "/reset")
    call(idp, "POST", "/reset")
    story = Story(docs, idp, echo)

    echo("S11 · T8 Permissions: why nothing is searchable before permissions are set\n")
    _say(
        echo,
        "0. T8 copies each page's readers (user and group IDs, from the docs tool) onto its "
        "chunks and publishes them to the search store, but only chunks with readers, a "
        "sensitivity (T5) and a vector (T7): the publication gate. Groups stay groups. Who "
        "is in them comes from the identity provider, kept in the permission store, and is "
        "looked up when someone asks: an inactive or unknown user reads nothing.",
        indent="",
    )
    echo(f"   Resolver: {story.t8.resolver_id}; stores: in-memory; vectors: fake (no model).\n")
    _initial_load(story)
    with serving(create_receiver_app(story.webhook.receive)) as receiver:
        hook = call(docs, "POST", "/subscriptions", json={"url": receiver.url + DOCS_WEBHOOK_PATH})
        try:
            _paused(story)
            _grid(story)
            _group_change(story)
            _leaver(story)
            _fast_lane(story)
            _delete(story)
        finally:
            call(docs, "DELETE", f"/subscriptions/{hook['id']}")
    _summary(story)
    return story


def _initial_load(story: Story) -> None:
    echo = story.echo
    echo("1. Initial load: identity sync -> permission store; pages -> T2 ... T7 -> T8.")
    users = story.identity.sync()
    story.run()
    active = sum(1 for m in story.permissions.memberships() if m.active)
    echo(f"   identity sync() -> {users} user records -> {active} active memberships")
    echo(f"   full listing sync() -> {story.listing.sync()} page events")
    for _, processed in story.run():
        if processed is None or processed.published is None:
            continue
        result = processed.published
        echo(
            f"   {processed.parse.event.source_id:<10} {len(result.chunks)} chunks  "
            f"readers {describe(result.record.acl):<26} "
            f"{_count(len(result.readers), 'person', 'people'):>9}"
        )
    chunks = story.index.chunks()
    ready = sum(1 for chunk in chunks if chunk.publishable)
    echo(
        f"   {len(chunks)} chunks published from {len(story.index.documents())} pages; "
        f"{ready} of {len(chunks)} have readers, a sensitivity and a vector.\n"
    )


def _paused(story: Story) -> None:
    echo, key = story.echo, f"{SOURCE}:{NEW}"
    echo("2. The publication gate (card 4.14): the line stops between T7 and T8.")
    echo(f"   dana creates {NEW} '{NEW_PAGE['title']}', readers hr-team:")
    story.paused = True
    call(story.docs, "POST", "/pages", json=NEW_PAGE, headers=DANA)
    story.webhook.sync()
    story.run()
    (held,) = story.held
    chunk = held.final[0]
    _say(
        echo,
        f"T2 process -> T3 ... T7 ran: {len(held.final)} chunks, each with a sensitivity "
        f"({chunk.sensitivity}) and a vector, and no readers: publishable {chunk.publishable}.",
    )
    echo(f"   In the search store: {len(story.index.chunks(key))} chunks of {NEW}.")
    finds = "bob" if NEW in story.pages_for("bob") else "nothing"
    echo(f"   bob is in hr-team, and finds {finds} of it.")
    try:
        story.index.publish(key, held.final)
        echo("   Publishing them anyway: accepted (this must never happen).")
    except UnpublishableError as exc:
        _say(echo, f"Publishing them anyway: refused: {exc}.")
    story.paused = False
    (done,) = story.resume()
    if done.published is None:
        raise LookupError(f"T8 did not publish {NEW}")
    echo(f"   T8 resumes: {done.published.reason.split(':')[0]}.")
    readers = [u for u in USERS if NEW in story.pages_for(u)]
    alice = "finds it too (a leak)" if NEW in story.pages_for("alice") else "finds nothing"
    echo(f"   Who finds it now: {_people(readers)}. alice, not in hr-team, {alice}.")
    _say(
        echo,
        "Until T8 has run, a chunk has no readers, so it cannot be filtered by them: the "
        "only safe place for it is outside the index. The store checks the gate again at "
        "the door, so a bug upstream cannot publish a chunk anyone could find.",
    )
    echo("")


def _grid(story: Story) -> None:
    echo = story.echo
    pages = [key.removeprefix(f"{SOURCE}:") for key in story.index.documents()]
    short = [p.removeprefix("HR-").replace("-", "") for p in pages]
    echo("3. Who can read what now (x = finds the page; readers x current groups):")
    echo("   " + " " * 7 + "".join(f"{name:>6}" for name in short))
    for user in USERS:
        found = story.pages_for(user)
        echo(f"   {user:<7}" + "".join(f"{'x' if p in found else '.':>6}" for p in pages))
    _say(
        echo,
        "CMP01 (salary bands) is for hr-compensation: bob only. The Italian pages are for "
        "it-employees and hr-team, the Spanish ones for es-employees and hr-team, PL11 for "
        "hr-team. Nothing here is stored per user: each cell is the page's groups against "
        "the user's groups today.",
    )
    echo("")


def _group_change(story: Story) -> None:
    echo, user = story.echo, REGROUPED
    before = story.pages_for(user)
    chunks = story.index.chunks()
    echo(f"4. A group change: dana removes {user} from it-employees in the identity provider.")
    call(story.idp, "DELETE", f"/scim/v2/Groups/it-employees/members/{user}", headers=DANA)
    story.identity.sync()
    decided = story.run()
    for result, processed in decided:
        membership = processed.membership if processed is not None else None
        why = membership.reason if membership is not None else result.decision.reason
        echo(f"   {result.decision.event.source_id}: T2 {result.decision.action.value} -> {why}")
    after = story.pages_for(user)
    echo(
        f"   {user} finds {len(before)} pages before, {len(after)} after: lost "
        f"{', '.join(sorted(before - after)) or 'none'}."
    )
    rewritten = sum(1 for old, new in zip(chunks, story.index.chunks(), strict=True) if old != new)
    echo(
        f"   Chunks rewritten: {rewritten}. Texts embedded: {story.embedded(decided)}. "
        "The pages still say it-employees;"
    )
    echo(f"   {user} is simply not in it any more.\n")


def _leaver(story: Story) -> None:
    echo, user = story.echo, LEFT
    before = len(story.index.readable_by(story.permissions.principals(user)))
    echo(f"5. A leaver: dana deactivates {user} (S1c: deactivated, not deleted).")
    call(story.idp, "POST", f"/scim/v2/Users/{user}/deactivate", headers=DANA)
    story.identity.sync()
    for _, processed in story.run():
        if processed is not None and processed.membership is not None:
            echo(f"   {processed.membership.reason}")
    membership = story.permissions.membership(user)
    groups = ", ".join(sorted(membership.groups)) if membership else "none"
    after = len(story.index.readable_by(story.permissions.principals(user)))
    echo(f"   {user} is still in {groups}, and finds {after} chunks (was {before}).")
    _say(
        echo,
        "An inactive user has no principals at all, whatever their groups say: reactivating "
        "them restores their access without anyone re-adding groups, and until then there "
        "is nothing to filter with.",
    )
    echo("")


def _fast_lane(story: Story) -> None:
    echo, key = story.echo, f"{SOURCE}:{REVOKED}"
    readers = [u for u in USERS if REVOKED in story.pages_for(u)]
    echo(f"6. The fast lane: lucia removes hr-team from {REVOKED}'s readers in the docs tool.")
    echo(f"   Before: {_people(readers)}.")
    texts = [chunk.text for chunk in story.index.chunks(key)]
    call(
        story.docs, "PUT", f"/pages/{REVOKED}/acl", json={"groups": ["es-employees"]}, headers=LUCIA
    )
    story.webhook.sync()
    decided = story.run()
    for result, _ in decided:
        echo(
            f"   {result.decision.event.source_id} v{result.decision.event.version}: "
            f"T2 {result.decision.action.value} -> T8 fast lane"
        )
    fast = [r for r in story.fast if r.action is T8Action.READERS][-1]
    echo(f"   {fast.reason.split(':')[0]}.")
    after = [u for u in USERS if REVOKED in story.pages_for(u)]
    echo(f"   After:  {_people(after)}.")
    lost = [u for u in readers if u not in after]
    same = texts == [chunk.text for chunk in story.index.chunks(key)]
    _say(
        echo,
        f"Texts embedded: {story.embedded(decided)}; texts unchanged: {same}. "
        f"{_people(lost)} lost it; lucia keeps it through es-employees. Only the readers "
        "changed, so T2 sent the event straight to T8: a vector comes from the text, not "
        "from who may read it.",
    )
    echo("")


def _delete(story: Story) -> None:
    echo, key = story.echo, f"{SOURCE}:{DELETED}"
    readers = [u for u in USERS if DELETED in story.pages_for(u)]
    echo(f"7. A delete: dana deletes {DELETED} ({len(readers)} people could read it).")
    call(story.docs, "DELETE", f"/pages/{DELETED}", headers=DANA)
    story.webhook.sync()
    for result, _ in story.run():
        n = story.removed.get(key, 0)
        action = result.decision.action.value
        echo(f"   {DELETED}: T2 {action} -> {n} chunks out of the search store")
    finds = [u for u in USERS if DELETED in story.pages_for(u)]
    echo(
        f"   Who finds it now: {_people(finds)}. Readers record: "
        f"{'gone' if story.permissions.readers(key) is None else 'still there'}.\n"
    )


def _summary(story: Story) -> None:
    echo = story.echo
    counts = Counter(result.decision.action for result, _ in story.decided)
    line = ", ".join(f"{a.value} {counts[a]}" for a in DiffAction)
    chunks = story.index.chunks()
    ready = sum(1 for chunk in chunks if chunk.publishable)
    lanes = sum(1 for r in story.fast if r.action is T8Action.READERS)
    echo(f"8. T2 decided {len(story.decided)} events: {line}.")
    echo(
        f"   T8 published {_count(len(story.published), 'time', 'times')} and ran the fast "
        f"lane {_count(lanes, 'time', 'times')}."
    )
    echo(
        f"   Search store: {len(chunks)} chunks from {len(story.index.documents())} pages, "
        f"{ready} of them publishable:"
    )
    echo("   nothing without readers, a sensitivity and a vector is in it.")


# -------------------------------------------------------------------------- CLI


@demo_app.command("t8")
def t8(
    url: Annotated[
        str | None, typer.Option(help="A running mock docs service, e.g. http://127.0.0.1:8081.")
    ] = None,
    idp_url: Annotated[
        str | None, typer.Option(help="A running mock identity provider (port 8083).")
    ] = None,
) -> None:
    """S11 · T8 Permissions: why nothing is searchable before permissions are set."""
    try:
        with ExitStack() as stack:
            docs = stack.enter_context(mock_client(DOCS, url))
            idp = stack.enter_context(mock_client(IDP, idp_url))
            run_t8_demo(docs, idp)
    except (MockStartError, httpx.HTTPError, LookupError, ValueError) as exc:
        raise fail(exc) from exc
