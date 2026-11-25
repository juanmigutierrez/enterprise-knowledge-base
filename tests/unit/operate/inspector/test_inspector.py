"""The inspector: local only, read only, and a stable contract for the UI."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from ekb.changelog.bus.consume import drain
from ekb.changelog.bus.options.in_memory import InMemoryEventBus
from ekb.changelog.rawstore.options.in_memory import InMemoryRawStore
from ekb.changelog.registry.options.in_memory import InMemoryRegistry
from ekb.changelog.registry.port import record_event
from ekb.contracts import Acl, ChangeEvent, ChangeOp, Entity, EntityKind
from ekb.operate.inspector.app import (
    HOST,
    InspectorNotAllowedError,
    create_inspector_app,
    openapi_json,
)
from ekb.operate.inspector.entities import EntityPanel
from ekb.operate.inspector.log import ChunkStore, DecisionLog, EventLog, ParsedStore
from ekb.process.t2_diff.options.content_hash import ContentHashDiffer
from ekb.process.t2_diff.step import DiffResult, DiffStep
from ekb.process.t3_parse.options.markdown import MarkdownParser
from ekb.process.t3_parse.port import MARKDOWN, SourceFile
from ekb.process.t4_chunk.options.structure_aware import StructureAwareChunker
from ekb.process.t5_classify.options.rules import RulesClassifier
from ekb.process.t5_classify.step import ClassifyStep
from ekb.process.t6_entities.options.exact_match import ExactMatchLinker
from ekb.process.t6_entities.step import LinkStep
from ekb.stores.entities.options.in_memory import InMemoryEntityStore
from tests.conftest import REPO_ROOT
from tests.unit.process.t6_entities.conftest import ENTITIES

AT = datetime(2026, 11, 24, 9, 0, tzinfo=UTC)


def _event(source_id: str, op: ChangeOp, version: str, **extra: object) -> ChangeEvent:
    fields: dict[str, object] = {
        "source": "hr_docs",
        "source_id": source_id,
        "op": op,
        "version": version,
        "occurred_at": AT,
    }
    if op is ChangeOp.UPSERT:
        fields |= {"raw_pointer": f"mem://raw/hr_docs/{source_id}/{version}", "content_hash": "h"}
        fields |= {"acl": Acl(groups=frozenset({"all-staff"}))}
    return ChangeEvent.model_validate(fields | extra)


EVENTS = [
    _event("HR-PL-04", ChangeOp.UPSERT, "1.1"),
    _event("HR-PL-99", ChangeOp.UPSERT, "1.1"),
    _event("HR-PL-04", ChangeOp.UPSERT, "2.1", changed_by="dana"),
    _event("HR-PL-99", ChangeOp.DELETE, "1.1", changed_by="dana"),
    ChangeEvent(
        source="identity",
        source_id="users/alice",
        op=ChangeOp.UPSERT,
        version="0",
        occurred_at=AT,
        raw_pointer="mem://x",
        person_id="alice",
    ),
]


@pytest.fixture
def bus() -> InMemoryEventBus:
    bus = InMemoryEventBus(partitions=3)
    for event in EVENTS:
        bus.publish(event)
    return bus


@pytest.fixture
def registry(bus: InMemoryEventBus) -> InMemoryRegistry:
    registry = InMemoryRegistry()
    drain(bus, "registry", lambda d: record_event(registry, d.event))
    return registry


@pytest.fixture
def rawstore() -> InMemoryRawStore:
    store = InMemoryRawStore(clock=lambda: AT)
    store.put("hr_docs:HR-PL-04", "1.1", b"first")
    store.put("hr_docs:HR-PL-04", "2.1", b"second")
    return store


@pytest.fixture
def log(bus: InMemoryEventBus) -> EventLog:
    log = EventLog()
    drain(bus, "inspector", log.append)
    return log


@pytest.fixture
def decisions(bus: InMemoryEventBus) -> DecisionLog:
    """T2's decisions on EVENTS, plus a repeat of the last edit, as the inspector keeps them."""
    decisions = DecisionLog()

    def keep(result: DiffResult) -> None:
        decisions.append(result.decision, result.purged)

    step = DiffStep(ContentHashDiffer(), InMemoryRegistry(), InMemoryRawStore(), keep)
    for event in [*EVENTS, EVENTS[2]]:
        step.handle(event)
    return decisions


