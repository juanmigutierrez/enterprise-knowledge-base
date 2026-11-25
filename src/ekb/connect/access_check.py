"""The current access check (S14): may this person read these documents *now*, by the source?

The architecture draws it as an arrow from the Knowledge API back to the
connector: before Q4 releases a passage, it asks the system the passage came
from, not the copies the index holds. For the docs tool that takes two reads:

1. **The person, from the identity provider** (``GET /scim/v2/Users/{id}``):
   active or not, and their groups *now*. Not the permission store, which is a
   copy kept by the identity sync and lags it a little: a person removed from a
   group a moment ago must already be denied. Read once per question.
2. **Each page, from the docs tool** (``GET /pages/{id}``): its readers now, its
   title, its content hash and version. A 404 is a deleted page. Read once per
   document.

Then the same rule as everywhere else: :func:`ekb.contracts.admits`. The page's
readers are the docs tool's user and group names, which are identity user IDs
and group names in the mock, exactly what T1 copies (D3).

**Unknown is no.** A source that does not answer within ``timeout`` (2 s) or
answers with an error, an answer that does not parse, or a document from a
source this check does not cover: ``unavailable``, and Q4 holds the passage
back (D8). An unknown or inactive person: ``denied`` for every document.

In production the same check is a read of the source under the user's own
identity (on-behalf-of, S15), or an authoritative permission API that accounts
for current group membership (D8). Here the mock's permissions are read
directly, as D8 allows for the HR mock.
"""

from __future__ import annotations

from collections.abc import Sequence

import httpx
from pydantic import ValidationError

from ekb.connect.docs_api import DocsApi
from ekb.connect.identity_api import IdentityApi, IdpUser
from ekb.connect.normalize.docs import acl_of, version_of
from ekb.contracts import AccessVerdict, Principals, SourceAccess, admits

VERSION = 1
TIMEOUT_S = 2.0
"""How long Q4 waits for each read before treating the source as not answering."""
DOCS_SOURCE = "hr_docs"

_FAILED = (httpx.HTTPError, ValidationError, KeyError, TypeError, ValueError)
"""What a source that answers badly raises: a timeout, an error status, an unreadable body."""


class SourceAccessChecker:
    """Q4's :class:`~ekb.access.query.q4_postcheck.port.SourceAccessCheck` for the docs tool."""

    def __init__(
        self,
        docs: DocsApi,
        identity: IdentityApi,
        *,
        source: str = DOCS_SOURCE,
        timeout: float = TIMEOUT_S,
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self._docs, self._identity = docs, identity
        self._source, self._timeout = source, timeout

    @property
    def checker_id(self) -> str:
        return f"source@{VERSION}({self._source}, timeout={self._timeout:g}s)"

    def check(self, user_id: str, document_keys: Sequence[str]) -> dict[str, SourceAccess]:
        keys = tuple(dict.fromkeys(document_keys))
        if not keys:
            return {}
        try:
            user = self._identity.get_user(user_id, timeout=self._timeout)
        except _FAILED as exc:
            reason = f"the identity provider did not answer ({_why(exc)})"
            return {key: self._answer(key, AccessVerdict.UNAVAILABLE, reason) for key in keys}
        if user is not None and user.id != user_id:
            reason = "the identity provider answered for another user"
            return {key: self._answer(key, AccessVerdict.UNAVAILABLE, reason) for key in keys}
        principals, refusal = _principals(user_id, user)
        return {key: self._page(key, principals, refusal) for key in keys}

    def _page(self, key: str, principals: Principals | None, refusal: str) -> SourceAccess:
        source, _, page_id = key.partition(":")
        if source != self._source or not page_id:
            reason = f"no current access check for source {source!r}"
            return self._answer(key, AccessVerdict.UNAVAILABLE, reason)
        if principals is None:
            return self._answer(key, AccessVerdict.DENIED, refusal)
        try:
            fetched = self._docs.get_page(page_id, timeout=self._timeout)
        except _FAILED as exc:
            reason = f"the docs tool did not answer ({_why(exc)})"
            return self._answer(key, AccessVerdict.UNAVAILABLE, reason)
        if fetched is None:
            return self._answer(key, AccessVerdict.DELETED, "the docs tool no longer has the page")
        page = fetched.page
        if page.id != page_id:
            return self._answer(
                key, AccessVerdict.UNAVAILABLE, "the docs tool answered for another page"
            )
        if page.first_rev is None or page.first_rev > page.rev:
            return self._answer(
                key, AccessVerdict.UNAVAILABLE, "the page lifetime could not be checked"
            )
        version = version_of(page.rev, page.acl_rev)
        if not admits(acl_of(page.acl), principals):
            reason = f"the docs tool's readers no longer include {principals.user_id}"
            return self._answer(key, AccessVerdict.DENIED, reason, version=version)
        groups = sorted(set(page.acl.groups) & principals.groups)
        why = f"through {', '.join(groups)}" if groups else "by name"
        return self._answer(
            key,
            AccessVerdict.ALLOWED,
            f"the docs tool lets {principals.user_id} read it now ({why})",
            title=page.title,
            content_hash=page.content_hash,
            first_rev=page.first_rev,
            version=version,
        )

    def _answer(
        self,
        key: str,
        verdict: AccessVerdict,
        reason: str,
        *,
        title: str = "",
        content_hash: str | None = None,
        first_rev: int | None = None,
        version: str = "",
    ) -> SourceAccess:
        return SourceAccess(
            document_key=key,
            verdict=verdict,
            reason=reason,
            title=title,
            content_hash=content_hash,
            first_rev=first_rev,
            version=version,
            checker_id=self.checker_id,
        )


def _principals(user_id: str, user: IdpUser | None) -> tuple[Principals | None, str]:
    """Who the identity provider says this person is now, or why they read nothing."""
    if user is None:
        return None, f"the identity provider does not know {user_id}"
    if not user.active:
        return None, f"the identity provider lists {user_id} as inactive (has left)"
    return Principals(user_id=user.id, groups=frozenset(user.groups)), ""


def _why(exc: Exception) -> str:
    if isinstance(exc, httpx.TimeoutException):
        return "timed out"
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    if isinstance(exc, httpx.HTTPError):
        return type(exc).__name__
    return "an answer that does not parse"
