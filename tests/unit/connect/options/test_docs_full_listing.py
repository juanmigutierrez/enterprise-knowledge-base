"""The full listing: finds what webhooks missed, above all deletes."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sources.mock_docs.models import PageAcl, PageCreate, PageEdit
from sources.mock_docs.store import DocsStore

from ekb.connect.docs_api import DocsApi, DocsPageSummary
from ekb.connect.options.docs_full_listing import DocsFullListingConnector
from ekb.connect.people import PersonMap
from ekb.contracts import Acl, ChangeEvent, ChangeOp
from tests.http import as_httpx
from tests.unit.connect.conftest import NEW_PAGE, Originals

NOTICED = datetime(2026, 11, 24, 2, 0, tzinfo=UTC)

MakeConnector = Callable[[dict[str, str]], DocsFullListingConnector]


@pytest.fixture
def make(
    api: DocsApi, events: list[ChangeEvent], originals: Originals, people: PersonMap
) -> MakeConnector:
    def build(known: dict[str, str]) -> DocsFullListingConnector:
        return DocsFullListingConnector(
            api, events.append, originals, people, known=lambda: known, clock=lambda: NOTICED
        )

    return build


def _known(store: DocsStore) -> dict[str, str]:
    return {p.id: f"{p.rev}.{p.acl_rev}" for p in store.list_pages()}


def test_nothing_changed_nothing_emitted(make: MakeConnector, mock_store: DocsStore) -> None:
    assert make(_known(mock_store)).sync() == 0


def test_every_difference_becomes_the_right_event(
    make: MakeConnector,
    mock_store: DocsStore,
    events: list[ChangeEvent],
    originals: Originals,
) -> None:
    known = _known(mock_store)
    mock_store.create(PageCreate.model_validate(NEW_PAGE))
    mock_store.edit("HR-PL-04", PageEdit(body="edited\n"))
    mock_store.set_acl("HR-PL-05", PageAcl(groups=("es-employees",)))
    mock_store.delete("HR-PL-99", actor="dana")

    assert make(known).sync() == 4
    by_id = {e.source_id: e for e in events}
    assert by_id["HR-PL-04"].op is ChangeOp.UPSERT
    assert by_id["HR-PL-04"].version == "2.1"
    assert by_id["HR-PL-11"].op is ChangeOp.UPSERT
    assert by_id["HR-PL-05"].op is ChangeOp.ACL_CHANGE  # permissions only: no fetch, no copy
    assert by_id["HR-PL-05"].acl == Acl(groups=frozenset({"es-employees"}))
    gone = by_id["HR-PL-99"]
    assert gone.op is ChangeOp.DELETE
    assert gone.version == "1.1"  # the last version we knew
    assert gone.occurred_at == NOTICED  # the source gives no time; when we noticed
    assert (gone.raw_pointer, gone.content_hash, gone.acl) == (None, None, Acl())
    assert gone.changed_by is None  # dana deleted it, but a listing can't know that
    assert len(originals.saved) == 2
    assert events[-1] is gone  # deletes come after the listing's own pages


def test_a_delete_is_found_without_any_webhook(
    make: MakeConnector, mock_store: DocsStore, events: list[ChangeEvent]
) -> None:
    """The case D3 adds the listing for: the source says nothing, the page just isn't there."""
    known = _known(mock_store)
    mock_store.delete("HR-CMP-01")
    mock_store.delete("HR-PL-02")
    make(known).sync()
    assert [(e.op, e.source_id) for e in events] == [
        (ChangeOp.DELETE, "HR-CMP-01"),
        (ChangeOp.DELETE, "HR-PL-02"),
    ]


def test_content_and_acl_change_together_is_an_upsert(
    make: MakeConnector, mock_store: DocsStore, events: list[ChangeEvent]
) -> None:
    known = _known(mock_store)
    mock_store.edit("HR-PL-05", PageEdit(body="edited\n"))
    mock_store.set_acl("HR-PL-05", PageAcl(groups=("es-employees",)))
    make(known).sync()
    (event,) = events
    assert (event.op, event.version) == (ChangeOp.UPSERT, "2.2")
    assert event.acl.groups == {"es-employees"}  # an upsert carries the current ACL too


def test_an_unreadable_known_version_is_refetched(
    make: MakeConnector, mock_store: DocsStore, events: list[ChangeEvent]
) -> None:
    known = _known(mock_store)
    known["HR-PL-04"] = "legacy-v7"
    make(known).sync()
    assert [(e.op, e.source_id) for e in events] == [(ChangeOp.UPSERT, "HR-PL-04")]


def test_diff_does_not_emit(
    make: MakeConnector, mock_store: DocsStore, events: list[ChangeEvent]
) -> None:
    known = _known(mock_store)
    mock_store.delete("HR-PL-99")
    assert [e.op for e in make(known).diff()] == [ChangeOp.DELETE]
    assert events == []


def test_a_page_deleted_between_listing_and_fetch_is_a_delete(
    docs: TestClient,
    events: list[ChangeEvent],
    originals: Originals,
    mock_store: DocsStore,
    people: PersonMap,
) -> None:
    known = _known(mock_store)
    mock_store.edit("HR-PL-04", PageEdit(body="edited\n"))

    class ListThenDelete(DocsApi):
        def list_pages(self) -> list[DocsPageSummary]:
            listing = super().list_pages()
            mock_store.delete("HR-PL-04")
            return listing

    connector = DocsFullListingConnector(
        ListThenDelete(as_httpx(docs)), events.append, originals, people, known=lambda: known
    )
    connector.sync()
    (event,) = events
    assert (event.op, event.source_id, event.version) == (ChangeOp.DELETE, "HR-PL-04", "1.1")


def test_recreated_page_with_missed_webhooks_replaces_original_and_acl(
    make: MakeConnector, mock_store: DocsStore, events: list[ChangeEvent]
) -> None:
    page = PageCreate.model_validate(NEW_PAGE)
    mock_store.create(page)
    known = _known(mock_store)
    mock_store.delete(page.id)
    mock_store.create(
        page.model_copy(update={"body": "replacement", "acl": PageAcl(groups=("hr-team",))})
    )
    assert make(known).sync() == 1
    (event,) = events
    assert (event.op, event.version, event.acl.groups) == (
        ChangeOp.UPSERT,
        "2.1",
        frozenset({"hr-team"}),
    )
    assert event.raw_pointer
