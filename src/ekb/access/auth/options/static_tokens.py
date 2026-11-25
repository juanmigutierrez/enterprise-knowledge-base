"""``auth: static_tokens``: random test tokens for a few test users (S15).

The POC has no token issuer yet (Keycloak and OIDC are S17), so the Knowledge
API issues its own test tokens: one per test user named in ``config/``, all for
one agent (``hr-assistant``). Each is 32 random bytes, prefixed ``ekb_test_`` so
anyone who finds one knows what it is.

- **Kept out of git.** They are written to a git-ignored file
  (``.cache/ekb/test-tokens.json``) on first start, and read back on later
  starts, so an MCP client configured once keeps working. A user added in
  ``config/`` gets a token the next time; a user removed loses theirs. No token
  is in the code, the configuration or the tests (AGENTS.md).
- **Compared by hash.** The check keeps only SHA-256 hashes of the tokens and
  looks the hash up, so a lookup takes the same time whatever the token.
- **Not a production design.** These tokens never expire and are not bound to
  this server (no audience). Real tokens come from the identity provider, act on
  behalf of the signed-in person (D9) and expire.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import subprocess  # nosec B404: Windows file permissions via system icacls
import tempfile
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from filelock import FileLock

from ekb.contracts import Caller
from ekb.platform.registry import register

VERSION = 1
PREFIX = "ekb_test_"
AGENT = "hr-assistant"
USERS = ("alice", "bob", "carla", "emma", "olivia")
"""Two Milan employees (bob in HR and compensation), one in Madrid and two in Toronto
(the demos make olivia leave): enough to see permissions differ."""
PATH = ".cache/ekb/test-tokens.json"


@dataclass(frozen=True)
class IssuedToken:
    """One test token and whom it acts for. Only the API's startup message shows ``token``."""

    user_id: str
    agent_id: str
    token: str = field(repr=False)

    @property
    def caller(self) -> Caller:
        return Caller(
            user_id=self.user_id,
            agent_id=self.agent_id,
            token_id=f"static:{self.user_id}@{self.agent_id}",
        )


def new_token() -> str:
    """32 random bytes, URL-safe, with the test prefix."""
    return PREFIX + secrets.token_urlsafe(32)


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def load_or_issue(path: Path, users: Sequence[str], agent: str) -> tuple[IssuedToken, ...]:
    """The tokens kept in ``path`` for ``users`` and ``agent``; new ones for anyone missing.

    The file is rewritten only when something changed. A file that cannot be read as
    tokens is replaced: test tokens carry nothing worth keeping.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(path) + ".lock"):
        return _load_locked(path, users, agent)


def _load_locked(path: Path, users: Sequence[str], agent: str) -> tuple[IssuedToken, ...]:
    kept, private_format = _read(path, agent)
    issued = tuple(
        IssuedToken(user, agent, kept[user])
        if user in kept
        else IssuedToken(user, agent, new_token())
        for user in dict.fromkeys(users)
    )
    if not private_format or {t.user_id: t.token for t in issued} != kept:
        _write(path, agent, issued)
    return issued


def _read(path: Path, agent: str) -> tuple[dict[str, str], bool]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}, False
    if not isinstance(data, dict) or data.get("agent") != agent:
        return {}, False
    tokens = data.get("tokens")
    if not isinstance(tokens, dict):
        return {}, False
    counts = Counter(token for token in tokens.values() if isinstance(token, str))
    valid = {
        str(user): token
        for user, token in tokens.items()
        if isinstance(token, str)
        and re.fullmatch(PREFIX + r"[A-Za-z0-9_-]{43}", token)
        and counts[token] == 1
    }
    return valid, data.get("format") == 1 and len(valid) == len(tokens)


def _write(path: Path, agent: str, issued: Iterable[IssuedToken]) -> None:
    body = {
        "format": 1,
        "note": "Test tokens for the local Knowledge API (S15). Synthetic users only; "
        "delete this file to issue new ones.",
        "agent": agent,
        "tokens": {t.user_id: t.token for t in issued},
    }
    # mkstemp starts private on POSIX. Set a protected owner-only DACL on
    # Windows while the unique file is still empty, before writing credentials.
    fd, name = tempfile.mkstemp(dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            if os.name == "nt":
                icacls = Path(os.environ["SYSTEMROOT"]) / "System32" / "icacls.exe"
                subprocess.run(  # noqa: S603  # nosec B603: fixed system utility
                    [str(icacls), name, "/inheritance:r", "/grant:r", "*S-1-3-4:(F)"],
                    check=True,
                    capture_output=True,
                )
            json.dump(body, output, indent=2)
            output.write("\n")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


@register("auth", "static_tokens")
class StaticTokens:
    """Test tokens: from ``issued`` when given (the tests), else from the file at ``path``."""

    def __init__(
        self,
        path: str | Path = PATH,
        *,
        users: Sequence[str] = USERS,
        agent: str = AGENT,
        issued: Iterable[IssuedToken] | None = None,
    ) -> None:
        tokens = tuple(issued) if issued is not None else load_or_issue(Path(path), users, agent)
        self.issued: tuple[IssuedToken, ...] = tokens
        """The tokens, for the API's startup message and the demo. Never logged."""
        self._callers: Mapping[str, Caller] = {_digest(t.token): t.caller for t in tokens}
        self._agents = tuple(dict.fromkeys(t.agent_id for t in tokens))

    @property
    def auth_id(self) -> str:
        agents = ", ".join(self._agents) or "no agent"
        count = len(self._callers)
        return f"static_tokens@{VERSION}({agents}, {count} user{'s' if count != 1 else ''})"

    def verify(self, token: str) -> Caller | None:
        if not token.startswith(PREFIX):
            return None
        return self._callers.get(_digest(token))
