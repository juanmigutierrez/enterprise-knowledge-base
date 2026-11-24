"""Identity sync: users and group changes from the identity provider.

A group change is a permission change for every page that group can read,
without any page changing (S1c). So the identity provider is a source like the
others: each user is one tracked item (``identity:users/alice``), and every
joiner, leaver or membership change is an upsert of that user's record.

The first sync follows the pattern from S1c, so nothing is lost between the
listing and the feed:

1. read the feed cursor;
2. list every user and emit one upsert each (version = that cursor);
3. from then on, replay the feed after the cursor. A change made during step 2
   shows up both in the listing and the feed; the repeat is harmless.

For each feed event the connector fetches the user **as they are now**, like
the docs webhook connector fetches the page.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from ekb.connect.identity_api import IdentityApi, IdpUser
from ekb.connect.normalize import identity as normalize
from ekb.connect.normalize.common import canonical_json
from ekb.connect.people import PersonMap
from ekb.connect.port import EventSink, OriginalStore
from ekb.contracts import ChangeEvent
from ekb.platform.registry import register


def _now() -> datetime:
    return datetime.now(UTC)


class UserVanishedError(LookupError):
    """The feed names a user the provider no longer has. Users are never deleted here,
    so this means the source broke its own rules: stop rather than guess."""


@register("connect", "identity_sync")
class IdentitySyncConnector:
    def __init__(
        self,
        api: IdentityApi,
        sink: EventSink,
        store_original: OriginalStore,
        people: PersonMap,
        source: str = "identity",
        clock: Callable[[], datetime] = _now,
    ) -> None:
        self._api = api
        self._sink = sink
        self._store_original = store_original
        self._people = people
        self._source = source
        self._clock = clock
        self._cursor: int | None = None

    @property
    def source(self) -> str:
        return self._source

    @property
    def cursor(self) -> int | None:
        return self._cursor

    def sync(self) -> int:
        emitted = 0
        if self._cursor is None:
            _, cursor = self._api.read_events(0)
            listed_at = self._clock()  # SCIM users here carry no timestamp of their own
            for user in self._api.list_users():
                self._sink(self._event(user, version=str(cursor), at=listed_at, changed_by=None))
                emitted += 1
            self._cursor = cursor
        events, _ = self._api.read_events(self._cursor)
        for item in events:
            normalize.check_kind(item.event)
            current = self._api.get_user(item.user_id)
            if current is None:
                raise UserVanishedError(f"user {item.user_id} named at seq {item.seq} is gone")
            self._sink(
                self._event(
                    current,
                    version=str(item.seq),
                    at=item.at,
                    changed_by=self._people.user(item.actor),
                )
            )
            self._cursor = item.seq
            emitted += 1
        return emitted

    def _event(
        self, user: IdpUser, *, version: str, at: datetime, changed_by: str | None
    ) -> ChangeEvent:
        original = canonical_json(user.data)
        pointer = self._store_original(
            f"{self._source}:{normalize.source_id_of(user.id)}", version, original
        )
        return normalize.user_event(
            self._source,
            user,
            version=version,
            occurred_at=at,
            raw_pointer=pointer,
            original=original,
            changed_by=changed_by,
        )
