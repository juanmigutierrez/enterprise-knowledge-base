"""Identity sync: each user is one tracked item; every change upserts it."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest
from fastapi.testclient import TestClient

from ekb.connect.identity_api import IdentityApi, IdpUser
from ekb.connect.normalize.common import UnknownChangeKindError
from ekb.connect.options.identity_sync import IdentitySyncConnector, UserVanishedError
from ekb.connect.people import PersonMap
from ekb.contracts import Acl, ChangeEvent, ChangeOp
from tests.unit.connect.conftest import Originals

LISTED = datetime(2026, 11, 23, 9, 0, tzinfo=UTC)


@pytest.fixture
def sync(
    idp: TestClient, events: list[ChangeEvent], originals: Originals, people: PersonMap
) -> IdentitySyncConnector:
    return IdentitySyncConnector(
        IdentityApi(idp), events.append, originals, people, clock=lambda: LISTED
    )


def _groups(originals: Originals, event: ChangeEvent) -> list[str]:
    groups: list[str] = json.loads(originals.saved[event.raw_pointer or ""])["groups"]
    return groups


def test_the_first_sync_lists_every_user(
    sync: IdentitySyncConnector, events: list[ChangeEvent]
) -> None:
    assert sync.sync() == 12
    alice = next(e for e in events if e.source_id == "users/alice")
    assert alice.document_key == "identity:users/alice"
    assert (alice.op, alice.version, alice.person_id, alice.occurred_at) == (
        ChangeOp.UPSERT,
        "0",  # the feed cursor when the listing was taken
        "alice",  # the identity provider's own ID is the canonical one
        LISTED,
    )
    assert alice.acl == Acl(groups=frozenset({"all-staff"}))
    assert sync.sync() == 0


def test_a_group_removal_and_a_leaver_are_upserts_of_the_user(
    sync: IdentitySyncConnector, idp: TestClient, events: list[ChangeEvent], originals: Originals
) -> None:
    sync.sync()
    events.clear()
    idp.delete("/scim/v2/Groups/it-employees/members/alice", headers={"X-Actor": "bob"})
    idp.post("/scim/v2/Users/olivia/deactivate", headers={"X-Actor": "dana"})
    assert sync.sync() == 2
    removed, left = events
    assert (removed.source_id, removed.version, removed.changed_by) == ("users/alice", "1", "bob")
    assert "it-employees" not in _groups(originals, removed)
    assert left.op is ChangeOp.UPSERT  # a leaver stays listed: not a delete
    assert json.loads(originals.saved[left.raw_pointer or ""])["active"] is False
    assert left.changed_by == "dana"


def test_a_change_during_the_first_listing_is_not_lost(
    idp: TestClient, events: list[ChangeEvent], originals: Originals, people: PersonMap
) -> None:
    class ChangeWhileListing(IdentityApi):
        def list_users(self) -> list[IdpUser]:
            users = super().list_users()
            idp.delete("/scim/v2/Groups/it-employees/members/alice")
            return users

    connector = IdentitySyncConnector(ChangeWhileListing(idp), events.append, originals, people)
    assert connector.sync() == 13  # 12 listed + the change replayed from the feed
    last = events[-1]
    assert (last.source_id, last.version) == ("users/alice", "1")
    assert "it-employees" not in _groups(originals, last)


def _feed(event: dict[str, object], user: dict[str, object] | None) -> IdentityApi:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/events":
            since = int(request.url.params["since"])
            events = [event] if since == 0 else []
            return httpx.Response(200, json={"events": events, "cursor": 1})
        if request.url.path == "/scim/v2/Users":
            return httpx.Response(200, json={"totalResults": 0, "Resources": []})
        return httpx.Response(200, json=user) if user else httpx.Response(404)

    return IdentityApi(httpx.Client(base_url="http://idp", transport=httpx.MockTransport(handler)))


EVENT = {"seq": 1, "event": "user.renamed", "user_id": "alice", "at": "2026-11-23T09:00:00Z"}


def test_an_unknown_identity_event_is_an_error(
    events: list[ChangeEvent], originals: Originals, people: PersonMap
) -> None:
    connector = IdentitySyncConnector(_feed(EVENT, None), events.append, originals, people)
    connector._cursor = 0  # skip the listing; replay the feed
    with pytest.raises(UnknownChangeKindError, match=r"user\.renamed"):
        connector.sync()
    assert connector.cursor == 0  # nothing consumed


def test_a_user_missing_from_the_provider_stops_the_sync(
    events: list[ChangeEvent], originals: Originals, people: PersonMap
) -> None:
    event = {**EVENT, "event": "user.deactivated"}
    connector = IdentitySyncConnector(_feed(event, None), events.append, originals, people)
    connector._cursor = 0
    with pytest.raises(UserVanishedError):
        connector.sync()
    assert events == []