def _app(
    log: EventLog,
    profile: str = "local",
    bus: InMemoryEventBus | None = None,
    registry: InMemoryRegistry | None = None,
    rawstore: InMemoryRawStore | None = None,
    decisions: DecisionLog | None = None,
    parsed: ParsedStore | None = None,
    chunks: ChunkStore | None = None,
) -> FastAPI:
    return create_inspector_app(
        log,
        profile=profile,
        bus=bus or InMemoryEventBus(),
        registry=registry or InMemoryRegistry(),
        rawstore=rawstore or InMemoryRawStore(),
        decisions=decisions or DecisionLog(),
        parsed=parsed or ParsedStore(),
        chunks=chunks or ChunkStore(),
        current_lifetime=lambda key: 0,
    )


BODY = (
    "# Remote work\n\n## The rule\n- Two days a week.\n\n"
    "| Day | Where |\n|---|---|\n| Fri | Home |\n"
)


@pytest.fixture
def parsed() -> ParsedStore:
    """T3's results as the inspector keeps them: HR-PL-04 parsed twice (an edit), one record."""
    store = ParsedStore()
    for version in ("1.1", "2.1"):
        file = SourceFile("hr_docs:HR-PL-04", version, "Remote work", "en", MARKDOWN, BODY.encode())
        store.record(EVENTS[0], MarkdownParser().parse(file))
    store.record(EVENTS[4], None)  # identity:users/alice is a record, not a document
    return store


@pytest.fixture
def chunks() -> ChunkStore:
    """T4's results as the inspector keeps them: HR-PL-04 chunked twice (an edit)."""
    store = ChunkStore()
    chunker = StructureAwareChunker()
    for version in ("1.1", "2.1"):
        file = SourceFile("hr_docs:HR-PL-04", version, "Remote work", "en", MARKDOWN, BODY.encode())
        cut = chunker.chunk(MarkdownParser().parse(file))
        store.record(
            EVENTS[0].model_copy(update={"version": version}),
            "Remote work",
            cut,
            chunker_id=chunker.chunker_id,
            max_tokens=chunker.max_tokens,
        )
    return store


@pytest.fixture
def client(
    log: EventLog,
    bus: InMemoryEventBus,
    registry: InMemoryRegistry,
    rawstore: InMemoryRawStore,
    decisions: DecisionLog,
    parsed: ParsedStore,
    chunks: ChunkStore,
) -> TestClient:
    return TestClient(
        _app(
            log,
            bus=bus,
            registry=registry,
            rawstore=rawstore,
            decisions=decisions,
            parsed=parsed,
            chunks=chunks,
        )
    )


def test_the_log_is_the_inspector_groups_copy_of_the_bus(
    log: EventLog, bus: InMemoryEventBus
) -> None:
    assert log.cursor == 5
    assert [e.event for e in log.entries()] == EVENTS  # one partition order kept per item
    first = log.entries()[0]
    assert (first.partition, first.offset) == (0, 0)  # HR-PL-04 lives in partition 0
    assert {g.group: g.lag for g in bus.stats().groups} == {"inspector": 0}


def test_documents_show_each_items_current_state(client: TestClient) -> None:
    documents = client.get("/api/documents").json()
    assert [d["document_key"] for d in documents] == [
        "hr_docs:HR-PL-04",
        "hr_docs:HR-PL-99",
        "identity:users/alice",
    ]
    edited, gone, alice = documents
    assert (edited["last"]["version"], edited["last"]["changed_by"], edited["events"]) == (
        "2.1",
        "dana",
        2,
    )
    assert edited["last"]["acl"] == {"users": [], "groups": ["all-staff"]}
    assert gone["deleted"] is True
    assert alice["last"]["person_id"] == "alice"
    only = client.get("/api/documents", params={"source": "identity"}).json()
    assert [d["document_key"] for d in only] == ["identity:users/alice"]


def test_events_page_after_a_cursor_and_filter(client: TestClient, log: EventLog) -> None:
    page = client.get("/api/events", params={"since": 3}).json()
    assert [e["seq"] for e in page["events"]] == [4, 5]
    assert page["cursor"] == 5
    history = client.get("/api/events", params={"document_key": "hr_docs:HR-PL-04"}).json()
    assert [e["version"] for e in history["events"]] == ["1.1", "2.1"]  # the item's history
    log.append(InMemoryEventBus().publish(_event("HR-PL-01", ChangeOp.UPSERT, "2.1")))
    assert [e["seq"] for e in client.get("/api/events", params={"since": 5}).json()["events"]] == [
        6
    ]


