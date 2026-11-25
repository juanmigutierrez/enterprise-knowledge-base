"""Port for the Knowledge API's token check. Every option implements :class:`TokenCheck`.

A token check answers one question: **who does this token act for?** One person
(the identity provider's user ID) and one agent, as a
:class:`~ekb.contracts.Caller`. Everything downstream (Q2's scope, Q4's source
check, the answer cache) runs as that person, so the token is the only place
identity comes from: no tool takes a user ID.

**Default deny.** An unknown, malformed or empty token is ``None``, never an
error and never a guest: the MCP layer refuses the call with 401. Whether the
person may read anything is not decided here: a valid token for someone who has
left still reaches Q2, which says they read nothing (S11).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ekb.contracts import Caller


@runtime_checkable
class TokenCheck(Protocol):
    """Turns a bearer token into the caller it was issued to, or ``None``."""

    @property
    def auth_id(self) -> str:
        """The option and its version, e.g. ``static_tokens@1(hr-assistant, 5 users)``."""
        ...

    def verify(self, token: str) -> Caller | None: ...
