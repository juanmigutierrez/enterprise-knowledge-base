"""S3 · Change log: the event bus, the document registry and the raw store.

``ekb demo s3`` connects the docs tool's two connectors (S2) to the change log
and shows its three jobs: keep each document's events in order, remember what
was processed, and keep the originals so a replay never asks the source again.
The live part needs the docs tool; the replay runs after it has been stopped.
"""

from __future__ import annotations

from collections import defaultdict
from contextlib import ExitStack
from typing import Annotated

import httpx
import typer

from ekb.app.changelog import REGISTRY_GROUP, ChangeLog
from ekb.app.choices import choose
from ekb.app.demos._app import DockerFlag, demo_app
from ekb.app.demos._mocks import Echo, MockStartError, call, fail, mock_client
from ekb.app.demos._serve import serving
from ekb.app.infra import run_namespace
from ekb.app.pipeline import build_changelog
from ekb.changelog.bus.port import Delivery, OutOfOrderCommitError
from ekb.changelog.registry.options.in_memory import InMemoryRegistry
from ekb.changelog.registry.port import DocumentRecord, record_event
from ekb.connect.docs_api import DocsApi
from ekb.connect.identity_api import IdentityApi
from ekb.connect.options.docs_full_listing import DocsFullListingConnector
from ekb.connect.options.docs_webhook import DocsWebhookConnector
from ekb.connect.people import PersonMap, load_people
from ekb.connect.receiver import DOCS_WEBHOOK_PATH, create_receiver_app
from ekb.contracts import ChangeEvent, ChangeOp
from ekb.platform.config import load_settings

DOCS, IDP = "sources.mock_docs", "sources.mock_idp"
SOURCE = "hr_docs"
EDITED, REVOKED, DELETED = "HR-PL-04", "HR-PL-05", "HR-PL-99"
DANA, LUCIA = ({"X-Actor": name} for name in ("dana", "lucia"))
REPLAY_GROUP = "reindex"


def _at(delivery: Delivery) -> str:
    return f"p{delivery.partition}@{delivery.offset}"


def _readers(acl_groups: frozenset[str], acl_users: frozenset[str]) -> str:
    return ",".join(sorted(acl_groups) + sorted(f"user:{u}" for u in acl_users)) or "-"


def _line(delivery: Delivery) -> str:
    event = delivery.event
    return (
        f"{_at(delivery):<6} {event.op.value:<10} {event.source_id:<9} v{event.version:<4} "
        f"by={event.changed_by or '-':<6} readers={_readers(event.acl.groups, event.acl.users)}"
    )


def _record_line(record: DocumentRecord) -> str:
    state = "deleted" if record.deleted else "live"
    pointer = (
        record.raw_pointer.split("://", 1)[-1].split("/", 1)[-1] if record.raw_pointer else "-"
    )
    hashed = record.content_hash.removeprefix("sha256:")[:8] if record.content_hash else "-"
    return (
        f"{record.source_id:<9} v{record.version:<4} {state:<7} "
        f"readers={_readers(record.acl.groups, record.acl.users):<13} "
        f"hash={hashed:<8} original={pointer}"
    )


def _pending(changelog: ChangeLog, group: str) -> int:
    """Events the group has not committed. A group that has never read has them all."""
    stats = changelog.bus.stats()
    return next((g.lag for g in stats.groups if g.group == group), stats.events)


# ---------------------------------------------------------------------- live part


def where(changelog: ChangeLog) -> tuple[str, str, str]:
    """Where the events, the originals and the records are, in plain words (S17)."""
    bus, raw, records = changelog.bus, changelog.rawstore, changelog.registry
    topic = getattr(bus, "topic", None)
    bucket = getattr(raw, "bucket", None)
    table = getattr(getattr(records, "table", None), "name", None)
    return (
        f"Kafka topic {topic}" if topic else "in memory; Kafka with --docker",
        f"S3 bucket {bucket} on SeaweedFS" if bucket else "in memory; SeaweedFS with --docker",
        f"DynamoDB table {table}" if table else "in memory; DynamoDB Local with --docker",
    )


def run_s3_live(
    docs: httpx.Client,
    idp: httpx.Client,
    echo: Echo = typer.echo,
    *,
    changelog: ChangeLog | None = None,
) -> ChangeLog:
    """Publish, record and redeliver, against the docs tool. Returns the change log
    (in memory unless one is given: ``--docker`` gives Kafka, DynamoDB and S3, S17)."""
    call(docs, "POST", "/reset")
    call(idp, "POST", "/reset")
    changelog = changelog or ChangeLog.in_memory(partitions=3)
    bus_at, raw_at, registry_at = where(changelog)
    published: list[Delivery] = []

    def publish(event: ChangeEvent) -> None:
        published.append(changelog.publish(event))

    api = DocsApi(docs)
    people = PersonMap(load_people(IdentityApi(idp)))
    known = changelog.known(SOURCE)
    store = changelog.store_original
    webhook = DocsWebhookConnector(api, publish, store, people, known=known)
    listing = DocsFullListingConnector(api, publish, store, people, known=known)

    echo("S3 · Change log: event bus, document registry, raw store\n")
    echo("0. The docs connectors (S2) now hand everything to the change log:")
    echo(f"   events    -> bus, {changelog.bus.partitions} partitions ({bus_at})")
    echo(f"   originals -> raw store ({raw_at})")
    echo(f"   'known'   <- document registry ({registry_at})\n")

    _initial_load(echo, changelog, listing, published)
    with serving(create_receiver_app(webhook.receive)) as receiver:
        hook = call(docs, "POST", "/subscriptions", json={"url": receiver.url + DOCS_WEBHOOK_PATH})
        try:
            _changes(docs, echo, webhook, published)
        finally:
            call(docs, "DELETE", f"/subscriptions/{hook['id']}")
    _crash(echo, changelog)
    _registry_after(echo, changelog, listing)
    return changelog


