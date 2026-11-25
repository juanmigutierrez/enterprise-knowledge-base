"""``ekb inspector``: the connectors run in rounds against the mocks; the command is local only."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sources.mock_docs.app import create_app as docs_app
from sources.mock_hr_api.app import create_app as hr_api_app
from sources.mock_hr_db.db import create_database
from sources.mock_idp.app import create_app as idp_app
from typer.testing import CliRunner

from ekb.app import inspector as inspector_module
from ekb.app.changelog import ChangeLog
from ekb.app.cli import app
from ekb.app.gateway import build_gateway
from ekb.app.inspector import SyncLoop, build_connectors
from ekb.contracts import Acl, ChangeOp, DiffAction, Sensitivity
from ekb.contracts.listing import NeedsOriginal
from ekb.operate.inspector.log import EventLog
from ekb.platform.config import load_settings
from tests.http import as_httpx
from tests.unit.changelog.factories import acl_change
from tests.unit.platform.gateway.helpers import make_world


@pytest.fixture
def clients() -> Iterator[tuple[TestClient, TestClient, TestClient]]:
    with (
        TestClient(docs_app(post=lambda url, payload: None)) as docs,
        TestClient(hr_api_app()) as hr,
        TestClient(idp_app()) as idp,
    ):
        yield docs, hr, idp


def test_round_zero_loads_every_source_once(
    clients: tuple[TestClient, TestClient, TestClient], tmp_path: Path
) -> None:
    docs, hr, idp = clients
    create_database(tmp_path / "hr.sqlite")
    log, changelog = EventLog(), ChangeLog.in_memory()
    connectors = build_connectors(
        as_httpx(docs), as_httpx(hr), as_httpx(idp), tmp_path / "hr.sqlite", changelog
    )
    loop = SyncLoop(connectors, changelog, log, listing_every=2)
    assert loop.run_round() == 12 + 17 + 16 + 12  # pages, HR records, DB rows, users
    # T2 recorded everything before the listings compared with the registry, so the
    # HR listing added nothing on top of the delta's first poll.
    registry = changelog.registry
    assert {s: len(registry.known(s)) for s in ("hr_docs", "hr_api", "hr_db", "identity")} == {
        "hr_docs": 12,
        "hr_api": 17,
        "hr_db": 16,
        "identity": 12,
    }
    assert log.cursor == 57  # the inspector's group has read every event
    assert {g.group: g.lag for g in changelog.bus.stats().groups} == {
        "inspector": 0,
        "t2_diff": 0,
    }
    assert loop.decisions.counts() == {
        DiffAction.PROCESS: 57,  # everything is new
        DiffAction.ACL_ONLY: 0,
        DiffAction.SKIP: 0,
        DiffAction.PURGE: 0,
    }
    assert len(changelog.rawstore.versions("hr_docs:HR-PL-04")) == 1
    assert loop.run_round() == 0  # nothing changed

    hr.delete("/employees/E-1012")
    assert loop.run_round() == 2  # leave ACL revocation, then the HR listing delete
    revoked = log.entries(since=log.cursor - 2)[0].event
    assert revoked.op is ChangeOp.ACL_CHANGE
    assert revoked.acl.users == frozenset()
    (last,) = log.entries(since=log.cursor - 1)
    assert (last.event.op, last.event.source_id) == (ChangeOp.DELETE, "employees/E-1012")
    gone = registry.get("hr_api:employees/E-1012")
    assert gone is not None
    assert gone.deleted
    assert "employees/E-1012" not in registry.known("hr_api")
    # T2: the revocation took the fast lane; the delete was purged with its original.
    revocation, purge = loop.decisions.entries(since=loop.decisions.cursor - 2)
    assert revocation.decision.action is DiffAction.ACL_ONLY
    assert (purge.decision.action, purge.purged) == (DiffAction.PURGE, 1)
    assert changelog.rawstore.versions("hr_api:employees/E-1012") == []


def test_t3_parses_what_t2_processes_and_forgets_what_it_purges(
    clients: tuple[TestClient, TestClient, TestClient], tmp_path: Path
) -> None:
    docs, hr, idp = clients
    create_database(tmp_path / "hr.sqlite")
    changelog = ChangeLog.in_memory()
    connectors = build_connectors(
        as_httpx(docs), as_httpx(hr), as_httpx(idp), tmp_path / "hr.sqlite", changelog
    )
    loop = SyncLoop(connectors, changelog, EventLog(), listing_every=1)
    loop.run_round()
    parsed = loop.parsed
    assert len(parsed.entries()) == 12  # every page; nothing from the other sources
    assert {e.document.document_key.partition(":")[0] for e in parsed.entries()} == {"hr_docs"}
    assert parsed.records() == {"hr_api": 17, "hr_db": 16, "identity": 12}  # D7: not parsed

    page = docs.get("/pages/HR-PL-01").json()
    docs.put("/pages/HR-PL-01", json={"body": page["body"]})  # same text: T2 skips
    docs.put("/pages/HR-PL-04", json={"body": "# Remote work\n\nFriday too.\n"})
    docs.delete("/pages/HR-PL-99")
    loop.run_round()  # the full listing finds all three

    resaved, edited = parsed.get("hr_docs:HR-PL-01"), parsed.get("hr_docs:HR-PL-04")
    assert resaved is not None
    assert edited is not None
    assert (resaved.runs, resaved.document.version) == (1, "1.1")  # not parsed again
    assert (edited.runs, edited.document.version) == (2, "2.1")
    assert [b.text for b in edited.document.blocks] == ["Remote work", "Friday too."]
    assert parsed.get("hr_docs:HR-PL-99") is None  # deleted: its parsed text is gone
    assert len(parsed.entries()) == 11


def test_t4_chunks_what_t3_parses_and_forgets_what_t2_purges(
    clients: tuple[TestClient, TestClient, TestClient], tmp_path: Path
) -> None:
    docs, hr, idp = clients
    create_database(tmp_path / "hr.sqlite")
    changelog = ChangeLog.in_memory()
    connectors = build_connectors(
        as_httpx(docs), as_httpx(hr), as_httpx(idp), tmp_path / "hr.sqlite", changelog
    )
    loop = SyncLoop(connectors, changelog, EventLog(), listing_every=1)
    loop.run_round()
    chunks = loop.chunks
    assert loop.t4.chunker_id == "structure_aware@1(max_tokens=400)"
    assert len(chunks.entries()) == 12  # every page; records are never chunked
    assert sum(len(e.chunks) for e in chunks.entries()) == 48

    page = docs.get("/pages/HR-PL-01").json()
    docs.put("/pages/HR-PL-01", json={"body": page["body"]})  # same text: T2 skips
    docs.put("/pages/HR-PL-04", json={"body": "# Remote work\n\n## Rule\nFriday too.\n"})
    docs.put("/pages/HR-PL-05/acl", json={"groups": ["es-employees"]})  # fast lane
    docs.delete("/pages/HR-PL-99")
    loop.run_round()

    resaved, edited = chunks.get("hr_docs:HR-PL-01"), chunks.get("hr_docs:HR-PL-04")
    revoked = chunks.get("hr_docs:HR-PL-05")
    assert resaved is not None
    assert edited is not None
    assert revoked is not None
    assert (resaved.runs, resaved.version) == (1, "1.1")  # not chunked again
    assert (revoked.runs, revoked.version) == (1, "1.1")  # readers changed, text didn't
    assert (edited.runs, edited.version) == (2, "2.1")
    assert [c.text for c in edited.chunks] == ["Rule\n\nFriday too."]
    assert chunks.get("hr_docs:HR-PL-99") is None  # deleted: its chunks are gone
    assert len(chunks.entries()) == 11


def test_headers_and_labels_in_the_live_loop_and_a_purge_forgets_the_replies(
    clients: tuple[TestClient, TestClient, TestClient], tmp_path: Path
) -> None:
    """S8: the loop writes headers through the gateway (fake models in the ci profile)
    and labels every chunk; a delete drops the document's cached replies too."""
    docs, hr, idp = clients
    create_database(tmp_path / "hr.sqlite")
    changelog = ChangeLog.in_memory()
    connectors = build_connectors(
        as_httpx(docs), as_httpx(hr), as_httpx(idp), tmp_path / "hr.sqlite", changelog
    )
    gateway = build_gateway(load_settings().gateway)
    loop = SyncLoop(
        connectors, changelog, EventLog(), listing_every=1, gateway=gateway, header="model"
    )
    loop.run_round()
    assert (loop.t4.header_id, loop.t5.classifier_id) == ("model@1(header@1)", "rules@1")
    entries = loop.chunks.entries()
    assert all(c.context_header and c.sensitivity for e in entries for c in e.chunks)
    restricted = loop.chunks.get("hr_docs:HR-CMP-01")
    assert restricted is not None
    assert {c.sensitivity for c in restricted.chunks} == {Sensitivity.RESTRICTED}
    assert restricted.header_note == "written by fake:fake-small, one request"
    assert restricted.classifier_note == "department people (from the source)"
    assert len(restricted.why) == len(restricted.chunks)
    assert gateway.cache.size() == 12  # one header reply per page

    docs.delete("/pages/HR-PL-99")
    loop.run_round()
    assert loop.chunks.get("hr_docs:HR-PL-99") is None
    assert gateway.cache.size() == 11  # its header reply went with it


