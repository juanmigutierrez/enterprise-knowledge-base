"""Client for the identity provider (stands in for Okta / Entra ID), SCIM-style.

Two kinds of read: the current users (``/scim/v2/Users``) and the event feed
after a cursor (``/events?since=<seq>``). The user ID (``alice``) is the
canonical person ID everywhere in the product (D3).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, StrictBool


class IdpUser(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    display_name: str
    active: StrictBool
    groups: tuple[str, ...]
    data: dict[str, Any] = Field(repr=False)

    @classmethod
    def parse(cls, data: dict[str, Any]) -> IdpUser:
        return cls(
            id=data["id"],
            display_name=data["displayName"],
            active=data["active"],
            groups=data["groups"],
            data=data,
        )


class IdpEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    seq: int
    event: str  # kept as text: an unknown kind must be reported, not rejected on arrival
    user_id: str
    group: str | None = None
    at: datetime
    actor: str | None = None


class IdentityApi:
    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def list_users(self) -> list[IdpUser]:
        response = self._client.get("/scim/v2/Users")
        response.raise_for_status()
        return [IdpUser.parse(item) for item in response.json()["Resources"]]

    def get_user(self, user_id: str, *, timeout: float | None = None) -> IdpUser | None:
        """The user now, or ``None`` if the identity provider does not know them.

        ``timeout`` (seconds) overrides the client's for this call (Q4's access check, S14).
        """
        path = f"/scim/v2/Users/{user_id}"
        if timeout is None:
            response = self._client.get(path)
        else:
            response = self._client.get(path, timeout=timeout)
        if response.status_code == httpx.codes.NOT_FOUND:
            return None
        response.raise_for_status()
        return IdpUser.parse(response.json())

    def read_events(self, since: int) -> tuple[list[IdpEvent], int]:
        """Events after ``since`` and the cursor to resume from, read together."""
        response = self._client.get("/events", params={"since": since})
        response.raise_for_status()
        body = response.json()
        return [IdpEvent.model_validate(e) for e in body["events"]], body["cursor"]
