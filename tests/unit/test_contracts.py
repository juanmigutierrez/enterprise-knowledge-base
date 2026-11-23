from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from ekb.contracts import Acl, ChangeEvent, ChangeOp

NOW = datetime(2026, 11, 23, tzinfo=UTC)


def test_upsert_requires_pointer_to_original() -> None:
    with pytest.raises(ValidationError, match="raw_pointer"):
        ChangeEvent(
            source="mock_docs", source_id="1", op=ChangeOp.UPSERT, version="v1", occurred_at=NOW
        )


def test_delete_must_not_carry_content() -> None:
    with pytest.raises(ValidationError, match="delete"):
        ChangeEvent(
            source="mock_docs",
            source_id="1",
            op=ChangeOp.DELETE,
            version="v2",
            occurred_at=NOW,
            raw_pointer="s3://ekb-raw/mock_docs/1/v2",
        )


def test_document_key_groups_events_per_document() -> None:
    event = ChangeEvent(
        source="mock_docs",
        source_id="42",
        op=ChangeOp.ACL_CHANGE,
        version="v3",
        occurred_at=NOW,
        acl=Acl(groups=frozenset({"hr"})),
    )
    assert event.document_key == "mock_docs:42"