def test_sources_count_items_and_deletes(client: TestClient) -> None:
    sources = {s["source"]: s for s in client.get("/api/sources").json()}
    assert (sources["hr_docs"]["items"], sources["hr_docs"]["deleted"]) == (1, 1)
    assert sources["hr_docs"]["events"] == 4
    assert sources["identity"]["items"] == 1


@pytest.mark.parametrize("profile", ["ci", "bedrock", "production", ""])
def test_it_refuses_every_profile_but_local(profile: str) -> None:
    """D19 gate: the inspector shows everything, so it never runs outside local."""
    with pytest.raises(InspectorNotAllowedError, match="local"):
        _app(EventLog(), profile=profile)


def test_it_is_read_only(client: TestClient) -> None:
    routes = [r for r in client.app.routes if isinstance(r, APIRoute)]  # type: ignore[attr-defined]
    assert routes
    assert {m for r in routes for m in (r.methods or set())} == {"GET"}


def test_it_binds_to_the_loopback_interface_only() -> None:
    assert HOST == "127.0.0.1"


def test_the_ui_contract_is_up_to_date() -> None:
    """ui/openapi.json is what the UI's types are generated from: it must match the API.

    Fix with: uv run ekb inspector --export-openapi ui/openapi.json; cd ui; npm run gen:types
    """
    committed = (REPO_ROOT / "ui" / "openapi.json").read_text(encoding="utf-8")
    assert committed == openapi_json(_app(EventLog()))


def test_events_say_where_they_sit_in_the_bus(client: TestClient) -> None:
    events = client.get("/api/events").json()["events"]
    places = [(e["source_id"], e["partition"], e["offset"]) for e in events]
    (first, second) = [(p, o) for source_id, p, o in places if source_id == "HR-PL-04"]
    assert first[0] == second[0]  # one partition
    assert first[1] < second[1]  # in order


def test_changelog_shows_partitions_and_each_groups_position(client: TestClient) -> None:
    view = client.get("/api/changelog").json()
    assert (view["partitions"], view["events"], sum(view["end_offsets"])) == (3, 5, 5)
    groups = {g["group"]: g for g in view["groups"]}
    assert set(groups) == {"inspector", "registry"}
    assert groups["registry"]["lag"] == 0
    assert sum(groups["registry"]["committed"]) == 5


def test_registry_shows_tombstones_without_content_and_counts_originals(
    client: TestClient,
) -> None:
    records = {r["document_key"]: r for r in client.get("/api/registry").json()}
    assert list(records) == ["hr_docs:HR-PL-04", "hr_docs:HR-PL-99", "identity:users/alice"]
    edited, gone = records["hr_docs:HR-PL-04"], records["hr_docs:HR-PL-99"]
    assert (edited["version"], edited["events"], edited["originals"]) == ("2.1", 2, 2)
    assert edited["acl"] == {"users": [], "groups": ["all-staff"]}
    assert gone["deleted"] is True
    assert (gone["content_hash"], gone["raw_pointer"], gone["person_id"]) == (None, None, None)
    assert gone["acl"] == {"users": [], "groups": []}
    only = client.get("/api/registry", params={"source": "identity"}).json()
    assert [r["document_key"] for r in only] == ["identity:users/alice"]


def test_originals_list_versions_and_never_the_bytes(client: TestClient) -> None:
    versions = client.get("/api/originals", params={"document_key": "hr_docs:HR-PL-04"}).json()
    assert [(v["version"], v["size"]) for v in versions] == [("1.1", 5), ("2.1", 6)]
    assert versions[0]["pointer"] == "mem://ekb-raw/hr_docs/HR-PL-04/1.1"
    assert all("first" not in str(v) and "second" not in str(v) for v in versions)
    assert client.get("/api/originals", params={"document_key": "hr_docs:none"}).json() == []
    assert client.get("/api/originals").status_code == 422  # document_key is required


