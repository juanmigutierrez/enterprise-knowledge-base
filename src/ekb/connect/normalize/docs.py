"""T1 for the docs tool: its page vocabulary → ``ChangeEvent``.

The whole translation table:

======================== ============ ===== ============ ============
docs tool says           ``op``       ACL   raw_pointer  content_hash
======================== ============ ===== ============ ============
page.created / .updated  upsert       now   set          now
page.permissions_changed acl_change   new   none         now
page.deleted             delete       none  none         none
======================== ============ ===== ============ ============

Beyond the rules in ``common``: **the event describes the source as it is
now**, not as the notification said. A webhook can arrive after later edits;
the fetched page wins. Pages are about no one in particular, so ``person_id``
stays empty; ``changed_by`` is resolved by the connector.

``version`` is ``"<rev>.<acl_rev>"``: an opaque label downstream, but it lets
the full listing tell a content change from a permissions-only change.
"""

from __future__ import annotations

from ekb.connect.docs_api import DocsAcl, DocsPage, DocsPageSummary
from ekb.connect.normalize.common import UnknownChangeKindError, delete_event
from ekb.contracts import Acl, ChangeEvent, ChangeOp

__all__ = [
    "UnknownChangeKindError",
    "acl_change_event",
    "acl_of",
    "delete_event",
    "op_for",
    "parse_version",
    "upsert_event",
    "version_of",
]

CREATED = "page.created"
UPDATED = "page.updated"
PERMISSIONS_CHANGED = "page.permissions_changed"
DELETED = "page.deleted"

_OPS = {
    CREATED: ChangeOp.UPSERT,
    UPDATED: ChangeOp.UPSERT,
    PERMISSIONS_CHANGED: ChangeOp.ACL_CHANGE,
    DELETED: ChangeOp.DELETE,
}


def op_for(kind: str) -> ChangeOp:
    try:
        return _OPS[kind]
    except KeyError:
        raise UnknownChangeKindError(f"unknown docs change kind {kind!r}") from None


def version_of(rev: int, acl_rev: int) -> str:
    return f"{rev}.{acl_rev}"


def parse_version(version: str) -> tuple[int, int] | None:
    """``(rev, acl_rev)`` from a version label, or ``None`` if it isn't one of ours."""
    rev, dot, acl_rev = version.partition(".")
    if not dot or not rev.isdigit() or not acl_rev.isdigit():
        return None
    return int(rev), int(acl_rev)


def acl_of(acl: DocsAcl, users: frozenset[str] | None = None) -> Acl:
    """The page's readers. ``users`` replaces the user list once the connector has
    checked each name against the identity provider (unknown names are dropped)."""
    return Acl(users=frozenset(acl.users) if users is None else users, groups=frozenset(acl.groups))


def upsert_event(
    source: str,
    page: DocsPage,
    *,
    raw_pointer: str,
    acl: Acl | None = None,
    changed_by: str | None = None,
) -> ChangeEvent:
    return ChangeEvent(
        source=source,
        source_id=page.id,
        op=ChangeOp.UPSERT,
        version=version_of(page.rev, page.acl_rev),
        occurred_at=page.updated_at,
        acl=acl_of(page.acl) if acl is None else acl,
        raw_pointer=raw_pointer,
        content_hash=page.content_hash,
        changed_by=changed_by,
    )


def acl_change_event(
    source: str,
    page: DocsPageSummary,
    *,
    acl: Acl | None = None,
    changed_by: str | None = None,
) -> ChangeEvent:
    """Permissions only. No pointer: the fast lane must not need the content."""
    return ChangeEvent(
        source=source,
        source_id=page.id,
        op=ChangeOp.ACL_CHANGE,
        version=version_of(page.rev, page.acl_rev),
        occurred_at=page.updated_at,
        acl=acl_of(page.acl) if acl is None else acl,
        content_hash=page.content_hash,
        changed_by=changed_by,
    )
