"""Small builders for the change events the change-log tests feed in."""

from __future__ import annotations

from datetime import UTC, datetime

from ekb.contracts import Acl, ChangeEvent, ChangeOp

AT = datetime(2026, 11, 24, 9, 0, tzinfo=UTC)
ALL_STAFF = Acl(groups=frozenset({"all-staff"}))


def upsert(
    source_id: str, version: str = "1.1", source: str = "hr_docs", **extra: object
) -> ChangeEvent:
    fields: dict[str, object] = {
        "source": source,
        "source_id": source_id,
        "op": ChangeOp.UPSERT,
        "version": version,
        "occurred_at": AT,
        "acl": ALL_STAFF,
        "raw_pointer": f"mem://ekb-raw/{source}/{source_id}/{version}",
        "content_hash": f"sha256:{source_id}-{version}",
    }
    return ChangeEvent.model_validate(fields | extra)


def acl_change(
    source_id: str, version: str, acl: Acl, source: str = "hr_docs", **extra: object
) -> ChangeEvent:
    fields: dict[str, object] = {
        "source": source,
        "source_id": source_id,
        "op": ChangeOp.ACL_CHANGE,
        "version": version,
        "occurred_at": AT,
        "acl": acl,
    }
    return ChangeEvent.model_validate(fields | extra)


def delete(source_id: str, version: str, source: str = "hr_docs", **extra: object) -> ChangeEvent:
    fields: dict[str, object] = {
        "source": source,
        "source_id": source_id,
        "op": ChangeOp.DELETE,
        "version": version,
        "occurred_at": AT,
    }
    return ChangeEvent.model_validate(fields | extra)
