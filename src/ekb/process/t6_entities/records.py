"""Reading a record system's records as entities: the "resolve" half of T6.

The HR record API (S1b) is where the company says who its people and teams
are. Its connector (S2a) stores each record as the API sent it, as canonical
JSON, and its change event already names the person in the identity
provider's terms (``person_id``, mapped by full name, D3). A **record reader**
turns one stored record into the entity it defines:

====================  ===============================================
the record             the entity
====================  ===============================================
``employees/E-1002``   ``person:bob``: name, work email, team
``teams/T-PPL``        ``team:T-PPL``: name
====================  ===============================================

**An employee with no identity user is no entity** (fail closed, as the
connectors do): a person's ID must be the canonical one, so a second ID made
up from ``E-1013`` would split one person in two. The record reports it, and
the connector's person map already logs the gap.

Like T3's original readers, a reader keeps only the fields it needs, so a
source adding a field breaks nothing. Which reader goes with which source is
chosen in the wiring (``ekb.app.entities``).
"""

from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel, ConfigDict, ValidationError

from ekb.contracts import ChangeEvent, Entity, EntityKind, entity_id

RecordReader = Callable[[ChangeEvent, bytes], Entity | None]
"""``(upsert event, stored original) -> Entity``, or ``None`` when the record
defines none; raises :class:`RecordError` when the record can't be read."""


class RecordError(ValueError):
    """A stored record is not what its source promised. Not skipped: the event comes back."""


class _Employee(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    id: str
    name: str
    work_email: str | None = None
    team_id: str | None = None


class _Team(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    id: str
    name: str


def hr_record(event: ChangeEvent, original: bytes) -> Entity | None:
    """An HR API record (``employees/<id>`` or ``teams/<id>``) → its person or team."""
    collection, _, record_id = event.source_id.partition("/")
    try:
        if collection == "employees":
            employee = _Employee.model_validate_json(original)
            _same_id(event, employee.id, record_id)
            if event.person_id is None:
                return None
            return Entity(
                entity_id=entity_id(EntityKind.PERSON, event.person_id),
                kind=EntityKind.PERSON,
                name=employee.name,
                aliases=(employee.work_email,) if employee.work_email else (),
                team_id=entity_id(EntityKind.TEAM, employee.team_id) if employee.team_id else None,
                source_key=event.document_key,
                source_version=event.version,
            )
        if collection == "teams":
            team = _Team.model_validate_json(original)
            _same_id(event, team.id, record_id)
            return Entity(
                entity_id=entity_id(EntityKind.TEAM, team.id),
                kind=EntityKind.TEAM,
                name=team.name,
                source_key=event.document_key,
                source_version=event.version,
            )
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first["loc"]) or "the record"
        raise RecordError(
            f"{event.document_key} v{event.version}: not an HR {collection} record "
            f"({where}: {first['msg']})"
        ) from exc
    raise RecordError(f"{event.document_key}: no entities are read from '{collection}' records")


def _same_id(event: ChangeEvent, stored: str, named: str) -> None:
    if stored != named:
        raise RecordError(f"{event.document_key}: the stored record is {stored}, not {named}")