def test_t2_decisions_show_action_reason_and_totals(client: TestClient) -> None:
    page = client.get("/api/t2/decisions").json()
    assert page["cursor"] == 6
    assert page["counts"] == {"process": 3, "acl_only": 0, "skip": 2, "purge": 1}
    rows = [(d["source_id"], d["version"], d["action"]) for d in page["decisions"]]
    assert rows == [
        ("HR-PL-04", "1.1", "process"),
        ("HR-PL-99", "1.1", "process"),
        ("HR-PL-04", "2.1", "skip"),  # every upsert here has hash "h": same text
        ("HR-PL-99", "1.1", "purge"),
        ("users/alice", "0", "process"),
        ("HR-PL-04", "2.1", "skip"),
    ]
    assert page["decisions"][2]["reason"] == "new version, same content and readers as v1.1"
    repeat = page["decisions"][-1]
    assert (repeat["previous_version"], repeat["reason"]) == (
        "2.1",
        "repeat: v2.1 was already processed",
    )
    purge = page["decisions"][3]
    assert (purge["op"], purge["purged"], purge["acl"]) == (
        "delete",
        0,  # this test's raw store never held HR-PL-99's original
        {"users": [], "groups": []},
    )


def test_t2_decisions_page_after_a_cursor_and_filter(
    client: TestClient, decisions: DecisionLog
) -> None:
    page = client.get("/api/t2/decisions", params={"since": 4}).json()
    assert [d["seq"] for d in page["decisions"]] == [5, 6]
    skipped = client.get("/api/t2/decisions", params={"action": "skip"}).json()
    assert [d["seq"] for d in skipped["decisions"]] == [3, 6]
    assert skipped["counts"]["process"] == 3  # totals are never filtered
    one = client.get("/api/t2/decisions", params={"document_key": "hr_docs:HR-PL-99"}).json()
    assert [d["action"] for d in one["decisions"]] == ["process", "purge"]
    identity = client.get("/api/t2/decisions", params={"source": "identity"}).json()
    assert [d["source_id"] for d in identity["decisions"]] == ["users/alice"]
    assert client.get("/api/t2/decisions", params={"action": "maybe"}).status_code == 422


def test_t3_lists_parsed_documents_and_counts_records_not_parsed(client: TestClient) -> None:
    view = client.get("/api/t3/parsed").json()
    (doc,) = view["documents"]
    assert (doc["document_key"], doc["version"], doc["title"], doc["runs"]) == (
        "hr_docs:HR-PL-04",
        "2.1",  # the latest parse
        "Remote work",
        2,  # parsed twice: once per processed version
    )
    assert (doc["source"], doc["source_id"], doc["language"]) == ("hr_docs", "HR-PL-04", "en")
    assert doc["blocks"] == 4
    assert doc["kinds"] == {"heading": 2, "list_item": 1, "table": 1}
    assert doc["parser_id"].startswith("markdown-it-py@")
    assert view["records"] == [{"source": "identity", "items": 1}]
    only = client.get("/api/t3/parsed", params={"source": "identity"}).json()
    assert only == {"documents": [], "records": [{"source": "identity", "items": 1}]}


def test_t3_shows_one_documents_blocks_and_clean_text(client: TestClient) -> None:
    view = client.get("/api/t3/parsed/document", params={"document_key": "hr_docs:HR-PL-04"})
    assert view.status_code == 200
    body = view.json()
    assert body["summary"]["version"] == "2.1"
    item, table = body["blocks"][2], body["blocks"][3]
    assert (item["kind"], item["marker"], item["section"]) == (
        "list_item",
        "-",
        ["Remote work", "The rule"],
    )
    assert table["rows"] == [["Day", "Where"], ["Fri", "Home"]]
    assert body["text"] == (
        "Remote work\n\nThe rule\n\n- Two days a week.\n\nDay | Where\nFri | Home"
    )


@pytest.mark.parametrize("key", ["hr_docs:none", "identity:users/alice"])
def test_t3_has_nothing_for_unknown_items_or_records(client: TestClient, key: str) -> None:
    response = client.get("/api/t3/parsed/document", params={"document_key": key})
    assert response.status_code == 404
    assert client.get("/api/t3/parsed/document").status_code == 422  # document_key is required


def test_a_purged_item_s_parsed_text_is_never_returned(
    client: TestClient, parsed: ParsedStore
) -> None:
    """Deletion gate, inspector side: a purge drops the parsed text with the originals."""
    assert parsed.drop("hr_docs:HR-PL-04") is True
    assert parsed.drop("identity:users/alice") is True
    gone = client.get("/api/t3/parsed/document", params={"document_key": "hr_docs:HR-PL-04"})
    assert gone.status_code == 404
    assert client.get("/api/t3/parsed").json() == {"documents": [], "records": []}
    assert parsed.drop("hr_docs:HR-PL-04") is False  # dropping again is harmless