def test_t7_embeds_in_the_live_loop_and_a_delete_forgets_the_vectors(
    clients: tuple[TestClient, TestClient, TestClient], tmp_path: Path
) -> None:
    """S10: the loop embeds every chunk through the gateway (the ci profile's fake
    embedding model); a deleted page's cached vectors go with it."""
    docs, hr, idp = clients
    create_database(tmp_path / "hr.sqlite")
    changelog = ChangeLog.in_memory()
    connectors = build_connectors(
        as_httpx(docs), as_httpx(hr), as_httpx(idp), tmp_path / "hr.sqlite", changelog
    )
    gateway = build_gateway(load_settings().gateway)
    loop = SyncLoop(
        connectors, changelog, EventLog(), listing_every=1, gateway=gateway, embedder="model"
    )
    loop.run_round()

    model_id = "model@1(input@1, fake:fake-embed, 32)"
    assert loop.t7.model_id == model_id
    entries = loop.chunks.entries()
    assert all(c.vector is not None for e in entries for c in e.chunks)
    assert {c.embedding_model for e in entries for c in e.chunks} == {model_id}
    assert {e.embedder_id for e in entries} == {model_id}
    remote = loop.chunks.get("hr_docs:HR-PL-04")
    assert remote is not None
    assert remote.embed_note.endswith("4 chunks embedded: 4 sent, 0 from the cache")
    page = loop.chunks.get("hr_docs:HR-PL-99")
    assert page is not None
    before = gateway.cache.size()

    docs.delete("/pages/HR-PL-99")
    loop.run_round()
    assert loop.chunks.get("hr_docs:HR-PL-99") is None
    assert gateway.cache.size() == before - len(page.chunks)  # its vectors went with it


