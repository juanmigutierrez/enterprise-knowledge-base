"""``GET /api/search`` (S13): one question asked as one person, over the real query path."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from ekb.app.choices import choose_query
from ekb.app.pipeline import Stores
from ekb.app.search import build_search
from ekb.changelog.bus.options.in_memory import InMemoryEventBus
from ekb.changelog.rawstore.options.in_memory import InMemoryRawStore
from ekb.changelog.registry.options.in_memory import InMemoryRegistry
from ekb.contracts import Acl
from ekb.operate.inspector.app import create_inspector_app
from ekb.operate.inspector.log import ChunkStore, DecisionLog, EventLog, ParsedStore
from ekb.operate.inspector.search import SearchPanel, withheld
from ekb.platform.config import load_settings
from ekb.process.t8_permissions.options.source_acl import SourceAclResolver
from ekb.process.t8_permissions.step import PermissionStep
from ekb.stores.entities.options.in_memory import InMemoryEntityStore
from ekb.stores.permissions.options.in_memory import InMemoryPermissionStore
from ekb.stores.search.options.in_memory import InMemorySearchStore
from tests.unit.process.t8_permissions.conftest import membership, ready, upsert

PAY = Acl(groups=frozenset({"hr-compensation"}))


def _world(deleted: str | None = None, *, wired: bool = True) -> tuple[TestClient, Stores]:
    """Two published pages (one for everyone, the salary bands for hr-compensation) and
    three people, one of whom has left. ``deleted``: a page whose delete is accepted but
    not purged yet."""
    lifetimes = {"hr_docs:HR-PL-01": 0, "hr_docs:HR-CMP-01": 0}
    if deleted:
        lifetimes[deleted] = 1

    def current(key: str) -> int:
        return lifetimes.get(key, 0)

    stores = Stores(
        search=InMemorySearchStore(current_lifetime=current),
        permissions=InMemoryPermissionStore(),
        entities=InMemoryEntityStore(),
    )
    for m in (
        membership("alice", "all-staff", "it-employees"),
        membership("bob", "all-staff", "hr-compensation"),
        membership("olivia", "all-staff", active=False),
    ):
        stores.permissions.put_membership(m)
    step = PermissionStep(SourceAclResolver(), stores.permissions, stores.search)
    chunks = ChunkStore()
    for page, acl in (("HR-PL-01", Acl(groups=frozenset({"all-staff"}))), ("HR-CMP-01", PAY)):
        document, cut = ready(page)
        event = upsert(f"hr_docs:{page}", acl)
        step.publish(event, document, cut)
        chunks.record(event, document.title, cut, chunker_id="test@1", max_tokens=400)
    search = build_search(choose_query(load_settings(profile="ci")), stores)
    panel = SearchPanel(search.ask, stores.search, encoder="questions not embedded (test)")
    app = create_inspector_app(
        EventLog(),
        profile="local",
        bus=InMemoryEventBus(),
        registry=InMemoryRegistry(),
        rawstore=InMemoryRawStore(),
        decisions=DecisionLog(),
        parsed=ParsedStore(),
        chunks=chunks,
        current_lifetime=current,
        search=panel if wired else None,
    )
    return TestClient(app), stores


def _ask(client: TestClient, user: str, q: str = "salary bands") -> dict[str, Any]:
    response = client.get("/api/search", params={"user": user, "q": q})
    assert response.status_code == 200, response.text
    view: dict[str, Any] = response.json()
    return view


def test_two_people_get_what_each_may_read_and_the_inspector_shows_what_was_kept_out() -> None:
    client, _ = _world()
    alice, bob = _ask(client, "alice"), _ask(client, "bob")

    assert {h["document_key"] for h in alice["hits"]} <= {"hr_docs:HR-PL-01"}
    assert alice["withheld"] == [
        {
            "document_key": "hr_docs:HR-CMP-01",
            "title": "Salary bands 2026 (restricted)",
            "chunks": alice["withheld_chunks"],
            "readers": {"users": [], "groups": ["hr-compensation"]},
        }
    ]
    assert alice["withheld_chunks"] > 0
    assert alice["reason"] == "reads as alice + all-staff, it-employees"
    assert alice["groups"] == ["all-staff", "it-employees"]
    assert alice["notes"] == ["keyword leg only: no embedding model for questions"]
    assert alice["encoder"] == "questions not embedded (test)"

    first = bob["hits"][0]
    assert first["document_key"] == "hr_docs:HR-CMP-01"
    assert first["title"] == "Salary bands 2026 (restricted)"
    assert first["readers"] == {"users": [], "groups": ["hr-compensation"]}
    assert (first["rank"], first["keyword_rank"], first["vector_rank"]) == (1, 1, None)
    assert bob["withheld"] == []
    assert bob["searched"] > alice["searched"]
    assert bob["retriever_id"].startswith("hybrid_rrf@1(top_k=5")


def test_a_leaver_and_an_unknown_person_read_nothing() -> None:
    client, _ = _world()
    for user in ("olivia", "mallory"):
        view = _ask(client, user)
        assert view["reads_nothing"] is True
        assert view["hits"] == []
        assert view["searched"] == 0
        assert view["notes"] == [f"nothing was searched: {user} reads nothing"]


def test_a_deleted_page_is_neither_a_hit_nor_withheld_before_the_purge() -> None:
    """The deletion gate in the inspector: the delete is accepted, T2 has not purged yet."""
    client, stores = _world(deleted="hr_docs:HR-CMP-01")
    assert stores.search.chunks_for_update("hr_docs:HR-CMP-01") == ()  # its lifetime is closed
    assert all(h["document_key"] != "hr_docs:HR-CMP-01" for h in _ask(client, "bob")["hits"])
    assert _ask(client, "alice")["withheld"] == []
    assert stores.search.remove("hr_docs:HR-CMP-01") > 0  # it was still stored: no purge yet


def test_a_user_is_required_and_an_unwired_inspector_says_so() -> None:
    client, _ = _world()
    assert client.get("/api/search", params={"q": "salary"}).status_code == 422
    assert client.get("/api/search", params={"user": "", "q": "salary"}).status_code == 422
    unwired, _ = _world(wired=False)
    view = _ask(unwired, "alice")
    assert view["hits"] == []
    assert view["notes"] == ["the query path is not wired in this inspector"]


def test_withheld_needs_a_shared_word_and_counts_nothing_for_an_empty_question() -> None:
    _, stores = _world()
    principals = stores.permissions.principals("alice")
    assert {c.document_key for c in withheld(stores.search, principals, "salary")} == {
        "hr_docs:HR-CMP-01"
    }
    assert withheld(stores.search, principals, "?!") == ()
    assert withheld(stores.search, principals, "zebra") == ()
    nobody = withheld(stores.search, None, "salary")  # reads nothing: everything matching
    assert {c.document_key for c in nobody} == {"hr_docs:HR-CMP-01"}