def test_a_re_created_document_starts_counting_runs_again(parsed: ParsedStore) -> None:
    entry = parsed.get("hr_docs:HR-PL-04")
    assert entry is not None
    parsed.drop("hr_docs:HR-PL-04")
    parsed.record(EVENTS[0], entry.document)
    again = parsed.get("hr_docs:HR-PL-04")
    assert again is not None
    assert again.runs == 1


def test_t4_lists_chunked_documents_with_their_sizes(client: TestClient) -> None:
    view = client.get("/api/t4/chunks").json()
    assert view["chunks"] == 1
    (doc,) = view["documents"]
    assert (doc["document_key"], doc["version"], doc["title"], doc["runs"]) == (
        "hr_docs:HR-PL-04",
        "2.1",  # the latest chunking
        "Remote work",
        2,  # chunked twice: once per processed version
    )
    assert (doc["source"], doc["source_id"]) == ("hr_docs", "HR-PL-04")
    assert doc["chunker_id"] == "structure_aware@1(max_tokens=400)"
    assert (doc["chunks"], doc["max_tokens"], doc["over_limit"]) == (1, 400, 0)
    assert doc["tokens"] == doc["smallest"] == doc["largest"] == 14
    assert client.get("/api/t4/chunks", params={"source": "identity"}).json() == {
        "documents": [],
        "chunks": 0,
    }


def test_t4_shows_one_documents_chunks_none_of_them_publishable(client: TestClient) -> None:
    view = client.get("/api/t4/chunks/document", params={"document_key": "hr_docs:HR-PL-04"})
    assert view.status_code == 200
    body = view.json()
    assert body["summary"]["version"] == "2.1"
    (chunk,) = body["chunks"]
    assert chunk == {
        "chunk_id": "hr_docs:HR-PL-04#0",
        "position": 0,
        "section": ["Remote work", "The rule"],
        "tokens": 14,
        "text": "The rule\n\n- Two days a week.\n\nDay | Where\nFri | Home",
        "context_header": "",
        "header_id": "",  # T4 without a header writer, as S6 wired it
        "department": None,  # T5 has not run on these
        "sensitivity": None,
        "pii": [],
        "classifier_id": "",
        "why": "",
        "entity_ids": [],  # T6 has not run on these either
        "linker_id": "",
        "mentions": [],
        "embedding_model": None,  # nor T7 (S10)
        "dimensions": None,
        "vector_preview": [],
        "vector_state": "none",
        "publishable": False,  # the publication gate: no sensitivity, vector or ACL yet
    }
    assert body["summary"]["sensitivity"] == {}
    assert body["summary"]["header_note"] == body["summary"]["classifier_note"] == ""


def test_t4_shows_t5_s_labels_and_why() -> None:
    """S8: the chunks the inspector keeps carry their header and T5's labels."""
    body = (
        "# Salaries\n\nRestricted to HR. Do not forward.\n\n## Contact\nWrite to hr@example.com.\n"
    )
    file = SourceFile("hr_docs:HR-X", "1.1", "Salaries", "en", MARKDOWN, body.encode(), "people")
    document = MarkdownParser().parse(file)
    cut = StructureAwareChunker().chunk(document)
    result = ClassifyStep(RulesClassifier()).handle(document, cut)
    store = ChunkStore()
    store.record(
        EVENTS[0].model_copy(update={"source_id": "HR-X"}),
        "Salaries",
        result.chunks,
        chunker_id="structure_aware@1(max_tokens=400)",
        max_tokens=400,
        header_note="no header: the none option writes none",
        classifier_note=result.note,
        why=result.why,
    )
    client = TestClient(_app(EventLog(), chunks=store))

    view = client.get("/api/t4/chunks/document", params={"document_key": "hr_docs:HR-X"}).json()

    assert view["summary"]["sensitivity"] == {"restricted": 2}
    assert view["summary"]["classifier_note"] == "department people (from the source)"
    assert view["summary"]["header_note"].startswith("no header")
    first, second = view["chunks"]
    assert (first["sensitivity"], first["department"], first["classifier_id"]) == (
        "restricted",
        "people",
        "rules@1",
    )
    assert first["why"] == "restricted: the document says “Restricted to”"
    assert second["pii"] == ["email"]  # an email is flagged but does not raise the chunk
    assert second["publishable"] is False  # labelled, but no vector or ACL yet


