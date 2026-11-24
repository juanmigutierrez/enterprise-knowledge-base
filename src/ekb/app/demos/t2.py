"""S4 · T2 Diff: what does each change still need?

``ekb demo t2`` connects the docs tool's two connectors (S2) to the change log
(S3) and runs T2 as consumer group ``t2_diff``. Each scene makes one kind of
change and shows T2's decision for every event, with its reason: a change
reported twice, a save that changes nothing, a revocation, and a delete with a
re-creation queued behind it.
"""

from __future__ import annotations

from collections import Counter
from contextlib import ExitStack
from typing import Annotated, Any

import httpx
import typer

from ekb.app.changelog import ChangeLog
from ekb.app.demos._app import demo_app
from ekb.app.demos._mocks import Echo, MockStartError, call, fail, mock_client
from ekb.app.demos._serve import serving
from ekb.app.diff import T2_GROUP, diff_step, run_t2
from ekb.changelog.bus.port import Delivery
from ekb.connect.docs_api import DocsApi
from ekb.connect.identity_api import IdentityApi
from ekb.connect.options.docs_full_listing import DocsFullListingConnector
from ekb.connect.options.docs_webhook import DocsWebhookConnector
from ekb.connect.people import PersonMap, load_people
from ekb.connect.receiver import DOCS_WEBHOOK_PATH, create_receiver_app
from ekb.contracts import DiffAction
from ekb.process.t2_diff.step import DiffResult, DiffStep

DOCS, IDP = "sources.mock_docs", "sources.mock_idp"
SOURCE = "hr_docs"
EDITED, RESAVED, REVOKED, DELETED = "HR-PL-04", "HR-PL-01", "HR-PL-05", "HR-PL-99"
DANA, LUCIA = ({"X-Actor": name} for name in ("dana", "lucia"))

NEXT = {
    DiffAction.PROCESS: "T3 parse -> ... -> T8 permissions",
    DiffAction.ACL_ONLY: "T8 permissions only (fast lane)",
    DiffAction.SKIP: "nothing",
    DiffAction.PURGE: "remove from every store",
}


class Story:
    """The demo's wiring: connectors -> change log -> T2, and what T2 decided."""

    def __init__(self, docs: httpx.Client, idp: httpx.Client, echo: Echo) -> None:
        self.docs, self.echo = docs, echo
        self.changelog = ChangeLog.in_memory(partitions=3)
        self.t2: DiffStep = diff_step(self.changelog)
        self.decided: list[tuple[Delivery, DiffResult]] = []
        api = DocsApi(docs)
        people = PersonMap(load_people(IdentityApi(idp)))
        sink, store = self.changelog.publish, self.changelog.store_original
        known = self.changelog.known(SOURCE)
        self.webhook = DocsWebhookConnector(api, sink, store, people, known=known)
        self.listing = DocsFullListingConnector(api, sink, store, people, known=known)

    def run_t2(self, show: bool = True) -> list[DiffResult]:
        """T2 drains its group; print one line per decision."""
        first = len(self.decided)
        run_t2(self.changelog, self.t2, lambda d, r: self.decided.append((d, r)))
        new = self.decided[first:]
        if show:
            for delivery, result in new:
                self.echo(f"   {_line(delivery, result)}")
        return [result for _, result in new]

    def originals(self, source_id: str) -> str:
        versions = self.changelog.rawstore.versions(f"{SOURCE}:{source_id}")
        return ", ".join(v.version for v in versions) or "none"


def _line(delivery: Delivery, result: DiffResult) -> str:
    event, decision = delivery.event, result.decision
    where = f"p{delivery.partition}@{delivery.offset}"
    plural = "s" if result.purged > 1 else ""
    purged = f"; {result.purged} original{plural} purged" if result.purged else ""
    return (
        f"{where:<6} {event.op.value:<10} {event.source_id:<9} v{event.version:<4} "
        f"-> {decision.action.value:<8} {decision.reason}{purged}"
    )


# ------------------------------------------------------------------------ scenes


def run_t2_demo(docs: httpx.Client, idp: httpx.Client, echo: Echo = typer.echo) -> ChangeLog:
    """The whole story against the docs tool. Returns the change log for inspection."""
    call(docs, "POST", "/reset")
    call(idp, "POST", "/reset")
    story = Story(docs, idp, echo)

    echo("S4 · T2 Diff: what does each change still need?\n")
    echo(f"0. T2 reads the change log as consumer group '{T2_GROUP}'. For each event it")
    echo("   compares with the registry (what was last processed) and decides:")
    for action, then in NEXT.items():
        echo(f"   {action.value:<8} -> {then}")
    echo("   Content is compared by hash, not by version: versions are only labels.\n")

    _initial_load(story)
    with serving(create_receiver_app(story.webhook.receive)) as receiver:
        hook = call(docs, "POST", "/subscriptions", json={"url": receiver.url + DOCS_WEBHOOK_PATH})
        try:
            _reported_twice(story)
            _same_text(story)
            _revocation(story)
            _delete_and_recreate(story)
        finally:
            call(docs, "DELETE", f"/subscriptions/{hook['id']}")
    _summary(story)
    return story.changelog


def _initial_load(story: Story) -> None:
    echo = story.echo
    echo("1. Initial load. The full listing finds 12 pages; T2 has never seen any of them:")
    echo(f"   full listing sync() -> {story.listing.sync()} events")
    results = story.run_t2(show=False)
    actions = Counter(r.decision.action.value for r in results)
    echo(f"   T2: {dict(actions)} (reason: '{results[0].decision.reason}')")
    echo(f"   full listing sync() again -> {story.listing.sync()} events.\n")


