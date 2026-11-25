"""T8 wired behind T7 (S11), its fast lane behind T2, and the stores it writes.

Four choices are made here, in the composition root, and nowhere else:

- **Which stores and which resolver.** By name: the worker pipeline (S12)
  passes what the profile names (``source_acl``; ``opensearch`` and
  ``dynamodb`` run as their ``in_memory`` stand-ins until S17).
- **Which sources say who is in which group**, and how to read their records:
  ``MEMBERSHIP_SOURCES``. Only the identity provider: its user records.
- **When T8 runs.** After T7, on the same document; before T3, T2's
  ``acl_only`` goes straight to T8 (the fast lane) and, for an edit, readers
  first withholds old chunks on a grant. Revocations take effect before the
  models run. The worker pipeline (S12) does this itself; the step demos use
  :func:`ekb.app.chunk.chunk_after_t3` (``t8=``) and :func:`readers_after_t2`.
- **What a purge removes.** :func:`unpublisher` takes a deleted document out
  of the search store and drops its readers record; a purged identity record
  takes its membership with it. The worker pipeline also uses it to withdraw
  an item it parks in the dead letters (S12).
"""

from __future__ import annotations

from collections.abc import Callable, Collection

from ekb.app.changelog import ChangeLog
from ekb.contracts import DiffAction
from ekb.platform import registry
from ekb.process.t2_diff.step import DiffResult, Downstream
from ekb.process.t8_permissions.options.source_acl import SourceAclResolver
from ekb.process.t8_permissions.port import PermissionResolver
from ekb.process.t8_permissions.records import MembershipReader, idp_user
from ekb.process.t8_permissions.step import PermissionResult, PermissionStep
from ekb.process.t8_permissions.sync import MembershipSync
from ekb.stores.permissions.options.in_memory import InMemoryPermissionStore
from ekb.stores.permissions.port import PermissionStore
from ekb.stores.search.options.in_memory import InMemorySearchStore
from ekb.stores.search.port import SearchStore

RESOLVERS = (SourceAclResolver,)  # imported, so each is registered
SEARCH_STORES = (InMemorySearchStore,)
PERMISSION_STORES = (InMemoryPermissionStore,)
DEFAULT_RESOLVER = "source_acl"
DEFAULT_STORE = "in_memory"

MEMBERSHIP_SOURCES: dict[str, MembershipReader] = {"identity": idp_user}
"""Sources whose records say who is in which group, each with the reader for its records."""


def search_store(
    name: str = DEFAULT_STORE, *, current_lifetime: Callable[[str], int] | None = None
) -> SearchStore:
    """A registered search store option by name (``in_memory``)."""
    built = registry.create("search", name, current_lifetime=current_lifetime)
    if not isinstance(built, SearchStore):
        raise TypeError(f"search option {name!r} does not implement the SearchStore port")
    return built


def permission_store(
    name: str = DEFAULT_STORE, *, current_lifetime: Callable[[str], int] | None = None
) -> PermissionStore:
    """A registered permission store option by name (``in_memory``)."""
    built = registry.create("permissions", name, current_lifetime=current_lifetime)
    if not isinstance(built, PermissionStore):
        raise TypeError(f"permissions option {name!r} does not implement the PermissionStore port")
    return built


def resolver(name: str = DEFAULT_RESOLVER) -> PermissionResolver:
    """A registered T8 option by name (``source_acl``)."""
    built = registry.create("t8_permissions", name)
    if not isinstance(built, PermissionResolver):
        raise TypeError(
            f"t8_permissions option {name!r} does not implement the PermissionResolver port"
        )
    return built


def permission_step(
    permissions: PermissionStore, index: SearchStore, name: str = DEFAULT_RESOLVER
) -> PermissionStep:
    """T8 reading memberships from ``permissions`` and publishing to ``index``."""
    return PermissionStep(resolver(name), permissions, index)


def membership_sync(changelog: ChangeLog, store: PermissionStore) -> MembershipSync:
    """The permission store's memberships, fed from the change log's stored identity records."""
    return MembershipSync(store, changelog.rawstore, MEMBERSHIP_SOURCES)


def readers_after_t2(
    t8: PermissionStep,
    downstream: Downstream,
    documents: Collection[str],
    on_readers: Callable[[PermissionResult], object] | None = None,
) -> Downstream:
    """T2's ``downstream`` with T8's fast lane in front, for the sources in ``documents``.

    ``acl_only`` → T8 puts the new readers on the published chunks; nothing else
    runs (a vector comes from the text, not from who may read it).
    ``process`` → withdraw old chunks on a grant, otherwise narrow their readers.
    Then run ``downstream`` (T3 … T8): if the edit also removed a reader, the old chunks
    lose them now, even if a model is down and the new text has to wait.
    Records (identity users, HR records) have no published chunks and go
    straight on.

    ``on_readers`` sees each fast-lane result (the demo and the inspector show them).
    If T8 raises, T2 records nothing and the event comes back (S4).
    """

    def handle(result: DiffResult) -> None:
        decision, applied = result.decision, None
        if decision.event.source in documents:
            if decision.action is DiffAction.ACL_ONLY:
                applied = t8.apply_readers(decision.event)
            elif decision.action is DiffAction.PROCESS:
                applied = t8.readers_first(decision.event)  # None for a new document
        if applied is not None and on_readers is not None:
            on_readers(applied)
        if decision.action is not DiffAction.ACL_ONLY or decision.event.source not in documents:
            downstream(result)

    return handle


def unpublisher(
    index: SearchStore, permissions: PermissionStore, memberships: MembershipSync | None = None
) -> Callable[[str], int]:
    """A purge's drop for T8's stores: the document out of search, its readers record
    gone, and the membership a purged identity record defined. Returns how many
    chunks left the search store."""

    def drop(document_key: str) -> int:
        removed = index.remove(document_key)  # first: the deletion gate
        permissions.forget_readers(document_key)
        if memberships is not None:
            memberships.forget(document_key)
        return removed

    return drop