@pytest.mark.parametrize("key", ["hr_docs:none", "identity:users/alice"])
def test_t4_has_nothing_for_unknown_items_or_records(client: TestClient, key: str) -> None:
    response = client.get("/api/t4/chunks/document", params={"document_key": key})
    assert response.status_code == 404
    assert client.get("/api/t4/chunks/document").status_code == 422  # document_key is required


def test_a_purged_item_s_chunks_are_never_returned(client: TestClient, chunks: ChunkStore) -> None:
    """Deletion gate, inspector side: a purge drops the chunks with the parsed text."""
    assert chunks.drop("hr_docs:HR-PL-04") is True
    gone = client.get("/api/t4/chunks/document", params={"document_key": "hr_docs:HR-PL-04"})
    assert gone.status_code == 404
    assert client.get("/api/t4/chunks").json() == {"documents": [], "chunks": 0}
    assert chunks.drop("hr_docs:HR-PL-04") is False  # dropping again is harmless


def test_chunks_of_a_closed_lifetime_are_hidden_before_the_purge(chunks: ChunkStore) -> None:
    """A delete is published before T2 purges: the API hides the old lifetime at once."""
    client = TestClient(
        create_inspector_app(
            EventLog(),
            profile="local",
            bus=InMemoryEventBus(),
            registry=InMemoryRegistry(),
            rawstore=InMemoryRawStore(),
            decisions=DecisionLog(),
            parsed=ParsedStore(),
            chunks=chunks,
            current_lifetime=lambda key: 1,  # a delete closed lifetime 0
        )
    )
    assert client.get("/api/t4/chunks").json()["documents"] == []
    url = "/api/t4/chunks/document"
    assert client.get(url, params={"document_key": "hr_docs:HR-PL-04"}).status_code == 404


def test_a_re_created_document_starts_counting_chunk_runs_again(chunks: ChunkStore) -> None:
    entry = chunks.get("hr_docs:HR-PL-04")
    assert entry is not None
    chunks.drop("hr_docs:HR-PL-04")
    chunks.record(EVENTS[0], entry.title, entry.chunks, chunker_id=entry.chunker_id, max_tokens=400)
    again = chunks.get("hr_docs:HR-PL-04")
    assert again is not None
    assert again.runs == 1


# ------------------------------------------------------------------ T6 entities (S9)

CONTACTS = (
    "# Contacts\n\n## Milan\nAsk Bruno Moretti or the People team.\n\n## Madrid\nLucía Navarro.\n"
)


def _entity_world(
    current_lifetime: int = 0,
) -> tuple[TestClient, InMemoryEntityStore, EntityPanel, ChunkStore]:
    """The inspector over a full entity store and one linked contacts page."""
    store = InMemoryEntityStore()
    panel = EntityPanel(store)
    for entity in ENTITIES:
        store.put(entity)
        panel.lifetimes.record(entity.source_key, 0)
    file = SourceFile("hr_docs:HR-C", "1.1", "Contacts", "en", MARKDOWN, CONTACTS.encode())
    document = MarkdownParser().parse(file)
    result = LinkStep(ExactMatchLinker(), store).handle(
        document, StructureAwareChunker().chunk(document)
    )
    chunks = ChunkStore()
    chunks.record(
        EVENTS[0].model_copy(update={"source_id": "HR-C"}),
        "Contacts",
        result.chunks,
        chunker_id="structure_aware@1(max_tokens=400)",
        max_tokens=400,
        linker_note=result.note,
        mentions=result.mentions,
    )
    app = create_inspector_app(
        EventLog(),
        profile="local",
        bus=InMemoryEventBus(),
        registry=InMemoryRegistry(),
        rawstore=InMemoryRawStore(),
        decisions=DecisionLog(),
        parsed=ParsedStore(),
        chunks=chunks,
        current_lifetime=lambda key: current_lifetime if key.startswith("hr_api:") else 0,
        entities=panel,
    )
    return TestClient(app), store, panel, chunks


