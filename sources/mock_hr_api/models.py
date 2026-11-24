"""Record shapes of the mock HR API: employees and teams, directory fields only.

There is no pay data and no per-record permission list. Record systems often
have no document-style ACLs; the connector applies a rule instead (here: every
record is readable by ``all-staff``), decided in the S1b plan.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

_EMPLOYEE_ID = r"^E-\d{4}$"
_TEAM_ID = r"^T-[A-Z]{3}$"
ACTOR_ID = r"^[a-z][a-z0-9.-]*$"  # an identity provider user ID, as in X-Actor


class Team(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=_TEAM_ID)
    name: str = Field(min_length=1)
    department: str
    lead_id: str = Field(pattern=_EMPLOYEE_ID)
    updated_at: datetime
    updated_by: str | None = None  # who saved it last (X-Actor); None for seeded records


class TeamUpdate(BaseModel):
    """Fields an HR admin may change on a team; missing fields stay as they are."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str | None = Field(default=None, min_length=1)
    lead_id: str | None = Field(default=None, pattern=_EMPLOYEE_ID)


class EmployeeFields(BaseModel):
    """Everything an employee record holds except its bookkeeping."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)  # full name: the 1-1 link to the identity provider
    work_email: str = Field(pattern=r"^[^@\s]+@[^@\s]+$")
    team_id: str = Field(pattern=_TEAM_ID)
    office: str
    manager_id: str | None = Field(default=None, pattern=_EMPLOYEE_ID)
    start_date: date


class EmployeeCreate(EmployeeFields):
    """Request body to hire someone (or to load a seed record)."""

    id: str = Field(pattern=_EMPLOYEE_ID)


class Employee(EmployeeCreate):
    updated_at: datetime
    updated_by: str | None = None  # who saved it last (X-Actor); None for seeded records


class EmployeeUpdate(BaseModel):
    """Fields an HR admin may change; missing fields stay as they are.

    ``manager_id`` can be cleared with an explicit ``null``; leaving it out
    keeps the current manager. That is why the store looks at which fields
    were *sent* (``model_fields_set``), not only at their values.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str | None = Field(default=None, min_length=1)
    work_email: str | None = Field(default=None, pattern=r"^[^@\s]+@[^@\s]+$")
    team_id: str | None = Field(default=None, pattern=_TEAM_ID)
    office: str | None = None
    manager_id: str | None = Field(default=None, pattern=_EMPLOYEE_ID)


class RecordList[R](BaseModel):
    """A listing or a delta, plus the server time to use as the next ``updated_since``."""

    model_config = ConfigDict(frozen=True)

    records: list[R]
    as_of: datetime
