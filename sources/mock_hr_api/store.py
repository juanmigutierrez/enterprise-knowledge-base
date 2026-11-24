"""State of the mock HR API: employees and teams, with no change feed at all.

Rules that the connector (S2a) relies on:

- Every save stamps ``updated_at`` with the current time, even if nothing
  changed (as in S1: the source never judges; T2 does).
- ``updated_since`` is **inclusive**: a record stamped exactly at that instant
  is returned. A reader that passes back the ``as_of`` of its previous call may
  see one record twice but can never miss one. T2 drops the duplicate.
- A delete leaves **no trace**: no tombstone, no event, nothing in any delta.
  Only comparing two full listings shows that a record is gone.
- A save may name who made it (``actor``, an identity provider user ID); it is
  kept as ``updated_by``. A delete's actor is accepted and, like the record,
  forgotten.
- Referential rules a real HR system enforces: an employee's team and manager
  must exist, names are unique (they link to the identity provider 1-1), and
  someone who still leads a team or manages people can't be deleted.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from pathlib import Path

from sources.common.clock import Clock, utc_now
from sources.common.seed import DATA_DIR, read_yaml
from sources.mock_hr_api.models import (
    Employee,
    EmployeeCreate,
    EmployeeUpdate,
    RecordList,
    Team,
    TeamUpdate,
)

SEED_FILE = DATA_DIR / "hr_api.yaml"


class RecordNotFoundError(KeyError):
    """No record with this ID (never existed, or deleted)."""


class RecordConflictError(ValueError):
    """The change clashes with existing records (duplicate ID or name, record still in use)."""


class InvalidReferenceError(ValueError):
    """The record points at a team or manager that does not exist."""


def _as_utc(moment: datetime) -> datetime:
    """Treat a time without a time zone as UTC, so comparisons never fail."""
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


class HrStore:
    """In-memory employees and teams, safe to use from several threads."""

    def __init__(self, *, clock: Clock = utc_now) -> None:
        self._clock = clock
        self._employees: dict[str, Employee] = {}
        self._teams: dict[str, Team] = {}
        self._lock = threading.Lock()

    @classmethod
    def from_seed(cls, path: Path = SEED_FILE, *, clock: Clock = utc_now) -> HrStore:
        """Load teams first (employees point at them), then employees.

        Team leads and managers are checked once everything is loaded, because
        a seed file may list a team before the employee who leads it.
        """
        data = read_yaml(path)
        store = cls(clock=clock)
        now = clock()
        for raw in data.get("teams", []):
            team = Team(**raw, updated_at=now)
            if team.id in store._teams:
                raise RecordConflictError(f"duplicate team {team.id}")
            store._teams[team.id] = team
        for raw in data.get("employees", []):
            employee = Employee(**EmployeeCreate(**raw).model_dump(), updated_at=now)
            if employee.id in store._employees:
                raise RecordConflictError(f"duplicate employee {employee.id}")
            store._employees[employee.id] = employee
        store._check_all()
        return store

    # ------------------------------------------------------------------ reads

    def employees(self, updated_since: datetime | None = None) -> RecordList[Employee]:
        """All employees, or only those stamped at or after ``updated_since``."""
        with self._lock:
            as_of = self._clock()
            records = self._filter(list(self._employees.values()), updated_since)
        return RecordList[Employee](records=records, as_of=as_of)

    def teams(self, updated_since: datetime | None = None) -> RecordList[Team]:
        with self._lock:
            as_of = self._clock()
            records = self._filter(list(self._teams.values()), updated_since)
        return RecordList[Team](records=records, as_of=as_of)

    def employee(self, employee_id: str) -> Employee:
        with self._lock:
            return self._employee(employee_id)

    def team(self, team_id: str) -> Team:
        with self._lock:
            return self._team(team_id)

    # ----------------------------------------------------------------- writes

    def hire(self, request: EmployeeCreate, *, actor: str | None = None) -> Employee:
        with self._lock:
            if request.id in self._employees:
                raise RecordConflictError(f"employee {request.id} exists")
            employee = Employee(**request.model_dump(), updated_at=self._clock(), updated_by=actor)
            self._check_employee(employee)
            self._employees[employee.id] = employee
            return employee

    def update_employee(
        self, employee_id: str, request: EmployeeUpdate, *, actor: str | None = None
    ) -> Employee:
        with self._lock:
            current = self._employee(employee_id)
            # Only fields that were sent change. ``null`` clears the manager; for any
            # other field it is ignored, because a record can't lose its name or team.
            changes = {
                field: getattr(request, field)
                for field in request.model_fields_set
                if field == "manager_id" or getattr(request, field) is not None
            }
            employee = Employee.model_validate(
                {
                    **current.model_dump(),
                    **changes,
                    "updated_at": self._clock(),
                    "updated_by": actor,
                }
            )
            self._check_employee(employee)
            self._employees[employee_id] = employee
            return employee

    def update_team(self, team_id: str, request: TeamUpdate, *, actor: str | None = None) -> Team:
        with self._lock:
            current = self._team(team_id)
            changes = {
                f: getattr(request, f) for f in request.model_fields_set if getattr(request, f)
            }
            team = Team.model_validate(
                {
                    **current.model_dump(),
                    **changes,
                    "updated_at": self._clock(),
                    "updated_by": actor,
                }
            )
            if team.lead_id not in self._employees:
                raise InvalidReferenceError(f"team lead {team.lead_id} does not exist")
            self._teams[team_id] = team
            return team

    def delete_employee(self, employee_id: str, *, actor: str | None = None) -> None:
        """Remove the record. Nothing records that it ever existed."""
        with self._lock:
            self._employee(employee_id)
            reports = sorted(e.id for e in self._employees.values() if e.manager_id == employee_id)
            leads = sorted(t.id for t in self._teams.values() if t.lead_id == employee_id)
            if reports or leads:
                ties = [f"manages {', '.join(reports)}"] if reports else []
                ties += [f"leads {', '.join(leads)}"] if leads else []
                raise RecordConflictError(f"employee {employee_id} still {' and '.join(ties)}")
            del self._employees[employee_id]

    # --------------------------------------------------------------- helpers

    @staticmethod
    def _filter[R: (Employee, Team)](records: list[R], since: datetime | None) -> list[R]:
        selected = (
            records if since is None else [r for r in records if r.updated_at >= _as_utc(since)]
        )
        return sorted(selected, key=lambda r: r.id)

    def _employee(self, employee_id: str) -> Employee:
        try:
            return self._employees[employee_id]
        except KeyError:
            raise RecordNotFoundError(f"employee {employee_id} not found") from None

    def _team(self, team_id: str) -> Team:
        try:
            return self._teams[team_id]
        except KeyError:
            raise RecordNotFoundError(f"team {team_id} not found") from None

    def _check_employee(self, employee: Employee) -> None:
        if employee.team_id not in self._teams:
            raise InvalidReferenceError(f"team {employee.team_id} does not exist")
        if employee.manager_id is not None:
            if employee.manager_id == employee.id:
                raise InvalidReferenceError(f"employee {employee.id} can't manage themselves")
            if employee.manager_id not in self._employees:
                raise InvalidReferenceError(f"manager {employee.manager_id} does not exist")
        clash = [
            e.id
            for e in self._employees.values()
            if e.name == employee.name and e.id != employee.id
        ]
        if clash:
            raise RecordConflictError(
                f"name {employee.name!r} is already used by {clash[0]}; names link to the "
                "identity provider 1-1, so they must be unique"
            )

    def _check_all(self) -> None:
        for employee in self._employees.values():
            self._check_employee(employee)
        for team in self._teams.values():
            if team.lead_id not in self._employees:
                raise InvalidReferenceError(f"team {team.id} lead {team.lead_id} does not exist")