def test_t4_shows_t6_s_links_and_the_words_behind_them() -> None:
    client, *_ = _entity_world()
    view = client.get("/api/t4/chunks/document", params={"document_key": "hr_docs:HR-C"}).json()
    assert view["summary"]["entities"] == ["person:bob", "person:lucia", "team:T-PPL"]
    assert view["summary"]["linker_note"] == "matched against 12 people and 5 teams"
    milan, madrid = view["chunks"]
    assert milan["entity_ids"] == ["person:bob", "team:T-PPL"]
    assert milan["linker_id"] == "exact_match@2"
    assert milan["mentions"] == [
        {"entity_id": "person:bob", "text": "Bruno Moretti", "rule": "name"},
        {"entity_id": "team:T-PPL", "text": "People team", "rule": "team"},
    ]
    assert madrid["entity_ids"] == ["person:lucia"]
    assert madrid["publishable"] is False  # linked, but no vector or ACL yet


def test_t6_lists_every_entity_with_the_chunks_that_name_it() -> None:
    client, *_ = _entity_world()
    view = client.get("/api/t6/entities").json()
    assert (view["people"], view["teams"], view["linked_chunks"]) == (12, 5, 2)
    by_id = {e["entity_id"]: e for e in view["entities"]}
    bob = by_id["person:bob"]
    assert (bob["name"], bob["aliases"], bob["team_id"]) == (
        "Bruno Moretti",
        ["bruno.moretti@example.com"],
        "team:T-PPL",
    )
    assert [(link["chunk_id"], link["text"]) for link in bob["links"]] == [
        ("hr_docs:HR-C#0", "Bruno Moretti")
    ]
    assert by_id["team:T-ENG"]["links"] == []
    assert view["stale_links"] == []
    teams = client.get("/api/t6/entities", params={"kind": "team"}).json()
    assert [e["entity_id"] for e in teams["entities"]] == [
        "team:T-ENG",
        "team:T-FIN",
        "team:T-PPL",
        "team:T-PRD",
        "team:T-SAL",
    ]


def test_a_link_to_an_entity_the_store_no_longer_holds_is_listed_as_stale() -> None:
    """S9's stated limit, made visible: the page keeps a leaver's ID until it changes."""
    client, store, *_ = _entity_world()
    store.remove("hr_api:employees/lucia")
    view = client.get("/api/t6/entities").json()
    assert "person:lucia" not in {e["entity_id"] for e in view["entities"]}
    assert [(s["entity_id"], s["chunk_id"]) for s in view["stale_links"]] == [
        ("person:lucia", "hr_docs:HR-C#1")
    ]
    teams = client.get("/api/t6/entities", params={"kind": "team"}).json()
    assert teams["stale_links"] == []  # a person's stale link is not a team's


def test_an_entity_whose_record_was_deleted_is_hidden_before_the_purge() -> None:
    """Deletion gate, inspector side: a delete closed every HR record's lifetime 0."""
    client, store, *_ = _entity_world(current_lifetime=1)
    assert len(store.entities()) == 17  # T2 has not purged them yet
    view = client.get("/api/t6/entities").json()
    assert view["entities"] == []
    assert (view["people"], view["teams"]) == (0, 0)


def test_a_purged_page_takes_its_links_off_the_entities() -> None:
    client, _, _, chunks = _entity_world()
    chunks.drop("hr_docs:HR-C")
    view = client.get("/api/t6/entities").json()
    assert view["linked_chunks"] == 0
    assert all(e["links"] == [] for e in view["entities"])


def test_recreation_during_listing_cannot_validate_deleted_entity_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old = ENTITIES[0]
    store = InMemoryEntityStore()
    panel = EntityPanel(store)
    store.put(old)
    panel.lifetimes.record(old.source_key, 0)
    read = store.entities

    def recreate_after_read(kind: EntityKind | None = None) -> tuple[Entity, ...]:
        previous = read(kind)
        store.remove(old.source_key)
        store.put(old.model_copy(update={"name": "Replacement"}))
        panel.lifetimes.record(old.source_key, 1)
        return previous

    monkeypatch.setattr(store, "entities", recreate_after_read)
    assert panel.visible(lambda _: 1) == ()


def test_without_an_entity_store_the_list_is_empty() -> None:
    client = TestClient(_app(EventLog()))
    assert client.get("/api/t6/entities").json() == {
        "entities": [],
        "people": 0,
        "teams": 0,
        "linked_chunks": 0,
        "stale_links": [],
    }
