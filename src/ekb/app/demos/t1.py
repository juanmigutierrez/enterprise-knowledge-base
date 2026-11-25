"""S2 / S2a · Connect + T1: every source's changes become one ``ChangeEvent`` shape.

``ekb demo t1`` tells the docs story (S2): webhooks, then the full listing
catching a lost delete. ``ekb demo t1 --all`` (S2a) makes one change in each
of the four sources and shows the four events side by side.

Connectors are wired by hand here and in ``ekb.app.connectors``; choosing them
per source in config, and running them as a service, needs a durable bus (S17).
"""

from __future__ import annotations

import sqlite3
import tempfile
from collections.abc import Callable
from contextlib import ExitStack, closing
from pathlib import Path
from typing import Annotated, Any

import httpx
import typer

from ekb.app.demos._app import demo_app
from ekb.app.demos._mocks import Echo, MockStartError, call, fail, mock_client, run_mock_command
from ekb.app.demos._serve import serving
from ekb.connect.docs_api import DocsApi
from ekb.connect.hr_api import HrApi
from ekb.connect.hr_db import CdcReader
from ekb.connect.identity_api import IdentityApi
from ekb.connect.normalize.docs import version_of
from ekb.connect.options.docs_full_listing import DocsFullListingConnector
from ekb.connect.options.docs_webhook import DocsWebhookConnector
from ekb.connect.options.hr_api_delta import HrApiDeltaConnector
from ekb.connect.options.hr_api_full_listing import HrApiFullListingConnector
from ekb.connect.options.hr_db_cdc import HrDbCdcConnector
from ekb.connect.options.identity_sync import IdentitySyncConnector
from ekb.connect.people import PersonMap, load_people
from ekb.connect.receiver import DOCS_WEBHOOK_PATH, create_receiver_app
from ekb.contracts import ChangeEvent, ChangeOp

DOCS, HR_API, IDP, HR_DB = (
    "sources.mock_docs",
    "sources.mock_hr_api",
    "sources.mock_idp",
    "sources.mock_hr_db",
)
NEW_PAGE_ID = "HR-PL-11"
NEW_PAGE = {
    "id": NEW_PAGE_ID,
    "title": "Wellbeing allowance",
    "language": "en",
    "department": "people",
    "body": "# Wellbeing allowance\n\nEvery employee can claim up to EUR 300 a year for sport.\n",
    "acl": {"groups": ["all-staff"]},
}
EDITED_PAGE = "HR-PL-04"  # Remote work, Milan
REVOKED_PAGE = "HR-PL-05"  # Teletrabajo, Madrid: hr-team loses access
DELETED_PAGE = "HR-PL-99"  # the superseded 2024 travel policy
DANA, LUCIA, BOB = ({"X-Actor": name} for name in ("dana", "lucia", "bob"))


class InMemoryOriginals:
    """Keeps fetched originals and returns their pointer. The raw store (S3) replaces it."""

    def __init__(self) -> None:
        self.saved: dict[str, bytes] = {}

    def __call__(self, document_key: str, version: str, original: bytes) -> str:
        pointer = f"mem://raw/{document_key.replace(':', '/')}/{version}"
        self.saved[pointer] = original
        return pointer


class KnownDocuments:
    """Every event received, and what the knowledge base believes each source holds.

    The document registry (S3) does this for real; the full listings compare
    the sources against it.
    """

    def __init__(self) -> None:
        self.events: list[ChangeEvent] = []
        self._versions: dict[str, dict[str, str]] = {}

    def __call__(self, event: ChangeEvent) -> None:
        self.events.append(event)
        versions = self._versions.setdefault(event.source, {})
        if event.op is ChangeOp.DELETE:
            versions.pop(event.source_id, None)
        else:
            versions[event.source_id] = event.version

    def seed(self, source: str, versions: dict[str, str]) -> None:
        self._versions[source] = dict(versions)

    def of(self, source: str) -> Callable[[], dict[str, str]]:
        return lambda: dict(self._versions.get(source, {}))

    def knows(self, source: str, source_id: str) -> bool:
        return source_id in self._versions.get(source, {})

    def count(self, source: str) -> int:
        return len(self._versions.get(source, {}))


def _readers(event: ChangeEvent) -> str:
    names = sorted(event.acl.groups) + sorted(f"user:{u}" for u in event.acl.users)
    return ",".join(names) or "-"


def _short(value: str | None) -> str:
    return value.removeprefix("sha256:")[:10] if value else "-"


def _version(version: str) -> str:
    """Short enough for one line: a timestamp version (the HR API's) shows its time."""
    return f"@{version[11:19]}" if len(version) > 10 and version[10:11] == "T" else f"v{version}"


