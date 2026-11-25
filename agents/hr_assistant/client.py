"""Ask the Knowledge API's ``ask`` tool over MCP and print the answer with its sources.

The token is the only identity the agent has: it is sent as ``Authorization:
Bearer <token>`` on every request, and the server decides from it whom the
agent acts for. The question goes in as it is; nothing here can ask "as
someone else".
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

DEFAULT_URL = "http://127.0.0.1:8080/mcp"
TIMEOUT_S = 60.0


class AssistantError(RuntimeError):
    """The Knowledge API could not be reached, refused the token, or the tool failed."""


@dataclass
class _Status:
    """The last HTTP status the server answered, to tell a refused token from an outage."""

    codes: list[int] = field(default_factory=list)

    async def record(self, response: httpx2.Response) -> None:
        self.codes.append(response.status_code)


def _leaves(error: BaseException) -> Iterator[BaseException]:
    if isinstance(error, BaseExceptionGroup):
        for inner in error.exceptions:
            yield from _leaves(inner)
    else:
        yield error


async def call_tool(url: str, token: str, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """One tool call with ``token``; its structured result. Raises :class:`AssistantError`."""
    status = _Status()
    headers = {"Authorization": f"Bearer {token}"}
    timeout = httpx2.Timeout(TIMEOUT_S)
    try:
        async with (
            httpx2.AsyncClient(
                headers=headers, timeout=timeout, event_hooks={"response": [status.record]}
            ) as http,
            Client(streamable_http_client(url, http_client=http)) as client,
        ):
            result = await client.call_tool(tool, arguments)
    except Exception as exc:
        if 401 in status.codes:
            raise AssistantError(
                "the Knowledge API refused the token (401): check EKB_TOKEN"
            ) from exc
        if any(isinstance(leaf, httpx2.TransportError) for leaf in _leaves(exc)):
            raise AssistantError(
                f"could not reach the Knowledge API at {url}: is `uv run ekb api` running?"
            ) from exc
        raise AssistantError(f"the call to {tool} failed: {exc}") from exc
    if result.is_error or result.structured_content is None:
        text = " ".join(getattr(block, "text", "") for block in result.content).strip()
        raise AssistantError(f"{tool} failed: {text or 'no result'}")
    return dict(result.structured_content)


def ask(url: str, token: str, question: str) -> dict[str, Any]:
    """The HR assistant's answer for the person ``token`` was issued to."""
    return asyncio.run(call_tool(url, token, "ask", {"question": question}))


def label(citation: dict[str, Any]) -> str:
    """``[1] HR-PL-01 · Annual leave policy > How much leave you get``."""
    key = str(citation.get("document_key", ""))
    page = key.partition(":")[2] or key
    title = str(citation.get("title", ""))
    section = [str(s) for s in citation.get("section", ())]
    if section and section[0].casefold() == title.casefold():
        section = section[1:]
    trail = "".join(f" > {heading}" for heading in section)
    changed = "  (the source changed since indexing)" if citation.get("source_changed") else ""
    return f"[{citation.get('number')}] {page} · {title}{trail}{changed}"


def render(answer: dict[str, Any]) -> list[str]:
    """The answer as the assistant prints it: who for, the text, the sources, the notes."""
    who = f"{answer.get('agent_id', '?')} for {answer.get('user_id', '?')}"
    cached = ", cached" if answer.get("cached") else ""
    lines = [f"HR assistant ({who}{cached}):"]
    lines.extend(f"  {line}" for line in str(answer.get("answer", "")).splitlines())
    citations: Sequence[dict[str, Any]] = answer.get("citations", ())
    if citations:
        lines.append("Sources:")
        lines.extend(f"  {label(c)}" for c in citations)
    lines.extend(f"Note: {note}" for note in answer.get("notes", ()))
    return lines
