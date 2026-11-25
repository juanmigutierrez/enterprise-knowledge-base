"""The Knowledge API as MCP tools (S15, D9): what an agent may call, and as whom.

An agent (Claude Code, MCP Inspector, ``agents/hr_assistant``) connects over
Streamable HTTP with ``Authorization: Bearer <token>``::

    agent ── MCP ──▶ bearer check (TokenCheck) ──▶ tool ──▶ KnowledgeService
                       no token / unknown → 401        whoami · search · ask
                                                         as the token's Caller

- **Identity from the token only.** The MCP SDK's bearer middleware calls our
  :class:`~ekb.access.auth.port.TokenCheck` (wrapped in :class:`_Verifier`) and
  refuses a missing or unknown token with 401 before any tool runs. A tool reads
  the :class:`~ekb.contracts.Caller` from the request's verified token, never from
  its arguments: no tool has a ``user`` parameter, and an argument the schema does
  not list is dropped.
- **Three tools, all read-only.** ``whoami`` (who the token acts for), ``search``
  (Q1 to Q4: cited passages, S14's :class:`~ekb.contracts.Answer`), and ``ask``
  (the HR assistant: a short answer written from those passages, cached and
  re-checked). Each returns a Pydantic model, so the client gets typed,
  structured output and its schema.
- **Nothing held back leaks through an error.** A failure inside the service is
  reported as ``Error executing tool <name>``, with the details in the server's
  log only.

This module knows nothing of stores or options: :class:`KnowledgeService` is
implemented and wired in ``ekb.app.assistant``.
"""

from __future__ import annotations

from typing import Annotated, Protocol, runtime_checkable

from mcp.server import MCPServer
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field
from starlette.applications import Starlette

from ekb.access.auth.port import TokenCheck
from ekb.contracts import Answer, AssistantAnswer, Caller, Identity

NAME = "ekb"
HOST = "127.0.0.1"
PATH = "/mcp"
MAX_QUESTION = 1000
"""Characters. A question is a sentence or two; a longer one is refused by the schema."""

INSTRUCTIONS = """\
The company's HR knowledge base (synthetic data). Every call acts for the person your \
token was issued to, and sees only what they may read now: the source is asked before \
anything is released, and personal data such as IBANs is removed.
- ask: the HR assistant's short answer, with numbered citations. Show the citations.
- search: the cited passages themselves, to write your own answer from. Cite them as [n].
- whoami: who your token acts for.
An empty answer means nothing the person may read covers the question: say so, and do not \
guess. A note that passages could not be checked means the answer may be incomplete."""

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False, idempotent_hint=True)

Question = Annotated[
    str,
    Field(
        min_length=1,
        max_length=MAX_QUESTION,
        description="The person's question, in their own words and language.",
    ),
]


@runtime_checkable
class KnowledgeService(Protocol):
    """What the tools call, always with the caller the token named."""

    def whoami(self, caller: Caller) -> Identity: ...

    def search(self, caller: Caller, question: str) -> Answer: ...

    def ask(self, caller: Caller, question: str) -> AssistantAnswer: ...


class _Verifier:
    """The SDK's token verifier, backed by our :class:`TokenCheck`."""

    def __init__(self, tokens: TokenCheck) -> None:
        self._tokens = tokens

    async def verify_token(self, token: str) -> AccessToken | None:
        caller = self._tokens.verify(token)
        if caller is None:
            return None
        return AccessToken(
            token=token,
            client_id=caller.agent_id,
            subject=caller.user_id,
            scopes=[],
            claims={"token_id": caller.token_id},
        )


def current_caller() -> Caller:
    """The caller of the request being served, from its verified token.

    Raises :class:`ToolError` when there is none: a server reached without the bearer
    middleware (in-process, in tests) answers no one.
    """
    token = get_access_token()
    if token is None or not token.subject:
        raise ToolError("not signed in: send Authorization: Bearer <token>")
    token_id = str((token.claims or {}).get("token_id", ""))
    return Caller(user_id=token.subject, agent_id=token.client_id, token_id=token_id)


def create_server(service: KnowledgeService, tokens: TokenCheck, *, base_url: str) -> MCPServer:
    """The MCP server: three read-only tools, each run as the token's caller.

    ``base_url`` (e.g. ``http://127.0.0.1:8080``) is where the server is reached; the
    SDK's auth settings need it, and no authorization server is advertised (the test
    tokens are issued by the API itself).
    """
    server = MCPServer(
        NAME,
        title="Enterprise knowledge base: HR",
        instructions=INSTRUCTIONS,
        token_verifier=_Verifier(tokens),
        auth=AuthSettings(issuer_url=base_url, resource_server_url=None),
        log_level="WARNING",
    )

    @server.tool(title="Who am I acting for", annotations=READ_ONLY)
    def whoami() -> Identity:
        """Who this connection acts for: the person, the agent, and the groups the person
        reads with now. It takes no arguments: the token decides, never a tool argument."""
        return service.whoami(current_caller())

    @server.tool(title="Search the HR documents", annotations=READ_ONLY)
    def search(question: Question) -> Answer:
        """Find passages of the HR documents the signed-in person may read now, each checked
        against its source and with personal data removed. Returns numbered passages with
        citations: write your own answer from them only, and cite them as [n]."""
        return service.search(current_caller(), question)

    @server.tool(title="Ask the HR assistant", annotations=READ_ONLY)
    def ask(question: Question) -> AssistantAnswer:
        """Ask the HR assistant: a short answer written only from passages of the HR
        documents the signed-in person may read now, with numbered citations [n]."""
        return service.ask(current_caller(), question)

    return server


def create_app(server: MCPServer, *, host: str = HOST, path: str = PATH) -> Starlette:
    """The server over Streamable HTTP at ``path``, for a local host only.

    The SDK checks the ``Host`` header against ``host`` (DNS-rebinding protection), so
    a page in a browser cannot reach the API through a name that resolves to it.
    """
    return server.streamable_http_app(streamable_http_path=path, host=host)
