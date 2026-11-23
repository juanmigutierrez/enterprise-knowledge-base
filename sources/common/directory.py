"""The directory: test users and groups, shared by the docs mock and the identity provider.

The identity provider (S1c) serves it; the docs mock (S1) uses it to refuse
permissions that name an unknown user or group, as a real docs tool only
accepts people its identity provider knows. Both load the same file at start,
so a user created in the identity provider at runtime is unknown to the docs
mock (a documented limit of the mocks).
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from sources.common.seed import DATA_DIR, read_yaml

DIRECTORY_FILE = DATA_DIR / "directory.yaml"


class UnknownPrincipalError(ValueError):
    """A user or group name that is not in the directory."""


class User(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)  # full name; links the user to their HR record (1-1)
    location: str
    groups: tuple[str, ...]
    active: bool = True


class Directory(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    groups: dict[str, str]  # group name -> description
    users: dict[str, User]

    @classmethod
    def load(cls, path: Path = DIRECTORY_FILE) -> Directory:
        directory = cls.model_validate(read_yaml(path))
        for user_id, user in directory.users.items():
            unknown = set(user.groups) - set(directory.groups)
            if unknown:
                raise UnknownPrincipalError(f"user {user_id!r} is in unknown groups {unknown}")
        names = [u.name for u in directory.users.values()]
        duplicates = {n for n in names if names.count(n) > 1}
        if duplicates:
            raise ValueError(f"full names must be unique (they link to HR records): {duplicates}")
        return directory

    def check(self, *, users: Iterable[str] = (), groups: Iterable[str] = ()) -> None:
        """Raise if any of ``users`` or ``groups`` is unknown."""
        unknown_users = set(users) - set(self.users)
        unknown_groups = set(groups) - set(self.groups)
        if unknown_users or unknown_groups:
            raise UnknownPrincipalError(
                f"unknown users {sorted(unknown_users)}, unknown groups {sorted(unknown_groups)}"
            )
