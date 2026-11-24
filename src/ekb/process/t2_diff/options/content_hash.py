"""T2 by content hash: compare what a reader would see, not version labels.

Versions are opaque and differ per source (``2.1``, an ISO timestamp, a log
sequence number), and a source can raise the version without changing a word
(the docs tool does, on every save). The content hash says whether the text
changed; the ACL says whether the readers did. So:

==============  ===================================  ==========  ===================
event           registry before                      action      why
==============  ===================================  ==========  ===================
delete          anything                             purge       never skipped
upsert          none, or a tombstone                 process     new, or back
upsert          live, but no original processed      process     content never seen
upsert          live, other or no hash               process     content changed
upsert          live, same hash and person, new readers       acl_only    readers changed
upsert          live, same hash,         process     now about someone
                other person                                     else (T6, T8)
upsert          live, all the same                   skip        repeat / same text
acl_change      a tombstone                          skip        stays deleted
acl_change      same readers as last processed       skip        repeat
acl_change      anything else                        acl_only    fast lane
==============  ===================================  ==========  ===================

**A revocation is never skipped**: an ACL change is skipped only when the
readers are exactly those last processed, or the item is deleted. An ACL change
whose content hash also differs still goes to the fast lane, so the new readers
apply now; the registry then marks the item as needing its original (S3), and
the next full listing fetches the new content as an upsert.
"""

from __future__ import annotations

from ekb.changelog.registry.port import DocumentRecord
from ekb.contracts import Acl, ChangeEvent, ChangeOp, DiffAction, DiffDecision
from ekb.platform.registry import register


def _short(content_hash: str | None) -> str:
    return content_hash.removeprefix("sha256:")[:8] if content_hash else "none"


def readers_diff(before: Acl, after: Acl) -> str:
    """``+es-employees -hr-team``: who gained and who lost access (users as ``user:``)."""

    def names(acl: Acl) -> set[str]:
        return set(acl.groups) | {f"user:{u}" for u in acl.users}

    old, new = names(before), names(after)
    changes = [f"+{n}" for n in sorted(new - old)] + [f"-{n}" for n in sorted(old - new)]
    return " ".join(changes) or "no change"


@register("t2_diff", "content_hash")
class ContentHashDiffer:
    def decide(self, previous: DocumentRecord | None, event: ChangeEvent) -> DiffDecision:
        if event.op is ChangeOp.DELETE:
            action, reason = DiffAction.PURGE, self._delete_reason(previous)
        elif event.op is ChangeOp.ACL_CHANGE:
            action, reason = self._acl_change(previous, event)
        else:
            action, reason = self._upsert(previous, event)
        return DiffDecision(
            action=action,
            reason=reason,
            event=event,
            previous_version=previous.version if previous else None,
        )

    @staticmethod
    def _delete_reason(previous: DocumentRecord | None) -> str:
        if previous is None:
            return "deleted before it was ever processed: removed everywhere anyway"
        if previous.deleted:
            return "deleted again: removed everywhere again (harmless, never skipped)"
        return "deleted in the source: remove it from every store"

    @staticmethod
    def _upsert(previous: DocumentRecord | None, event: ChangeEvent) -> tuple[DiffAction, str]:
        if previous is None:
            return DiffAction.PROCESS, "new: never processed"
        if previous.deleted:
            return DiffAction.PROCESS, f"back after a delete (v{previous.version})"
        if previous.raw_pointer is None:
            return DiffAction.PROCESS, "its content was never processed, only its readers"
        if event.content_hash is None or event.content_hash != previous.content_hash:
            return DiffAction.PROCESS, (
                f"content changed: hash {_short(previous.content_hash)} -> "
                f"{_short(event.content_hash)}"
            )
        if event.person_id != previous.person_id:
            return DiffAction.PROCESS, (
                f"same content, now about {event.person_id or 'no one'} "
                f"(was {previous.person_id or 'no one'})"
            )
        if event.acl != previous.acl:
            return DiffAction.ACL_ONLY, (
                f"same content, readers changed ({readers_diff(previous.acl, event.acl)})"
            )
        if event.version == previous.version:
            return DiffAction.SKIP, f"repeat: v{event.version} was already processed"
        return DiffAction.SKIP, (f"new version, same content and readers as v{previous.version}")

    @staticmethod
    def _acl_change(previous: DocumentRecord | None, event: ChangeEvent) -> tuple[DiffAction, str]:
        if previous is not None and previous.deleted:
            return DiffAction.SKIP, "deleted: new readers don't bring it back"
        content_note = ""
        if (
            previous is not None
            and event.content_hash is not None
            and event.content_hash != previous.content_hash
        ):
            content_note = "; the content changed too, the full listing fetches it"
        if previous is None:
            return DiffAction.ACL_ONLY, (
                "first seen as a permissions change: readers applied now, "
                "the full listing fetches its content"
            )
        if event.acl == previous.acl:
            return DiffAction.SKIP, f"readers unchanged{content_note}"
        return DiffAction.ACL_ONLY, (
            f"readers changed ({readers_diff(previous.acl, event.acl)}){content_note}"
        )
