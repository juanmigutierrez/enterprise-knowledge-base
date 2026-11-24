"""T1 for the docs tool: its vocabulary maps onto the one change event."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ekb.connect.docs_api import DocsAcl, DocsPage
from ekb.connect.normalize import docs as normalize
from ekb.contracts import Acl, ChangeOp

AT = datetime(2026, 11, 23, 9, 0, tzinfo=UTC)
PAGE = DocsPage(
    id="HR-PL-05",
    title="Teletrabajo",
    acl=DocsAcl(users=("bob",), groups=("hr-team", "es-employees")),
    rev=3,
    acl_rev=2,
    content_hash="sha256:abc",
    updated_at=AT,
)


@pytest.mark.parametrize(
    ("kind", "op"),
    [
        ("page.created", ChangeOp.UPSERT),
        ("page.updated", ChangeOp.UPSERT),
        ("page.permissions_changed", ChangeOp.ACL_CHANGE),
        ("page.deleted", ChangeOp.DELETE),
    ],
)
def test_every_docs_change_kind_has_an_op(kind: str, op: ChangeOp) -> None:
    assert normalize.op_for(kind) is op


def test_an_unknown_kind_is_an_error_not_a_skip() -> None:
    with pytest.raises(normalize.UnknownChangeKindError, match=r"page\.moved"):
        normalize.op_for("page.moved")


def test_upsert_carries_pointer_acl_hash_and_version() -> None:
    event = normalize.upsert_event("hr_docs", PAGE, raw_pointer="mem://raw/hr_docs/HR-PL-05/3.2")
    assert event.op is ChangeOp.UPSERT
    assert event.document_key == "hr_docs:HR-PL-05"
    assert event.version == "3.2"
    assert event.acl == Acl(users=frozenset({"bob"}), groups=frozenset({"es-employees", "hr-team"}))
    assert event.raw_pointer == "mem://raw/hr_docs/HR-PL-05/3.2"
    assert event.content_hash == "sha256:abc"
    assert event.occurred_at == AT


def test_acl_change_needs_no_content() -> None:
    event = normalize.acl_change_event("hr_docs", PAGE)
    assert event.op is ChangeOp.ACL_CHANGE
    assert event.raw_pointer is None
    assert event.acl.groups == {"es-employees", "hr-team"}


def test_delete_carries_nothing_about_the_document() -> None:
    """Deletion gate: no pointer, no ACL, no hash travels with a delete."""
    event = normalize.delete_event("hr_docs", "HR-PL-99", version="1.1", occurred_at=AT)
    assert event.op is ChangeOp.DELETE
    assert event.raw_pointer is None
    assert event.content_hash is None
    assert event.acl == Acl()


@pytest.mark.parametrize(
    ("version", "parsed"),
    [("3.2", (3, 2)), ("12.1", (12, 1)), ("3", None), ("v3.2", None), ("3.x", None), ("", None)],
)
def test_parse_version(version: str, parsed: tuple[int, int] | None) -> None:
    assert normalize.parse_version(version) == parsed
    if parsed:
        assert normalize.version_of(*parsed) == version