def _reported_twice(story: Story) -> None:
    echo = story.echo
    echo(f"2. The same change, reported twice. dana edits {EDITED}; before T2 runs, the")
    echo("   webhook and the full listing both report it (the listing still sees v1.1 in")
    echo("   the registry, because T2 hasn't recorded the edit yet):")
    call(
        story.docs,
        "PUT",
        f"/pages/{EDITED}",
        json={"body": "Friday is remote too.\n"},
        headers=DANA,
    )
    echo(
        f"   webhook sync() -> {story.webhook.sync()} event; full listing sync() -> "
        f"{story.listing.sync()} event"
    )
    story.run_t2()
    echo("   The second copy is a repeat: parsing and embedding it again would change nothing.")
    echo("   (The bus can also deliver an event twice after a crash: same answer.)\n")


def _same_text(story: Story) -> None:
    echo = story.echo
    page = call(story.docs, "GET", f"/pages/{RESAVED}")
    echo(f"3. A save that changes nothing. dana saves {RESAVED} with exactly the same text;")
    echo(f"   the docs tool still raises its version (rev {page['rev']} -> {page['rev'] + 1}):")
    call(story.docs, "PUT", f"/pages/{RESAVED}", json={"body": page["body"]}, headers=DANA)
    story.webhook.sync()
    story.run_t2()
    record = story.changelog.registry.get(f"{SOURCE}:{RESAVED}")
    version = record.version if record else "?"
    echo(f"   Same hash, same readers: skipped. The registry still moves to v{version},")
    echo(f"   so the full listing stays quiet: sync() -> {story.listing.sync()} events.\n")


def _revocation(story: Story) -> None:
    echo = story.echo
    echo(f"4. A revocation. lucia removes hr-team from {REVOKED}; only es-employees remain:")
    call(
        story.docs, "PUT", f"/pages/{REVOKED}/acl", json={"groups": ["es-employees"]}, headers=LUCIA
    )
    story.webhook.sync()
    story.run_t2()
    echo("   The fast lane: the text didn't change, so parse, chunk, classify, entities and")
    echo("   embed are not run again; only T8 updates who may read the chunks (S11).")
    echo("   A revocation is never skipped: only an exact repeat of the readers is.\n")


def _delete_and_recreate(story: Story) -> None:
    echo, docs = story.echo, story.docs
    page: dict[str, Any] = call(docs, "GET", f"/pages/{DELETED}")
    echo(f"5. A delete, and a re-creation queued behind it. dana deletes {DELETED}, then")
    echo("   re-creates it with new text, both before T2 runs.")
    before = story.originals(DELETED)
    call(docs, "DELETE", f"/pages/{DELETED}", headers=DANA)
    recreated = {k: page[k] for k in ("id", "title", "language", "department", "acl")}
    call(
        docs,
        "POST",
        "/pages",
        json=recreated | {"body": "Replaced by the 2025 policy.\n"},
        headers=DANA,
    )
    story.webhook.sync()
    echo(f"   {DELETED}'s originals in the raw store: {before} before, {story.originals(DELETED)}")
    echo("   after: the connector stored the re-creation's when it reported it.")
    story.run_t2()
    echo(f"   Originals left: {story.originals(DELETED)}. The delete purged what T2 had seen")
    echo("   (1.1) and kept the re-creation's, which T2 then processed.")
    call(docs, "DELETE", f"/pages/{DELETED}", headers=DANA)
    story.webhook.sync()
    echo("   dana deletes it for good:")
    story.run_t2()
    record = story.changelog.registry.get(f"{SOURCE}:{DELETED}")
    state = "tombstone (no hash, readers or pointer)" if record and record.deleted else "live"
    echo(f"   Originals left: {story.originals(DELETED)}. Registry: {state}.")
    echo("   A delete is never skipped. The search index (S11) will drop its chunks the")
    echo("   same way, when the pipeline routes 'purge' to the stores (S12).\n")


def _summary(story: Story) -> None:
    echo = story.echo
    results = [r for _, r in story.decided]
    counts = Counter(r.decision.action for r in results)
    total = len(results)
    line = ", ".join(f"{a.value} {counts[a]}" for a in DiffAction)
    echo(f"6. T2 decided {total} events: {line}.")
    content = total - counts[DiffAction.PURGE]
    avoided = counts[DiffAction.SKIP] + counts[DiffAction.ACL_ONLY]
    echo(f"   Without T2, all {content} non-delete events would run T3-T7 (parse, chunk,")
    processed = counts[DiffAction.PROCESS]
    echo(f"   classify, entities, embed). With it, {processed} do: {avoided} runs avoided.")
    stats = story.changelog.bus.stats()
    groups = ", ".join(f"{g.group} pending {g.lag}" for g in stats.groups)
    echo(f"   Consumer groups: {groups}.")


# -------------------------------------------------------------------------- CLI


@demo_app.command("t2")
def t2(
    url: Annotated[
        str | None, typer.Option(help="A running mock docs service, e.g. http://127.0.0.1:8081.")
    ] = None,
    idp_url: Annotated[
        str | None, typer.Option(help="A running mock identity provider (port 8083).")
    ] = None,
) -> None:
    """S4 · T2 Diff: skip repeats and unchanged saves, fast-track revocations, purge deletes."""
    try:
        with ExitStack() as stack:
            docs = stack.enter_context(mock_client(DOCS, url))
            idp = stack.enter_context(mock_client(IDP, idp_url))
            run_t2_demo(docs, idp)
    except (MockStartError, httpx.HTTPError) as exc:
        raise fail(exc) from exc
