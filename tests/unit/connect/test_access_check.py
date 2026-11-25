"""The connector's current access check (S14): the docs tool and the identity provider, now.

Against the real mocks in-process for what they answer, and with
``httpx.MockTransport`` for sources that time out, fail or answer garbage (a
``TestClient`` raises ``httpx2`` errors, see ``tests/http.py``).
"""

from __future__ import annotations

from typing import cast

import httpx
import pytest
from fastapi.testclient import TestClient

from ekb.connect.access_check import SourceAccessChecker
from ekb.connect.docs_api import DocsApi
from ekb.connect.identity_api import IdentityApi
from ekb.contracts import AccessVerdict
from tests.http import as_httpx

pytestmark = pytest.mark.filterwarnings(
    "ignore:You should not use the 'timeout' argument with the TestClient"
)
DANA = {"X-Actor": "dana"}
LEAVE, PAY, OLD = "hr_docs:HR-PL-01", "hr_docs:HR-CMP-01", "hr_docs:HR-PL-99"


def checker(docs: object, idp: object) -> SourceAccessChecker:
    """The check over a ``TestClient`` (a real mock) or an ``httpx.Client`` (a failing one)."""
    return SourceAccessChecker(
        DocsApi(cast(httpx.Client, docs)), IdentityApi(cast(httpx.Client, idp))
    )


def failing(status: int | None = None, body: bytes = b"") -> httpx.Client:
    """A source that times out (``status`` None) or answers ``status`` with ``body``."""

    def answer(request: httpx.Request) -> httpx.Response:
        if status is None:
            raise httpx.ReadTimeout("no answer", request=request)
        return httpx.Response(status, content=body)

    return httpx.Client(base_url="http://source.invalid", transport=httpx.MockTransport(answer))


def test_a_reader_is_allowed_with_the_page_as_it_is_now(docs: TestClient, idp: TestClient) -> None:
    answers = checker(docs, idp).check("alice", [LEAVE, LEAVE])
    assert list(answers) == [LEAVE]  # each document once
    leave = answers[LEAVE]
    assert leave.verdict is AccessVerdict.ALLOWED
    assert leave.reason == "the docs tool lets alice read it now (through all-staff)"
    assert leave.title == "Annual leave policy"
    assert leave.version == "1.1"
    assert leave.content_hash == docs.get("/pages/HR-PL-01").json()["content_hash"]
    assert leave.checker_id == "source@1(hr_docs, timeout=2s)"


def test_a_page_whose_readers_were_narrowed_in_the_source_is_denied(
    docs: TestClient, idp: TestClient
) -> None:
    """The index has not seen the change: only the source knows."""
    docs.put("/pages/HR-PL-01/acl", json={"groups": ["hr-team"]}, headers=DANA)
    alice = checker(docs, idp).check("alice", [LEAVE])[LEAVE]
    assert alice.verdict is AccessVerdict.DENIED
    assert alice.reason == "the docs tool's readers no longer include alice"
    assert alice.title == ""  # nothing about the page is passed on
    assert checker(docs, idp).check("dana", [LEAVE])[LEAVE].verdict is AccessVerdict.ALLOWED


def test_a_page_shared_with_a_user_by_name_admits_them(docs: TestClient, idp: TestClient) -> None:
    docs.put("/pages/HR-CMP-01/acl", json={"users": ["alice"]}, headers=DANA)
    alice = checker(docs, idp).check("alice", [PAY])[PAY]
    assert alice.verdict is AccessVerdict.ALLOWED
    assert alice.reason.endswith("(by name)")


def test_group_membership_is_read_from_the_identity_provider_now(
    docs: TestClient, idp: TestClient
) -> None:
    assert checker(docs, idp).check("bob", [PAY])[PAY].verdict is AccessVerdict.ALLOWED
    idp.delete("/scim/v2/Groups/hr-compensation/members/bob", headers=DANA)
    assert checker(docs, idp).check("bob", [PAY])[PAY].verdict is AccessVerdict.DENIED


def test_a_deleted_page_is_deleted(docs: TestClient, idp: TestClient) -> None:
    docs.delete("/pages/HR-PL-99", headers=DANA)
    old = checker(docs, idp).check("alice", [OLD])[OLD]
    assert (old.verdict, old.reason) == (
        AccessVerdict.DELETED,
        "the docs tool no longer has the page",
    )