def _event_line(event: ChangeEvent) -> str:
    return (
        f"-> {event.op.value:<10} {event.document_key:<34} {_version(event.version):<9} "
        f"person={event.person_id or '-':<6} by={event.changed_by or '-':<6} "
        f"acl={_readers(event)}"
    )


def _change_line(change: dict[str, Any]) -> str:
    return (
        f"#{change['seq']:<3} {change['event']:<25} {change['page_id']:<9} "
        f"rev={change['rev']} acl_rev={change['acl_rev']} actor={change.get('actor') or '-'}"
    )


def _people(idp: httpx.Client, hr: httpx.Client | None = None) -> PersonMap:
    return PersonMap(load_people(IdentityApi(idp), HrApi(hr) if hr else None))


# --------------------------------------------------------------------------- S2


def run_t1(docs: httpx.Client, idp: httpx.Client, echo: Echo = typer.echo) -> None:
    """The S2 story: the docs tool's four kinds of change, then a lost webhook."""
    call(docs, "POST", "/reset")
    call(idp, "POST", "/reset")
    api = DocsApi(docs)
    known = KnownDocuments()
    known.seed("hr_docs", {p.id: version_of(p.rev, p.acl_rev) for p in api.list_pages()})
    originals = InMemoryOriginals()
    people = _people(idp)
    webhook = DocsWebhookConnector(api, known, originals, people, known=known.of("hr_docs"))
    listing = DocsFullListingConnector(api, known, originals, people, known=known.of("hr_docs"))

    echo("S2 · Connect + T1 normalize: every change becomes one ChangeEvent\n")
    with serving(create_receiver_app(webhook.receive)) as receiver:
        hook = call(docs, "POST", "/subscriptions", json={"url": receiver.url + DOCS_WEBHOOK_PATH})
        try:
            _webhook_part(docs, echo, webhook, known, receiver.url)
            receiver.stop()
            _listing_part(docs, echo, webhook, listing, known, hook["id"])
        finally:
            call(docs, "DELETE", f"/subscriptions/{hook['id']}")

    echo("\nNext: `ekb demo t1 --all` does the same for the other three sources.")


def _webhook_part(
    docs: httpx.Client,
    echo: Echo,
    webhook: DocsWebhookConnector,
    known: KnownDocuments,
    receiver_url: str,
) -> None:
    echo(f"0. The connector's webhook receiver listens on {receiver_url}{DOCS_WEBHOOK_PATH}")
    echo("   and is subscribed to the docs tool. The knowledge base knows")
    echo(f"   {known.count('hr_docs')} documents (version = rev.acl_rev).\n")

    cursor = call(docs, "GET", "/changes")["cursor"]
    echo("1. Four things happen in the docs tool:")
    call(docs, "POST", "/pages", json=NEW_PAGE, headers=DANA)
    echo(f"   dana   adds    {NEW_PAGE_ID} (Wellbeing allowance), readable by all-staff")
    call(
        docs, "PUT", f"/pages/{EDITED_PAGE}", json={"body": "Friday is remote too.\n"}, headers=DANA
    )
    echo(f"   dana   edits   {EDITED_PAGE} (Remote work, Milan)")
    call(
        docs, "PUT", f"/pages/{REVOKED_PAGE}/acl", json={"groups": ["es-employees"]}, headers=LUCIA
    )
    echo(f"   lucia  revokes {REVOKED_PAGE} (Teletrabajo, Madrid): hr-team loses access")
    call(docs, "DELETE", f"/pages/{DELETED_PAGE}", headers=DANA)
    echo(f"   dana   deletes {DELETED_PAGE} (Business travel 2024, superseded)\n")

    echo(f"2. Each one was POSTed to the receiver at once; {webhook.pending} notifications")
    echo("   are queued. Receiving only queues, so the docs tool gets its answer fast.\n")

    changes = call(docs, "GET", "/changes", params={"since": cursor})["changes"]
    first = len(known.events)
    emitted = webhook.sync()
    echo(f"3. sync() fetches each page as it is now and normalizes it (T1): {emitted} events.")
    echo("   What the docs tool said  ->  the ChangeEvent everything downstream sees:\n")
    for change, event in zip(changes, known.events[first:], strict=True):
        echo(f"   {_change_line(change)}")
        echo(f"      {_event_line(event)}")
    echo("")
    revoke = next(e for e in known.events[first:] if e.op is ChangeOp.ACL_CHANGE)
    rev, acl_rev = revoke.version.split(".")
    echo("   - Four dialects of 'something changed' became three ops: upsert, acl_change, delete.")
    echo("   - An upsert points to the stored original; later steps read that, not the source.")
    echo(
        f"   - The revoke is acl_change (v{revoke.version}: rev still {rev}, "
        f"acl_rev now {acl_rev}),"
    )
    echo("     so permissions can be updated without re-reading the text (T2 fast lane, S4).")
    echo("   - The delete carries nothing about the page, only who deleted it (deletion gate).")
    echo("   - by= is the identity provider ID of whoever made the change.\n")

    upsert = next(e for e in known.events[first:] if e.op is ChangeOp.UPSERT)
    echo("4. One event in full. Every connector, for every source type, produces this shape:")
    for line in upsert.model_dump_json(indent=2).splitlines():
        echo(f"   {line}")
    echo("")