def test_the_command_stops_when_the_embedding_model_cannot_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S10: T7 fails closed, so the inspector checks its model before loading anything."""
    monkeypatch.setenv("EKB_PROFILE", "local")
    monkeypatch.setattr(inspector_module, "build_gateway", lambda config: make_world().gateway)
    monkeypatch.setattr(inspector_module, "probe", lambda gateway: "ollama:bge-m3: unavailable")

    result = CliRunner().invoke(app, ["inspector"])

    assert result.exit_code == 2
    assert "T7 cannot embed: ollama:bge-m3: unavailable" in result.output
    assert "ollama pull bge-m3" in result.output
    assert "--extra ollama" in result.output
    assert "--embed fake" in result.output


def test_t6_links_in_the_live_loop_and_a_leaver_leaves_the_store(
    clients: tuple[TestClient, TestClient, TestClient], tmp_path: Path
) -> None:
    """S9: round 0 fills the entity store from the HR records before the pages are
    linked; the HR listing finds a leaver's delete and the store drops them."""
    docs, hr, idp = clients
    create_database(tmp_path / "hr.sqlite")
    changelog = ChangeLog.in_memory()
    connectors = build_connectors(
        as_httpx(docs), as_httpx(hr), as_httpx(idp), tmp_path / "hr.sqlite", changelog
    )
    loop = SyncLoop(connectors, changelog, EventLog(), listing_every=1)
    loop.run_round()
    store = loop.entities.store
    assert loop.t6.linker_id == "exact_match@2"
    assert len(store.entities()) == 17  # 12 people, 5 teams
    remote = loop.chunks.get("hr_docs:HR-PL-04")
    assert remote is not None
    assert [c.entity_ids for c in remote.chunks] == [(), ("team:T-PPL",), (), ("team:T-PPL",)]
    assert remote.linker_note == "matched against 12 people and 5 teams"
    assert [m.text for m in remote.mentions[1]] == ["People team"]
    lifetime = loop.entities.lifetimes.snapshot()["hr_api:employees/E-1009"]
    assert lifetime == changelog.current_lifetime("hr_api:employees/E-1009")

    hr.delete("/employees/E-1009")
    loop.run_round()
    assert store.get("person:lucia") is None
    assert len(store.entities()) == 16