def test_a_leaver_and_an_unknown_person_are_denied_everything(
    docs: TestClient, idp: TestClient
) -> None:
    idp.post("/scim/v2/Users/olivia/deactivate", headers=DANA)
    olivia = checker(docs, idp).check("olivia", [LEAVE, OLD])
    assert {a.verdict for a in olivia.values()} == {AccessVerdict.DENIED}
    assert olivia[LEAVE].reason == "the identity provider lists olivia as inactive (has left)"
    mallory = checker(docs, idp).check("mallory", [LEAVE])[LEAVE]
    assert mallory.verdict is AccessVerdict.DENIED
    assert mallory.reason == "the identity provider does not know mallory"


def test_a_source_this_check_does_not_cover_is_unavailable(
    docs: TestClient, idp: TestClient
) -> None:
    answers = checker(docs, idp).check("alice", ["hr_api:employees/E-1001", "hr_docs:"])
    assert {a.verdict for a in answers.values()} == {AccessVerdict.UNAVAILABLE}
    assert (
        answers["hr_api:employees/E-1001"].reason == "no current access check for source 'hr_api'"
    )


@pytest.mark.parametrize(
    ("status", "body", "why"),
    [
        (None, b"", "timed out"),
        (500, b"", "HTTP 500"),
        (200, b"not json", "an answer that does not parse"),
        (200, b'{"id": "HR-PL-01"}', "an answer that does not parse"),
    ],
)
def test_a_docs_tool_that_does_not_answer_well_is_unavailable(
    idp: TestClient, status: int | None, body: bytes, why: str
) -> None:
    with failing(status, body) as docs:
        leave = checker(docs, idp).check("alice", [LEAVE])[LEAVE]
    assert leave.verdict is AccessVerdict.UNAVAILABLE
    assert leave.reason == f"the docs tool did not answer ({why})"


@pytest.mark.parametrize(("status", "why"), [(None, "timed out"), (503, "HTTP 503")])
def test_an_identity_provider_that_does_not_answer_makes_every_document_unavailable(
    docs: TestClient, status: int | None, why: str
) -> None:
    with failing(status) as idp:
        answers = checker(docs, idp).check("alice", [LEAVE, PAY])
    assert {a.verdict for a in answers.values()} == {AccessVerdict.UNAVAILABLE}
    assert answers[PAY].reason == f"the identity provider did not answer ({why})"


def test_the_check_waits_only_its_own_timeout(docs: TestClient) -> None:
    seen: list[object] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request.extensions["timeout"])
        return httpx.Response(404)

    idp = httpx.Client(base_url="http://idp.invalid", transport=httpx.MockTransport(answer))
    fast = SourceAccessChecker(DocsApi(as_httpx(docs)), IdentityApi(idp), timeout=0.5)
    assert fast.check("alice", [LEAVE])[LEAVE].verdict is AccessVerdict.DENIED  # 404: unknown
    assert seen == [{"connect": 0.5, "read": 0.5, "write": 0.5, "pool": 0.5}]
    assert fast.check("alice", []) == {}
    with pytest.raises(ValueError, match="timeout"):
        SourceAccessChecker(DocsApi(as_httpx(docs)), IdentityApi(idp), timeout=0)


@pytest.mark.parametrize("field", ["id", "groups", "active"])
def test_invalid_identity_cannot_authorize_a_page(
    docs: TestClient, idp: TestClient, field: str
) -> None:
    data = idp.get("/scim/v2/Users/bob").json()
    data[field] = {"id": "another-user", "groups": {"hr-compensation": False}, "active": "yes"}[
        field
    ]
    with httpx.Client(
        base_url="http://idp.invalid",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=data)),
    ) as wrong:
        assert checker(docs, wrong).check("bob", [PAY])[PAY].verdict is AccessVerdict.UNAVAILABLE


@pytest.mark.parametrize("field", ["id", "first_rev"])
def test_page_must_identify_the_requested_page_and_lifetime(
    docs: TestClient, idp: TestClient, field: str
) -> None:
    data = docs.get("/pages/HR-PL-01").json()
    if field == "id":
        data["id"] = "HR-PL-02"
    else:
        del data["first_rev"]
    with httpx.Client(
        base_url="http://docs.invalid",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=data)),
    ) as wrong:
        assert (
            checker(wrong, idp).check("alice", [LEAVE])[LEAVE].verdict is AccessVerdict.UNAVAILABLE
        )