def _listing_part(
    docs: httpx.Client,
    echo: Echo,
    webhook: DocsWebhookConnector,
    listing: DocsFullListingConnector,
    known: KnownDocuments,
    hook_id: int,
) -> None:
    page = NEW_PAGE_ID
    echo(f"5. Now the receiver goes down, and dana deletes {page}, the page added in step 1.")
    call(docs, "DELETE", f"/pages/{page}", headers=DANA)
    hook = next(s for s in call(docs, "GET", "/subscriptions") if s["id"] == hook_id)
    echo(f"   The docs tool tried once: delivered={hook['delivered']} failed={hook['failed']}.")
    echo("   It does not retry; that notification is lost.")
    echo(f"   webhook sync() -> {webhook.sync()} events. The knowledge base still believes")
    echo(f"   {page} exists: {known.knows('hr_docs', page)}.\n")

    first = len(known.events)
    emitted = listing.sync()
    echo(
        "6. The full listing compares every page in the source with what is known: "
        f"{emitted} event."
    )
    for event in known.events[first:]:
        echo(f"   {_event_line(event)}")
    echo(f"   {page} known now: {known.knows('hr_docs', page)}. Without the listing it would have")
    echo("   stayed searchable. by=- : a listing sees that the page is gone, not who removed it.")
    echo(f"   Run again, nothing left to fix: full listing sync() -> {listing.sync()} events.")


# -------------------------------------------------------------------------- S2a


