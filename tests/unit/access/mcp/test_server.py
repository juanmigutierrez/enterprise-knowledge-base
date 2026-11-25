"""The Knowledge API's MCP server (S15): the token decides who a call acts for, nothing else.

The gates: no token or an unknown one is refused with 401 before any tool runs;
no tool accepts a user, and an argument the schema does not list is dropped; a
server reached without the bearer middleware answers no one; a failure inside
the service is reported without its details.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any

import httpx
import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult

from ekb.access.auth.options.static_tokens import IssuedToken, StaticTokens, new_token
from ekb.access.mcp.server import KnowledgeService, create_app, create_server
from ekb.app.demos._serve import serving
from ekb.contracts import Answer, AssistantAnswer, Caller, Identity

ALICE = IssuedToken("alice", "hr-assistant", new_token())


class Service:
    """Records who each call was for; ``fail`` makes every call raise."""

    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[tuple[str, Caller, str]] = []
        self.fail = fail

    def _seen(self, tool: str, caller: Caller, question: str = "") -> None:
        self.calls.append((tool, caller, question))
        if self.fail:
            raise RuntimeError("HR-CMP-01 is denied for alice")

    def whoami(self, caller: Caller) -> Identity:
        self._seen("whoami", caller)
        return Identity(user_id=caller.user_id, agent_id=caller.agent_id, reads_as="reads as x")

    def search(self, caller: Caller, question: str) -> Answer:
        self._seen("search", caller, question)
        return Answer(
            question=question, user_id=caller.user_id, reason="r", agent_id=caller.agent_id
        )

    def ask(self, caller: Caller, question: str) -> AssistantAnswer:
        self._seen("ask", caller, question)
        return AssistantAnswer(
            question=question, user_id=caller.user_id, agent_id=caller.agent_id, answer="a"
        )


@pytest.fixture
def service() -> Service:
    return Service()


@pytest.fixture
def url(service: Service) -> Iterator[str]:
    server = create_server(service, StaticTokens(issued=[ALICE]), base_url="http://127.0.0.1")
    with serving(create_app(server)) as served:
        yield served.url + "/mcp"


async def _call(url: str, token: str, tool: str, arguments: dict[str, Any]) -> CallToolResult:
    headers = {"Authorization": f"Bearer {token}"}
    async with (
        httpx2.AsyncClient(headers=headers) as http,
        Client(streamable_http_client(url, http_client=http)) as client,
    ):
        return await client.call_tool(tool, arguments)


def call(url: str, tool: str, arguments: dict[str, Any] | None = None) -> CallToolResult:
    return asyncio.run(_call(url, ALICE.token, tool, arguments or {}))


def test_the_service_port_is_what_the_app_implements() -> None:
    assert isinstance(Service(), KnowledgeService)


@pytest.mark.parametrize("token", [None, "guess", ALICE.token + "x"])
def test_a_missing_or_unknown_token_is_refused_before_any_tool_runs(
    url: str, service: Service, token: str | None
) -> None:
    headers = {"Accept": "application/json, text/event-stream"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    for method in ("initialize", "tools/list", "tools/call"):
        body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": {"name": "ask"}}
        reply = httpx.post(url, json=body, headers=headers, timeout=10.0)
        assert reply.status_code == 401
        assert reply.headers["www-authenticate"].startswith('Bearer error="invalid_token"')
    assert service.calls == []


def test_every_tool_runs_as_the_token_s_caller(url: str, service: Service) -> None:
    for tool, arguments in (
        ("whoami", {}),
        ("search", {"question": "q"}),
        ("ask", {"question": "q"}),
    ):
        result = call(url, tool, arguments)
        assert not result.is_error, result.content
        assert result.structured_content is not None
        assert result.structured_content["user_id"] == "alice"
    expected = ALICE.caller
    assert [c[1] for c in service.calls] == [expected] * 3


def test_no_tool_takes_a_user_and_an_unlisted_argument_is_dropped(
    url: str, service: Service
) -> None:
    async def tools() -> dict[str, dict[str, Any]]:
        headers = {"Authorization": f"Bearer {ALICE.token}"}
        async with (
            httpx2.AsyncClient(headers=headers) as http,
            Client(streamable_http_client(url, http_client=http)) as client,
        ):
            listed = await client.list_tools()
        return {t.name: t.input_schema for t in listed.tools}

    schemas = asyncio.run(tools())
    assert set(schemas) == {"whoami", "search", "ask"}
    assert schemas["whoami"].get("properties", {}) == {}
    for name in ("search", "ask"):
        assert set(schemas[name]["properties"]) == {"question"}
        assert schemas[name]["properties"]["question"]["maxLength"] == 1000
    result = call(url, "ask", {"question": "q", "user": "bob", "user_id": "bob"})
    assert not result.is_error
    assert service.calls[-1][1].user_id == "alice"


def test_an_empty_or_huge_question_is_refused_by_the_schema(url: str, service: Service) -> None:
    for question in ("", "x" * 1001):
        assert call(url, "ask", {"question": question}).is_error
    assert service.calls == []


def test_a_failure_inside_the_service_is_reported_without_its_details() -> None:
    failing = Service(fail=True)
    server = create_server(failing, StaticTokens(issued=[ALICE]), base_url="http://127.0.0.1")
    with serving(create_app(server)) as served:
        result = call(served.url + "/mcp", "ask", {"question": "salary bands"})
    assert result.is_error
    text = " ".join(getattr(block, "text", "") for block in result.content)
    assert "HR-CMP-01" not in text
    assert text == "Error executing tool ask"


def test_a_server_reached_without_the_bearer_middleware_answers_no_one(service: Service) -> None:
    server = create_server(service, StaticTokens(issued=[ALICE]), base_url="http://127.0.0.1")

    async def in_process() -> CallToolResult:
        async with Client(server) as client:
            return await client.call_tool("whoami", {})

    result = asyncio.run(in_process())
    assert result.is_error
    assert "not signed in" in " ".join(getattr(block, "text", "") for block in result.content)
    assert service.calls == []


def test_a_request_under_another_host_name_is_refused_even_with_a_token(
    url: str, service: Service
) -> None:
    """DNS rebinding: a web page cannot reach the local API through a name that resolves to it."""
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "page", "version": "1"},
        },
    }
    headers = {
        "Accept": "application/json, text/event-stream",
        "Authorization": f"Bearer {ALICE.token}",
    }
    assert httpx.post(url, json=body, headers=headers, timeout=10.0).status_code == 200
    evil = {**headers, "Host": "evil.example"}
    assert httpx.post(url, json=body, headers=evil, timeout=10.0).status_code == 421
    assert service.calls == []
