"""The inspector's Model gateway panel (S7): routes, quotas, spend, calls; no key, no prompt."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from ekb.changelog.bus.options.in_memory import InMemoryEventBus
from ekb.changelog.rawstore.options.in_memory import InMemoryRawStore
from ekb.changelog.registry.options.in_memory import InMemoryRegistry
from ekb.operate.inspector.app import create_inspector_app
from ekb.operate.inspector.gateway import GatewayPanel
from ekb.operate.inspector.log import ChunkStore, DecisionLog, EventLog, ParsedStore
from ekb.platform.gateway.status import provider_status
from tests.unit.platform.gateway.helpers import FakeClock, ask, make_config, make_world

NOW = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)
PASSAGE = "Salary band E3 tops out at 70,000 EUR"


def client_for(panel: GatewayPanel | None) -> TestClient:
    app = create_inspector_app(
        EventLog(),
        profile="local",
        bus=InMemoryEventBus(),
        registry=InMemoryRegistry(),
        rawstore=InMemoryRawStore(),
        decisions=DecisionLog(),
        parsed=ParsedStore(),
        chunks=ChunkStore(),
        current_lifetime=lambda _: 0,
        gateway=panel,
    )
    return TestClient(app)


def busy_panel() -> GatewayPanel:
    """Three calls through a small gateway: a miss, a hit, and a failover."""
    world = make_world(max_spend_usd=5.0)
    ask(world.gateway, PASSAGE, document_key="hr_docs:HR-CMP-01")
    ask(world.gateway, PASSAGE, document_key="hr_docs:HR-CMP-01")
    world.clients["fake:one"].fail_next("unavailable")
    ask(world.gateway, "another passage")
    config = make_config()
    return GatewayPanel(
        config=config,
        log=world.log,
        providers=provider_status(config, {}),
        max_spend_usd=5.0,
        clock=FakeClock(NOW),
    )


def test_the_overview_shows_routes_models_quotas_and_the_cap() -> None:
    body = client_for(busy_panel()).get("/api/gateway").json()

    assert body["cache"] == "in_memory"
    assert body["max_spend_usd"] == 5.0
    assert {r["task"]: r["candidates"] for r in body["routes"]}["header"] == [
        "fake:one",
        "fake:two",
    ]
    paid = next(m for m in body["models"] if m["ref"] == "fake:paid")
    assert (paid["free"], paid["input_usd_per_million"], paid["output_usd_per_million"]) == (
        False,
        1.0,
        2.0,
    )
    quotas = {q["name"]: q for q in body["quotas"]}
    assert quotas["one_q"]["used_today"] == 2  # the miss and the failed attempt; not the hit
    assert quotas["two_q"]["used_today"] == 1
    assert body["providers"] == [
        {
            "provider": "fake",
            "key_env": None,
            "ready": True,
            "models": ["fake:one", "fake:two", "fake:paid", "fake:vec"],
        }
    ]


def test_calls_page_through_the_cost_log_with_a_cursor() -> None:
    client = client_for(busy_panel())

    page = client.get("/api/gateway/calls").json()
    later = client.get("/api/gateway/calls", params={"since": 2}).json()

    assert [c["outcome"] for c in page["calls"]] == ["ok", "hit", "unavailable", "ok"]
    assert [c["seq"] for c in page["calls"]] == [1, 2, 3, 4]
    assert page["cursor"] == 4
    assert [c["seq"] for c in later["calls"]] == [3, 4]
    totals = {(t["task"], t["candidate"]): t for t in page["totals"]}
    assert totals[("header", "fake:one")]["hits"] == 1
    assert totals[("header", "fake:one")]["failures"] == 1


def test_calls_can_be_filtered_by_task() -> None:
    page = client_for(busy_panel()).get("/api/gateway/calls", params={"task": "answer"}).json()

    assert page["calls"] == []
    assert page["cursor"] == 4  # the cursor still covers the whole log


def test_the_panel_shows_no_prompt_no_reply_and_no_key() -> None:
    world = make_world()
    reply = ask(world.gateway, PASSAGE, system="You are the HR assistant.")
    config = make_config()
    environ = {"FAKE_API_KEY": "sk-should-never-appear"}
    panel = GatewayPanel(config=config, log=world.log, providers=provider_status(config, environ))
    client = client_for(panel)

    text = json.dumps([client.get("/api/gateway").json(), client.get("/api/gateway/calls").json()])

    assert PASSAGE not in text
    assert "HR assistant" not in text
    assert reply.text not in text
    assert "sk-should-never-appear" not in text


def test_without_a_gateway_the_panel_is_empty_not_broken() -> None:
    client = client_for(None)

    assert client.get("/api/gateway").json()["routes"] == []
    assert client.get("/api/gateway/calls").json() == {"calls": [], "cursor": 0, "totals": []}