def _initial_load(
    echo: Echo,
    changelog: ChangeLog,
    listing: DocsFullListingConnector,
    published: list[Delivery],
) -> None:
    echo("1. Initial load. The registry is empty, so the full listing finds every page new.")
    echo(f"   full listing sync() -> {listing.sync()} events published:")
    by_partition: dict[int, list[str]] = defaultdict(list)
    for delivery in published:
        by_partition[delivery.partition].append(delivery.event.source_id)
    for partition in range(changelog.bus.partitions):
        pages = by_partition[partition]
        echo(f"   partition {partition}: {len(pages):>2} events  {' '.join(pages)}")
    sample = changelog.rawstore.versions(f"{SOURCE}:{EDITED}")[0]
    stored = sum(len(changelog.rawstore.versions(d.event.document_key)) for d in published)
    echo(f"   raw store: {stored} originals, e.g. {sample.pointer} ({sample.size} bytes)")
    echo("   A page always goes to the same partition: sha256(document_key) mod 3.\n")

    echo(
        f"2. Published is not processed: group '{REGISTRY_GROUP}' has "
        f"{_pending(changelog, REGISTRY_GROUP)} events pending."
    )
    echo("   S3's stand-in worker records each one, then commits it (T2 does this from S4):")
    echo(
        f"   recorded {changelog.record_processed()} events; the registry knows "
        f"{len(changelog.registry.known(SOURCE))} pages; pending now "
        f"{_pending(changelog, REGISTRY_GROUP)}."
    )
    echo(f"   full listing sync() again -> {listing.sync()} events: the source and the registry")
    echo("   agree. The listing now compares against the registry, not a dict (S2).\n")


def _changes(
    docs: httpx.Client, echo: Echo, webhook: DocsWebhookConnector, published: list[Delivery]
) -> None:
    echo("3. Four changes in the docs tool, delivered by webhook:")
    first = len(published)
    call(docs, "PUT", f"/pages/{EDITED}", json={"body": "Friday is remote too.\n"}, headers=DANA)
    webhook.sync()
    echo(f"   dana   edits   {EDITED} (Remote work, Milan)")
    call(docs, "PUT", f"/pages/{REVOKED}/acl", json={"groups": ["es-employees"]}, headers=LUCIA)
    echo(f"   lucia  revokes hr-team on {REVOKED} (Teletrabajo, Madrid)")
    call(docs, "PUT", f"/pages/{EDITED}", json={"body": "Monday too.\n"}, headers=DANA)
    echo(f"   dana   edits   {EDITED} again")
    call(docs, "DELETE", f"/pages/{DELETED}", headers=DANA)
    echo(f"   dana   deletes {DELETED} (Business travel 2024)")
    webhook.sync()
    echo("   Where each event landed (partition@offset):")
    for delivery in published[first:]:
        echo(f"   {_line(delivery)}")
    edits = [d for d in published[first:] if d.event.source_id == EDITED]
    echo(f"   - Both {EDITED} edits are in partition {edits[0].partition}, in order: no worker")
    echo(f"     can apply v{edits[1].event.version} before v{edits[0].event.version}.")
    echo(f"   - {REVOKED} is in another partition. Its order relative to {EDITED} is not")
    echo("     guaranteed, and nothing needs it: each document has its own history.\n")


def _crash(echo: Echo, changelog: ChangeLog) -> None:
    bus = changelog.bus
    echo("4. The worker crashes. It polls, records the first event, and dies:")
    polled = bus.poll(REGISTRY_GROUP)
    first = polled[0]
    record_event(changelog.registry, first.event)
    bus.commit(REGISTRY_GROUP, first)
    echo(f"   poll -> {len(polled)} events; recorded and committed {_at(first)} only.")
    again = bus.poll(REGISTRY_GROUP)
    echo(
        f"   After a restart, poll -> {len(again)} events again: "
        f"{', '.join(_at(d) for d in again)}."
    )
    echo("   Nothing committed is lost, nothing uncommitted is skipped: at least once.")
    echo("   (So a worker can see an event twice. T2 skips repeats by hash: ekb demo t2.)")
    the_delete = next(d for d in again if d.event.op is ChangeOp.DELETE)
    try:
        bus.commit(REGISTRY_GROUP, the_delete)
        echo("   Committing the delete first was accepted (it must not be).")
    except OutOfOrderCommitError:
        earlier = next(
            d for d in again if d.partition == the_delete.partition and d.offset < the_delete.offset
        )
        echo(f"   Committing the delete ({_at(the_delete)}) before {_at(earlier)} is refused:")
        echo("   a skipped event could be a delete or a revocation.")
    echo(f"   The worker drains them in order: {changelog.record_processed()} recorded.\n")


