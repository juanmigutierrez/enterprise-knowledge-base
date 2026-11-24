"""The webhook connector: notifications in, normalized events out."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import httpx
import pytest
from fastapi.testclient import TestClient
from sources.mock_docs.models import PageAcl, PageEdit
from sources.mock_docs.store import DocsStore

from ekb.connect.docs_api import DocsApi, DocsNotification
from ekb.connect.normalize.common import UnknownChangeKindError
from ekb.connect.options.docs_webhook import DocsWebhookConnector
from ekb.connect.people import People, PeopleLoader, PersonMap
from ekb.contracts import Acl, ChangeEvent, ChangeOp
from tests.unit.connect.conftest import NEW_PAGE, Originals, Webhooks


def _without(load: PeopleLoader, user_id: str) -> PeopleLoader:
    def load_without() -> People:
        people = load()
        users = {u: n for u, n in people.users.items() if u != user_id}
        return People(users=users, employees=people.employees)

    return load_without


@pytest.fixture
def connector(
    api: DocsApi, events: list[ChangeEvent], originals: Originals, people: PersonMap
) -> DocsWebhookConnector:
    return DocsWebhookConnector(
        api, events.append, originals, people, known=lambda: {"HR-PL-05": "1.1"}
    )


def _deliver(webhooks: Webhooks, connector: DocsWebhookConnector) -> None:
    for notification in webhooks.take():
        connector.receive(notification)


def test_add_edit_revoke_delete_become_four_events(
    docs: TestClient,
    webhooks: Webhooks,
    connector: DocsWebhookConnector,
    events: list[ChangeEvent],
    originals: Originals,
) -> None:
    docs.post("/pages", json=NEW_PAGE)
    docs.put("/pages/HR-PL-04", json={"body": "Friday is remote too.\n"})
    docs.put("/pages/HR-PL-05/acl", json={"groups": ["es-employees"]})  # hr-team revoked
    docs.delete("/pages/HR-PL-99")
    _deliver(webhooks, connector)
    assert connector.pending == 4
    assert events == []  # receiving only queues

    assert connector.sync() == 4
    assert connector.pending == 0
    added, edited, revoked, deleted = events
    assert [e.op for e in events] == [
        ChangeOp.UPSERT,
        ChangeOp.UPSERT,
        ChangeOp.ACL_CHANGE,
        ChangeOp.DELETE,
    ]
    assert {e.source for e in events} == {"hr_docs"}

    assert (added.source_id, added.version) == ("HR-PL-11", "1.1")
    assert added.acl == Acl(groups=frozenset({"all-staff"}))
    assert added.raw_pointer is not None
    assert b"EUR 300 a year" in originals.saved[added.raw_pointer]  # the original is kept

    assert (edited.source_id, edited.version) == ("HR-PL-04", "2.1")
    assert b"Friday is remote too." in originals.saved[edited.raw_pointer or ""]

    assert (revoked.source_id, revoked.version) == ("HR-PL-05", "1.2")
    assert revoked.acl == Acl(groups=frozenset({"es-employees"}))  # hr-team is gone
    assert revoked.raw_pointer is None

    assert deleted.source_id == "HR-PL-99"
    assert (deleted.raw_pointer, deleted.content_hash, deleted.acl) == (None, None, Acl())
    assert len(originals.saved) == 2  # only upserts store an original


def test_the_same_notification_twice_gives_the_same_event(
    docs: TestClient,
    webhooks: Webhooks,
    connector: DocsWebhookConnector,
    events: list[ChangeEvent],
) -> None:
    docs.put("/pages/HR-PL-04", json={"body": "edited\n"})
    (notification,) = webhooks.take()
    connector.receive(notification)
    connector.receive(notification)  # sources retry; receivers see duplicates
    connector.sync()
    assert events[0] == events[1]


def test_the_page_as_it_is_now_wins_over_the_notification(
    docs: TestClient,
    webhooks: Webhooks,
    connector: DocsWebhookConnector,
    events: list[ChangeEvent],
    mock_store: DocsStore,
) -> None:
    docs.put("/pages/HR-PL-04", json={"body": "first\n"})
    _deliver(webhooks, connector)
    mock_store.edit("HR-PL-04", PageEdit(body="second\n"))  # edited again before we fetch
    connector.sync()
    assert events[0].version == "3.1"  # the notification said rev 2


def test_a_page_gone_before_the_fetch_becomes_a_delete(
    docs: TestClient,
    webhooks: Webhooks,
    connector: DocsWebhookConnector,
    events: list[ChangeEvent],
    mock_store: DocsStore,
) -> None:
    docs.put("/pages/HR-PL-04", json={"body": "edited\n"})
    docs.put("/pages/HR-PL-05/acl", json={"groups": ["es-employees"]})
    _deliver(webhooks, connector)
    mock_store.delete("HR-PL-04")
    mock_store.delete("HR-PL-05")
    connector.sync()
    assert [(e.op, e.source_id) for e in events] == [
        (ChangeOp.DELETE, "HR-PL-04"),
        (ChangeOp.DELETE, "HR-PL-05"),
    ]
    assert events[0].version == "2.1"  # the last version the source told us about


def test_an_unknown_kind_is_refused_on_receipt(connector: DocsWebhookConnector) -> None:
    odd = DocsNotification.model_validate(
        {
            "seq": 1,
            "event": "page.moved",
            "page_id": "HR-PL-04",
            "rev": 1,
            "acl_rev": 1,
            "at": "2026-11-23T09:00:00Z",
        }
    )
    with pytest.raises(UnknownChangeKindError):
        connector.receive(odd)
    assert connector.pending == 0


def test_a_source_outage_while_fetching_keeps_the_queue(
    webhooks: Webhooks,
    events: list[ChangeEvent],
    originals: Originals,
    mock_store: DocsStore,
    people: PersonMap,
) -> None:
    mock_store.set_acl("HR-PL-05", PageAcl(groups=("es-employees",)))
    mock_store.edit("HR-PL-04", PageEdit(body="edited\n"))
    notifications = webhooks.take()

    def down(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    broken = DocsApi(httpx.Client(base_url="http://docs", transport=httpx.MockTransport(down)))
    connector = DocsWebhookConnector(broken, events.append, originals, people)
    for notification in notifications:
        connector.receive(notification)
    with pytest.raises(httpx.HTTPStatusError):
        connector.sync()
    assert connector.pending == 2  # nothing lost; the next sync retries
    assert events == []


def test_deletes_need_no_fetch(
    webhooks: Webhooks,
    events: list[ChangeEvent],
    originals: Originals,
    mock_store: DocsStore,
    people: PersonMap,
) -> None:
    mock_store.delete("HR-PL-99", actor="dana")

    def refuse(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected fetch {request.url}")

    offline = DocsApi(httpx.Client(base_url="http://docs", transport=httpx.MockTransport(refuse)))
    connector = DocsWebhookConnector(offline, events.append, originals, people, source="other_docs")
    for notification in webhooks.take():
        connector.receive(notification)
    assert connector.sync() == 1
    assert events[0].document_key == "other_docs:HR-PL-99"
    assert events[0].changed_by == "dana"  # who deleted it, from the notification


def test_the_writer_becomes_changed_by_and_unknown_people_are_dropped(
    docs: TestClient,
    webhooks: Webhooks,
    connector: DocsWebhookConnector,
    events: list[ChangeEvent],
    people: PersonMap,
    mock_store: DocsStore,
) -> None:
    docs.put("/pages/HR-PL-04", json={"body": "edited\n"}, headers={"X-Actor": "dana"})
    docs.put(
        "/pages/HR-PL-05/acl",
        json={"users": ["carla"], "groups": ["es-employees"]},
        headers={"X-Actor": "lucia"},
    )
    _deliver(webhooks, connector)
    # The identity provider forgets carla after the docs tool accepted her (directories drift).
    people._load = _without(people._load, "carla")
    people.refresh()
    connector.sync()
    edited, shared = events
    assert (edited.changed_by, edited.person_id) == ("dana", None)  # pages are about no one
    assert shared.changed_by == "lucia"
    assert shared.acl == Acl(groups=frozenset({"es-employees"}))  # carla dropped: fail closed
    assert people.unmapped == {"user:carla"}  # and reported


def test_acl_webhook_recovers_a_missed_content_edit(
    connector: DocsWebhookConnector,
    mock_store: DocsStore,
    webhooks: Webhooks,
    events: list[ChangeEvent],
    originals: Originals,
) -> None:
    mock_store.edit("HR-PL-05", PageEdit(body="replacement"))
    webhooks.take()
    mock_store.set_acl("HR-PL-05", PageAcl(groups=("hr-team",)))
    _deliver(webhooks, connector)
    assert connector.sync() == 1
    (event,) = events
    assert event.op is ChangeOp.UPSERT
    assert event.acl.groups == {"hr-team"}
    assert b"replacement" in originals.saved[event.raw_pointer or ""]


def test_overlapping_sync_calls_drain_each_notification_once(
    api: DocsApi, people: PersonMap, originals: Originals, mock_store: DocsStore, webhooks: Webhooks
) -> None:
    started, release, second_started = Event(), Event(), Event()
    seen: list[str] = []

    def sink(event: ChangeEvent) -> None:
        seen.append(event.source_id)
        started.set()
        assert release.wait(5)

    connector = DocsWebhookConnector(api, sink, originals, people)

    def second_sync() -> int:
        second_started.set()
        return connector.sync()

    with ThreadPoolExecutor(2) as executor:
        mock_store.delete("HR-PL-04")
        _deliver(webhooks, connector)
        first = executor.submit(connector.sync)
        assert started.wait(5)
        second = executor.submit(second_sync)
        assert second_started.wait(5)
        mock_store.delete("HR-PL-05")
        _deliver(webhooks, connector)
        release.set()
        assert first.result() + second.result() == 2
    assert seen == ["HR-PL-04", "HR-PL-05"]