def run_t1_all(
    docs: httpx.Client,
    hr: httpx.Client,
    idp: httpx.Client,
    db_path: Path,
    echo: Echo = typer.echo,
) -> None:
    """The S2a story: one change in each of the four sources, four events of one shape."""
    for client in (docs, hr, idp):
        call(client, "POST", "/reset")
    known = KnownDocuments()
    originals = InMemoryOriginals()
    people = _people(idp, hr)
    docs_api = DocsApi(docs)
    known.seed("hr_docs", {p.id: version_of(p.rev, p.acl_rev) for p in docs_api.list_pages()})
    webhook = DocsWebhookConnector(docs_api, known, originals, people, known=known.of("hr_docs"))
    delta = HrApiDeltaConnector(HrApi(hr), known, originals, people)
    hr_listing = HrApiFullListingConnector(
        HrApi(hr), known, originals, people, known=known.of("hr_api")
    )
    cdc = HrDbCdcConnector(CdcReader(db_path), known, originals, people)
    identity = IdentitySyncConnector(IdentityApi(idp), known, originals, people)

    echo("S2a · Connect + T1 for every source type: push, pull, CDC and identity\n")
    mapped, total = people.coverage()
    echo("0. One person ID everywhere: the identity provider's user ID.")
    echo(f"   HR employees mapped to identity users (by full name): {mapped}/{total}.")
    echo(f"   Gaps: {', '.join(people.gaps()) or 'none'}.\n")

    echo("1. Initial load: each connector reads its source once.")
    loads = [
        ("docs tool", "webhooks + listing", "already known", known.count("hr_docs")),
        ("HR record API", "delta polling", "first poll", delta.sync()),
        ("HR database", "change data capture", "snapshot", cdc.sync()),
        ("identity provider", "identity sync", "listing", identity.sync()),
    ]
    for source, how, what, count in loads:
        echo(f"   {source:<18} {how:<20} {what:<14} {count:>3} items")
    echo("")

    with serving(create_receiver_app(webhook.receive)) as receiver:
        hook = call(docs, "POST", "/subscriptions", json={"url": receiver.url + DOCS_WEBHOOK_PATH})
        try:
            echo("2. One change in each source:")
            call(docs, "PUT", f"/pages/{EDITED_PAGE}", json={"body": "Friday too.\n"}, headers=DANA)
            echo(
                f"   docs tool          dana   edits {EDITED_PAGE} (Remote work, Milan)       push"
            )
            call(hr, "PATCH", "/employees/E-1008", json={"manager_id": "E-1009"}, headers=LUCIA)
            echo("   HR record API      lucia  gives E-1008 (Javier) a new manager     pull")
            _sql(
                db_path,
                "UPDATE leave_balances SET days_taken = days_taken + 2, changed_by = 'dana' "
                "WHERE employee_id = 'E-1001' AND year = 2026",
            )
            echo("   HR database        dana   records 2 days of leave for E-1001      CDC")
            call(idp, "DELETE", "/scim/v2/Groups/it-employees/members/alice", headers=BOB)
            echo("   identity provider  bob    removes alice from it-employees         identity\n")

            first = len(known.events)
            counts = [webhook.sync(), delta.sync(), cdc.sync(), identity.sync()]
        finally:
            call(docs, "DELETE", f"/subscriptions/{hook['id']}")

    echo(f"3. Each connector syncs ({' + '.join(map(str, counts))} events).")
    echo("   Four sources, one shape:\n")
    for event in known.events[first:]:
        echo(f"   {_event_line(event)}")
    echo("")
    echo("   - Same fields, same meaning, whatever the source: nothing after T1 needs to")
    echo("     know whether a change was pushed, polled, captured or synced.")
    echo("   - person= is who the item is about, by= who changed it: both are identity")
    echo("     provider IDs, even where the source said E-1001.")
    echo("   - The leave row is readable by alice and hr-team only; the rest by all-staff.")
    echo("   - Versions differ by source (rev.acl_rev, a time @.., a CDC seq, a feed seq);")
    echo("     they only need to order one item's history.")
    echo("   - alice's group change is an upsert of her user record. Which pages she lost")
    echo("     is worked out later, by permission resolution (T8, S11).\n")

    leave = next(e for e in known.events[first:] if e.source == "hr_db")
    echo("4. The leave row's event in full:")
    for line in leave.model_dump_json(indent=2).splitlines():
        echo(f"   {line}")
    echo("")

    echo("5. lucia deletes E-1012 (Olivia Chen) in the HR record API.")
    call(hr, "DELETE", "/employees/E-1012", headers=LUCIA)
    echo(f"   delta sync() -> {delta.sync()} events: the API keeps no trace of a delete.")
    first = len(known.events)
    emitted = hr_listing.sync()
    echo(f"   full listing sync() -> {emitted} event{'s' * (emitted != 1)}:")
    for event in known.events[first:]:
        echo(f"   {_event_line(event)}")
    unmapped = ", ".join(sorted(people.unmapped)) or "none"
    echo(f"   Unmapped people reported during the run: {unmapped}.")
    echo("\nTry it yourself: run a mock (`uv run python -m sources.mock_hr_api`), change")
    echo("something with an X-Actor header in its /docs page, and pass its --*-url here.")


def _sql(db_path: Path, statement: str) -> None:
    """Play the HR application: one write, naming the writer in ``changed_by``.

    Like ``ekb demo s1d``, the demo writes with SQL directly: it is the
    application here, not the product (the connector only ever reads).
    """
    with closing(sqlite3.connect(db_path)) as conn, conn:
        conn.execute(statement)


# -------------------------------------------------------------------------- CLI


@demo_app.command("t1")
def t1(
    all_sources: Annotated[
        bool, typer.Option("--all", help="S2a: one change in each of the four sources.")
    ] = False,
    url: Annotated[
        str | None, typer.Option(help="A running mock docs service, e.g. http://127.0.0.1:8081.")
    ] = None,
    hr_url: Annotated[
        str | None, typer.Option(help="A running mock HR API, e.g. http://127.0.0.1:8082.")
    ] = None,
    idp_url: Annotated[
        str | None, typer.Option(help="A running mock identity provider (port 8083).")
    ] = None,
) -> None:
    """S2 · T1: the docs tool's changes as events; --all: every source type (S2a)."""
    try:
        with ExitStack() as stack:
            docs = stack.enter_context(mock_client(DOCS, url))
            idp = stack.enter_context(mock_client(IDP, idp_url))
            if not all_sources:
                run_t1(docs, idp)
                return
            hr = stack.enter_context(mock_client(HR_API, hr_url))
            folder = stack.enter_context(tempfile.TemporaryDirectory())
            db_path = Path(folder) / "hr.sqlite"
            run_mock_command(HR_DB, ["init", "--path", str(db_path)])
            run_t1_all(docs, hr, idp, db_path)
    except (MockStartError, httpx.HTTPError) as exc:
        raise fail(exc) from exc
