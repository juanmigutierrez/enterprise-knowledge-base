"""HTTP API of the mock HR record API. Run it with ``python -m sources.mock_hr_api``.

Endpoints (plus ``GET /health`` from the shared factory):

========  ===========================================  =================================
GET      /employees[?updated_since=<ISO time>]        listing, or delta (inclusive)
GET      /employees/{id}                              one record
POST     /employees                                   hire
PATCH    /employees/{id}                              change some fields
DELETE   /employees/{id}                              delete: **no event, no trace**
GET      /teams[?updated_since=<ISO time>]            listing, or delta
GET      /teams/{id}                                  one team
PATCH    /teams/{id}                                  rename, change lead
POST     /reset                                       reload the synthetic records
========  ===========================================  =================================

Every listing returns ``as_of``: pass it back as ``updated_since`` next time.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Response, status

from sources.common.factory import create_service_app
from sources.mock_hr_api.models import (
    Employee,
    EmployeeCreate,
    EmployeeUpdate,
    RecordList,
    Team,
    TeamUpdate,
)
from sources.mock_hr_api.store import (
    SEED_FILE,
    HrStore,
    InvalidReferenceError,
    RecordConflictError,
    RecordNotFoundError,
)

DEFAULT_PORT = 8082


def create_app(seed_file: Path = SEED_FILE) -> FastAPI:
    app = create_service_app(
        "Mock HR record API",
        errors={
            RecordNotFoundError: status.HTTP_404_NOT_FOUND,
            RecordConflictError: status.HTTP_409_CONFLICT,
            InvalidReferenceError: status.HTTP_422_UNPROCESSABLE_CONTENT,
        },
    )
    app.state.store = HrStore.from_seed(seed_file)

    def store() -> HrStore:
        result: HrStore = app.state.store
        return result

    @app.get("/employees")
    def list_employees(updated_since: datetime | None = None) -> RecordList[Employee]:
        return store().employees(updated_since)

    @app.get("/employees/{employee_id}")
    def get_employee(employee_id: str) -> Employee:
        return store().employee(employee_id)

    @app.post("/employees", status_code=status.HTTP_201_CREATED)
    def hire(request: EmployeeCreate) -> Employee:
        return store().hire(request)

    @app.patch("/employees/{employee_id}")
    def update_employee(employee_id: str, request: EmployeeUpdate) -> Employee:
        return store().update_employee(employee_id, request)

    @app.delete("/employees/{employee_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_employee(employee_id: str) -> Response:
        store().delete_employee(employee_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get("/teams")
    def list_teams(updated_since: datetime | None = None) -> RecordList[Team]:
        return store().teams(updated_since)

    @app.get("/teams/{team_id}")
    def get_team(team_id: str) -> Team:
        return store().team(team_id)

    @app.patch("/teams/{team_id}")
    def update_team(team_id: str, request: TeamUpdate) -> Team:
        return store().update_team(team_id, request)

    @app.post("/reset")
    def reset() -> dict[str, int]:
        app.state.store = HrStore.from_seed(seed_file)
        current = store()
        return {
            "employees": len(current.employees().records),
            "teams": len(current.teams().records),
        }

    return app
