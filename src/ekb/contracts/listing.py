"""A listing must refetch missing content while retaining the version for a delete."""

from pydantic import BaseModel, ConfigDict


class NeedsOriginal(BaseModel):
    """Last observed source version of a live item whose original needs fetching."""

    model_config = ConfigDict(frozen=True)

    version: str


type ListingVersion = str | NeedsOriginal