def test_t8_publishes_in_the_live_loop_and_follows_groups_readers_and_deletes(
    clients: tuple[TestClient, TestClient, TestClient], tmp_path: Path
) -> None:
    """S11: every chunk the loop cuts is published with its page's readers; a group change,
    a readers change (the fast lane) and a delete reach the search store."""
    docs, hr, idp = clients
    create_database(tmp_path / "hr.sqlite")
    changelog = ChangeLog.in_memory()
    connectors = build_connectors(
        as_httpx(docs), as_httpx(hr), as_httpx(idp), tmp_path / "hr.sqlite", changelog
    )
    loop = SyncLoop(connectors, changelog, EventLog(), listing_every=1)
    loop.run_round()
    index, stores = loop.permissions.index, loop.permissions.permissions
    cut = sum(len(e.chunks) for e in loop.chunks.entries())
    assert len(index.chunks()) == cut > 0
    assert all(c.publishable for c in index.chunks())
    assert all(c.publishable for e in loop.chunks.entries() for c in e.chunks)
    assert len(stores.memberships()) == 12
    assert loop.permissions.resolver_id == loop.t8.resolver_id == "source_acl@1"
    lifetime = loop.permissions.lifetimes.snapshot()["hr_docs:HR-PL-02"]
    assert lifetime == changelog.current_lifetime("hr_docs:HR-PL-02")

    def pages(user: str) -> set[str]:
        return {c.document_key for c in index.readable_by(stores.principals(user))}

    assert "hr_docs:HR-PL-02" in pages("alice")
    idp.delete("/scim/v2/Groups/it-employees/members/alice")
    loop.run_round()
    assert "hr_docs:HR-PL-02" not in pages("alice")

    assert "hr_docs:HR-PL-05" in pages("dana")
    docs.put("/pages/HR-PL-05/acl", json={"groups": ["es-employees"]})
    loop.run_round()
    assert "hr_docs:HR-PL-05" not in pages("dana")
    record = stores.readers("hr_docs:HR-PL-05")
    assert record is not None
    assert record.acl == Acl(groups=frozenset({"es-employees"}))

    docs.delete("/pages/HR-PL-99")
    loop.run_round()
    assert index.chunks("hr_docs:HR-PL-99") == ()
    assert stores.readers("hr_docs:HR-PL-99") is None


def test_the_loop_needs_no_gateway_without_a_model_option(
    clients: tuple[TestClient, TestClient, TestClient], tmp_path: Path
) -> None:
    docs, hr, idp = clients
    changelog = ChangeLog.in_memory()
    connectors = build_connectors(
        as_httpx(docs), as_httpx(hr), as_httpx(idp), tmp_path / "missing.sqlite", changelog
    )
    loop = SyncLoop(connectors, changelog, EventLog())  # header none, rules, fake vectors
    assert (loop.t4.header_id, loop.t5.classifier_id) == ("none@1", "rules@1")
    assert loop.t7.model_id == "fake@1(input@1, sha256, 32)"
    with pytest.raises(ValueError, match="needs the model gateway"):
        SyncLoop(connectors, changelog, EventLog(), header="model")
    with pytest.raises(ValueError, match="needs the model gateway"):
        SyncLoop(connectors, changelog, EventLog(), embedder="model")


@pytest.mark.parametrize(
    ("option", "value"),
    [("--header", "sonnet"), ("--classifier", "presidio"), ("--embed", "bge_m3_ollama")],
)
def test_the_command_refuses_unknown_header_or_classifier_options(option: str, value: str) -> None:
    result = CliRunner().invoke(app, ["inspector", option, value])
    assert result.exit_code == 2
    assert "one of" in result.output


def test_a_failing_source_does_not_stop_the_others(
    clients: tuple[TestClient, TestClient, TestClient], tmp_path: Path
) -> None:
    docs, hr, idp = clients
    changelog = ChangeLog.in_memory()  # no database file: the CDC connector fails every round
    connectors = build_connectors(
        as_httpx(docs), as_httpx(hr), as_httpx(idp), tmp_path / "missing.sqlite", changelog
    )
    loop = SyncLoop(connectors, changelog, EventLog())
    assert loop.run_round() == 12 + 17 + 12
    assert any("hr_db" in error for error in loop.errors)


def test_the_loop_runs_in_the_background(
    clients: tuple[TestClient, TestClient, TestClient], tmp_path: Path
) -> None:
    docs, hr, idp = clients
    create_database(tmp_path / "hr.sqlite")
    changelog = ChangeLog.in_memory()
    connectors = build_connectors(
        as_httpx(docs), as_httpx(hr), as_httpx(idp), tmp_path / "hr.sqlite", changelog
    )
    loop = SyncLoop(connectors, changelog, EventLog())
    with loop.running(interval_s=0.01):
        while loop.rounds < 2:
            pass
    assert loop.errors == []


