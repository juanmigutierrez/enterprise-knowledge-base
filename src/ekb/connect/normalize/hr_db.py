"""T1 for the HR database: a change-log entry → ``ChangeEvent``.

==========  ======  =============================================================
CDC ``op``  op      notes
==========  ======  =============================================================
``r``       upsert  the snapshot read when capture starts
``c``/``u`` upsert  the row as it is after the change
``d``       delete  carries nothing; who deleted is unknown (the row is gone)
==========  ======  =============================================================

Each table has a rule for who may read its rows and whom a row is about:

==================  ===================================  ==========================
table               ACL                                  person_id
==================  ===================================  ==========================
office_headcount    all-staff                            none
leave_balances      that employee + hr-team (S1d)        that employee's identity user
==================  ===================================  ==========================

If a leave row's employee can't be mapped to an identity user, the row is
readable by ``hr-team`` only: fail closed, and the person map reports it.

``source_id`` is ``<table>/<key values>`` (``leave_balances/E-1001/2026``) and
``version`` is the log ``seq``: commit order, the strongest order any source
gives.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ekb.connect.hr_db import CdcEntry
from ekb.connect.normalize.common import (
    ALL_STAFF,
    UnknownChangeKindError,
    canonical_json,
    delete_event,
    record_upsert,
)
from ekb.contracts import Acl, ChangeEvent

UPSERT_OPS = frozenset({"r", "c", "u"})
DELETE_OP = "d"
HR_TEAM = "hr-team"


@dataclass(frozen=True)
class TableRule:
    person_column: str | None
    acl: Callable[[str | None], Acl]


def _leave_acl(person: str | None) -> Acl:
    users = frozenset({person}) if person else frozenset()
    return Acl(users=users, groups=frozenset({HR_TEAM}))


TABLES: dict[str, TableRule] = {
    "office_headcount": TableRule(person_column=None, acl=lambda _person: ALL_STAFF),
    "leave_balances": TableRule(person_column="employee_id", acl=_leave_acl),
}


def rule_for(table: str) -> TableRule:
    try:
        return TABLES[table]
    except KeyError:
        raise UnknownChangeKindError(f"no rule for table {table!r}") from None


def source_id_of(entry: CdcEntry) -> str:
    return "/".join([entry.table, *(str(value) for value in entry.key.values())])


def person_key(entry: CdcEntry) -> str | None:
    """The source's own ID of the person the row is about (``E-1001``), if any."""
    column = rule_for(entry.table).person_column
    row = entry.after or entry.before or {}
    value: Any = row.get(column) if column else None
    return str(value) if value is not None else None


def event_for(
    source: str,
    entry: CdcEntry,
    *,
    store_original: Callable[[str, str, bytes], str],
    person_id: str | None,
    changed_by: str | None,
) -> ChangeEvent:
    rule = rule_for(entry.table)
    source_id = source_id_of(entry)
    version = str(entry.seq)
    if entry.op == DELETE_OP:
        return delete_event(source, source_id, version=version, occurred_at=entry.logged_at)
    if entry.op not in UPSERT_OPS or entry.after is None:
        raise UnknownChangeKindError(f"unknown CDC op {entry.op!r} at seq {entry.seq}")
    original = canonical_json(entry.after)
    return record_upsert(
        source,
        source_id,
        version=version,
        occurred_at=entry.logged_at,
        acl=rule.acl(person_id),
        raw_pointer=store_original(f"{source}:{source_id}", version, original),
        original=original,
        person_id=person_id,
        changed_by=changed_by,
    )
