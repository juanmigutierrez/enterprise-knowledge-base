"""HTTP API of the mock identity provider. Run it with ``python -m sources.mock_idp``.

Endpoints (plus ``GET /health`` from the shared factory). Paths follow SCIM 2.0;
deactivation follows Okta's lifecycle endpoints:

======  ============================================  ===============================
GET     /scim/v2/Users                                all users (active or not)
GET     /scim/v2/Users/{id}                           one user, with their groups
POST    /scim/v2/Users                                joiner
POST    /scim/v2/Users/{id}/deactivate                leaver
GET     /scim/v2/Groups                               all groups with members
GET     /scim/v2/Groups/{id}                          one group
POST    /scim/v2/Groups/{id}/members                  add a member ``{"user_id": ...}``
DELETE  /scim/v2/Groups/{id}/members/{user_id}        remove a member
GET     /events?since=<seq>                           identity event feed after a cursor
POST    /reset                                        reload the synthetic directory
======  ============================================  ===============================
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, Query, status

from sources.common.directory import DIRECTORY_FILE
from sources.common.factory import create_service_app
from sources.mock_idp.models import (
    IdentityEventFeed,
    ListResponse,
    MemberAdd,
    ScimGroup,
    ScimUser,
    UserCreate,
)
from sources.mock_idp.store import (
    IdentityConflictError,
    IdpStore,
    NotFoundError,
    UnknownPrincipalError,
)

DEFAULT_PORT = 8083


def create_app(directory_file: Path = DIRECTORY_FILE) -> FastAPI:
    app = create_service_app(
        "Mock identity provider",
        errors={
            NotFoundError: status.HTTP_404_NOT_FOUND,
            IdentityConflictError: status.HTTP_409_CONFLICT,
            UnknownPrincipalError: status.HTTP_422_UNPROCESSABLE_CONTENT,
        },
    )
    app.state.store = IdpStore.from_seed(directory_file)

    def store() -> IdpStore:
        result: IdpStore = app.state.store
        return result

    @app.get("/scim/v2/Users", response_model_by_alias=True)
    def list_users() -> ListResponse[ScimUser]:
        users = store().users()
        return ListResponse[ScimUser](total_results=len(users), resources=users)

    @app.get("/scim/v2/Users/{user_id}", response_model_by_alias=True)
    def get_user(user_id: str) -> ScimUser:
        return store().user(user_id)

    @app.post("/scim/v2/Users", status_code=status.HTTP_201_CREATED, response_model_by_alias=True)
    def create_user(request: UserCreate) -> ScimUser:
        return store().create_user(request)

    @app.post("/scim/v2/Users/{user_id}/deactivate", response_model_by_alias=True)
    def deactivate(user_id: str) -> ScimUser:
        return store().deactivate(user_id)

    @app.get("/scim/v2/Groups", response_model_by_alias=True)
    def list_groups() -> ListResponse[ScimGroup]:
        groups = store().groups()
        return ListResponse[ScimGroup](total_results=len(groups), resources=groups)

    @app.get("/scim/v2/Groups/{group_id}", response_model_by_alias=True)
    def get_group(group_id: str) -> ScimGroup:
        return store().group(group_id)

    @app.post("/scim/v2/Groups/{group_id}/members", response_model_by_alias=True)
    def add_member(group_id: str, request: MemberAdd) -> ScimGroup:
        return store().add_member(group_id, request.user_id)

    @app.delete("/scim/v2/Groups/{group_id}/members/{user_id}", response_model_by_alias=True)
    def remove_member(group_id: str, user_id: str) -> ScimGroup:
        return store().remove_member(group_id, user_id)

    @app.get("/events")
    def events(since: Annotated[int, Query(ge=0)] = 0) -> IdentityEventFeed:
        entries, cursor = store().read_feed(since)
        return IdentityEventFeed(events=entries, cursor=cursor)

    @app.post("/reset")
    def reset() -> dict[str, int]:
        app.state.store = IdpStore.from_seed(directory_file)
        current = store()
        return {"users": len(current.users()), "groups": len(current.groups())}

    return app