def test_the_command_refuses_outside_the_local_profile() -> None:
    result = CliRunner().invoke(app, ["inspector"])  # tests run with EKB_PROFILE=ci
    assert result.exit_code == 2
    assert "only in the 'local' profile" in result.output


def test_the_command_exports_the_schema(tmp_path: Path) -> None:
    target = tmp_path / "openapi.json"
    result = CliRunner().invoke(app, ["inspector", "--export-openapi", str(target)])
    assert result.exit_code == 0, result.output
    assert '"title": "ekb inspector"' in target.read_text()


@pytest.mark.parametrize(
    ("option", "value"),
    [("--listing-every", "0"), ("--interval", "0"), ("--interval", "-1"), ("--interval", "nan")],
)
def test_invalid_sync_schedule_is_rejected(option: str, value: str) -> None:
    result = CliRunner().invoke(app, ["inspector", option, value])
    assert result.exit_code == 2


def test_a_failing_consumer_is_retried_next_round_and_loses_nothing(
    clients: tuple[TestClient, TestClient, TestClient],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    docs, hr, idp = clients
    create_database(tmp_path / "hr.sqlite")
    log, changelog = EventLog(), ChangeLog.in_memory()
    connectors = build_connectors(
        as_httpx(docs), as_httpx(hr), as_httpx(idp), tmp_path / "hr.sqlite", changelog
    )
    loop = SyncLoop(connectors, changelog, log)

    def broken(_: object) -> None:
        raise ValueError("the inspector's copy failed")

    monkeypatch.setattr(log, "append", broken)
    loop.run_round()
    assert any("consumer group inspector" in error for error in loop.errors)
    assert log.cursor == 0
    monkeypatch.undo()
    loop.run_round()
    assert log.cursor == 57  # every event was still pending, none was skipped


def test_a_failing_t2_round_records_nothing_and_is_retried_in_full(
    clients: tuple[TestClient, TestClient, TestClient],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If T2 can't hand a decision on, it records nothing, so the retry is not mistaken
    for a repeat and skipped."""
    docs, hr, idp = clients
    create_database(tmp_path / "hr.sqlite")
    changelog = ChangeLog.in_memory()
    connectors = build_connectors(
        as_httpx(docs), as_httpx(hr), as_httpx(idp), tmp_path / "hr.sqlite", changelog
    )
    loop = SyncLoop(connectors, changelog, EventLog())

    def broken(decision: object, purged: int = 0) -> None:
        raise ValueError("the decision log failed")

    monkeypatch.setattr(loop.decisions, "append", broken)
    loop.run_round()
    assert any("consumer group t2_diff" in error for error in loop.errors)
    assert changelog.registry.records() == []
    monkeypatch.undo()
    loop.run_round()
    counts = loop.decisions.counts()
    assert counts[DiffAction.PROCESS] == 57  # every item processed, none lost
    # With nothing recorded, the HR listing reported the 17 records the delta had
    # already sent; T2 skipped those copies as repeats.
    assert counts[DiffAction.SKIP] == 17


@pytest.mark.parametrize(
    ("source", "item", "listing_index"),
    [
        ("hr_docs", "HR-PL-04", 0),
        ("hr_api", "employees/E-1001", 1),
    ],
)
def test_listing_repairs_missing_original_and_tombstones_absent_items(
    clients: tuple[TestClient, TestClient, TestClient],
    tmp_path: Path,
    source: str,
    item: str,
    listing_index: int,
) -> None:
    changelog = ChangeLog.in_memory()
    docs, hr, idp = (as_httpx(client) for client in clients)
    connectors = build_connectors(docs, hr, idp, tmp_path / "unused.sqlite", changelog)
    listing = connectors.listings[listing_index]
    listing.sync()
    changelog.record_processed()
    key = f"{source}:{item}"
    before = changelog.registry.get(key)
    assert before is not None
    assert before.raw_pointer is not None
    for source_id in (item, "vanished"):
        changelog.bus.publish(
            acl_change(source_id, before.version, Acl(), source, content_hash="mismatch")
        )
    changelog.record_processed()
    pending = changelog.registry.get(key)
    assert pending is not None
    assert pending.acl == Acl()
    assert pending.raw_pointer is None
    assert changelog.registry.known(source)[item] == NeedsOriginal(version=before.version)
    assert listing.sync() == 2
    changelog.record_processed()
    repaired = changelog.registry.get(key)
    gone = changelog.registry.get(f"{source}:vanished")
    assert repaired is not None
    assert repaired.raw_pointer == before.raw_pointer
    assert repaired.content_hash == before.content_hash
    assert gone is not None
    assert gone.deleted
    assert gone.version == before.version
    assert listing.sync() == 0
