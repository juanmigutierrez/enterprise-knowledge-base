"""Client for the HR record API (stands in for Workday), as the connector sees it.

The API has two collections, ``employees`` and ``teams``. Each listing returns
``as_of``, the server time to pass back as ``updated_since`` next time.

Records are kept as the API sent them (``data``), because that is the original
the raw store keeps; only the fields the connector needs are read out.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict

Collection = Literal["employees", "teams"]
COLLECTIONS: tuple[Collection, ...] = ("employees", "teams")


class HrRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    collection: Collection
    id: str
    name: str
    updated_at: datetime
    updated_by: str | None
    data: dict[str, Any]

    @property
    def source_id(self) -> str:
        return f"{self.collection}/{self.id}"

    @classmethod
    def parse(cls, collection: Collection, data: dict[str, Any]) -> HrRecord:
        return cls(
            collection=collection,
            id=data["id"],
            name=data["name"],
            updated_at=data["updated_at"],
            updated_by=data.get("updated_by"),
            data=data,
        )


class HrListing(BaseModel):
    model_config = ConfigDict(frozen=True)

    records: list[HrRecord]
    as_of: datetime


class HrApi:
    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def list(self, collection: Collection, updated_since: datetime | None = None) -> HrListing:
        """Every record, or (with ``updated_since``) those saved at or after that time."""
        params = {"updated_since": updated_since.isoformat()} if updated_since else None
        response = self._client.get(f"/{collection}", params=params)
        response.raise_for_status()
        body = response.json()
        return HrListing(
            records=[HrRecord.parse(collection, item) for item in body["records"]],
            as_of=body["as_of"],
        )