def _registry_after(echo: Echo, changelog: ChangeLog, listing: DocsFullListingConnector) -> None:
    echo("5. The registry now (what T2 and the full listings compare against):")
    for source_id in (EDITED, REVOKED, DELETED):
        record = changelog.registry.get(f"{SOURCE}:{source_id}")
        if record is not None:
            echo(f"   {_record_line(record)}")
    echo(f"   {REVOKED} is at v1.2 but its original is still 1.1: only its readers changed,")
    echo("   so there is nothing new to read, parse or embed (T2's fast lane, S4).")
    echo(f"   {DELETED} is a tombstone: it remembers the delete, not the content.")
    echo(f"   full listing sync() -> {listing.sync()} events.\n")


# --------------------------------------------------------------------- replay part


def run_s3_replay(
    changelog: ChangeLog, source_note: str, echo: Echo = typer.echo, *, durable: bool = False
) -> None:
    """Replay the whole history with a new group, reading originals only from the raw store.
    ``durable``: ``changelog`` is a second set of clients, as a new process would open."""
    echo(f"6. Replay. {source_note}")
    if durable:
        echo("   This is a new bus, registry and raw store, opened as another process would:")
        echo("   what they read was kept by Kafka, DynamoDB and SeaweedFS, not by this demo.")
    echo(f"   A new consumer group, '{REPLAY_GROUP}', starts at the earliest event:")
    rebuilt = InMemoryRegistry()
    events, originals, total_bytes = 0, 0, 0
    while deliveries := changelog.bus.poll(REPLAY_GROUP):
        for delivery in deliveries:
            event = delivery.event
            if event.raw_pointer is not None:
                total_bytes += len(changelog.rawstore.get(event.raw_pointer))
                originals += 1
            record_event(rebuilt, event)
            changelog.bus.commit(REPLAY_GROUP, delivery)
            events += 1
    echo(f"   {events} events replayed; {originals} originals read from the raw store")
    echo(f"   ({total_bytes} bytes), 0 requests to the docs tool.")
    same = rebuilt.records() == changelog.registry.records()
    live = len(rebuilt.known(SOURCE))
    tombstones = sum(r.deleted for r in rebuilt.records())
    echo(f"   Rebuilt: {live} live pages, {tombstones} tombstone. Same as the registry: {same}.")
    deleted = changelog.rawstore.versions(f"{SOURCE}:{DELETED}")
    echo(f"   {DELETED} was replayed as an upsert, then its delete, so it ends deleted.")
    echo(f"   Its original is still stored ({len(deleted)} version): this demo's stand-in")
    echo("   never purges. T2 purges it when it processes the delete (ekb demo t2).")
    groups = ", ".join(f"{g.group} pending {g.lag}" for g in changelog.bus.stats().groups)
    echo(f"   Consumer groups: {groups}.\n")
    echo("   Why keep originals: re-parsing (T3) or re-embedding with a new model (T7)")
    echo("   replays them, while the source may have changed, be down or rate limited.")
    echo("   On AWS the bus (SQS) keeps nothing once consumed, so the raw store and the")
    echo("   registry are the replay source there (D4).")


# -------------------------------------------------------------------------- CLI


@demo_app.command("s3")
def s3(
    url: Annotated[
        str | None, typer.Option(help="A running mock docs service, e.g. http://127.0.0.1:8081.")
    ] = None,
    idp_url: Annotated[
        str | None, typer.Option(help="A running mock identity provider (port 8083).")
    ] = None,
    docker: DockerFlag = False,
) -> None:
    """S3 · Change log: publish, consume in order, redeliver after a crash, replay."""
    try:
        with run_namespace(docker) as namespace:
            durable = None
            if docker:
                choices = choose(load_settings(), offline=True, docker=True, namespace=namespace)
                durable = build_changelog(choices)
            with ExitStack() as stack:
                docs = stack.enter_context(mock_client(DOCS, url))
                idp = stack.enter_context(mock_client(IDP, idp_url))
                changelog = run_s3_live(docs, idp, changelog=durable)
            note = (
                "The demo has closed its connection to the docs tool."
                if url
                else "The docs tool has been stopped."
            )
            if docker:
                run_s3_replay(build_changelog(choices), note, durable=True)
            else:
                run_s3_replay(changelog, note)
    except (MockStartError, httpx.HTTPError, OSError, LookupError, ValueError) as exc:
        raise fail(exc) from exc
