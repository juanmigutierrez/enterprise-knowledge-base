"""T1 for the HR record API: a record in a listing → ``ChangeEvent``.

==========================  ======  =========  ===============================
the API shows               op      ACL        person_id
==========================  ======  =========  ===============================
an employee (new, changed)  upsert  all-staff  that employee's identity user
a team (new, changed)       upsert  all-staff  none
a record no longer listed   delete  none       none
==========================  ======  =========  ===============================

- ``source_id`` is ``<collection>/<id>`` (``employees/E-1001``), so employees
  and teams never collide.
- ``version`` is the record's ``updated_at``. The API has no revision number.
  The inclusive delta can return a record twice; both events then carry the
  same version and hash, and T2 skips the repeat.
- ``all-staff``: the records hold directory fields only, no pay data (S1b).
"""

from __future__ import annotations

from ekb.connect.hr_api import HrRecord
from ekb.connect.normalize.common import ALL_STAFF, record_upsert
from ekb.contracts import ChangeEvent


def version_of(record: HrRecord) -> str:
    return record.updated_at.isoformat()


def record_event(
    source: str,
    record: HrRecord,
    *,
    raw_pointer: str,
    original: bytes,
    person_id: str | None,
    changed_by: str | None,
) -> ChangeEvent:
    return record_upsert(
        source,
        record.source_id,
        version=version_of(record),
        occurred_at=record.updated_at,
        acl=ALL_STAFF,
        raw_pointer=raw_pointer,
        original=original,
        person_id=person_id,
        changed_by=changed_by,
    )
