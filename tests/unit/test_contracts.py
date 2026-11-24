from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from ekb.contracts import Acl, ChangeEvent, ChangeOp, DiffAction, DiffDecision

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


@pytest.mark.parametrize(
    "extra",
    [
        {"content_hash": "sha256:abc"},
        {"acl": Acl(groups=frozenset({"hr-team"}))},
        {"person_id": "alice"},
    ],
)
def test_delete_carries_nothing_about_the_item(extra: dict[str, object]) -> None:
    """Deletion gate, enforced by the contract: only keys, version, time and who deleted."""
    with pytest.raises(ValidationError, match="delete"):
        ChangeEvent(
            source="hr_docs",
            source_id="1",
            op=ChangeOp.DELETE,
            version="2",
            occurred_at=NOW,
            **extra,
        )


def test_delete_may_say_who_deleted() -> None:
    event = ChangeEvent(
        source="hr_docs",
        source_id="1",
        op=ChangeOp.DELETE,
        version="2",
        occurred_at=NOW,
        changed_by="dana",
    )
    assert (event.changed_by, event.schema_version) == ("dana", 2)


# ------------------------------------------------------------------ T2 decision


def _event(op: ChangeOp) -> ChangeEvent:
    fields: dict[str, object] = {
        "source": "hr_docs",
        "source_id": "HR-PL-04",
        "op": op,
        "version": "2.1",
        "occurred_at": NOW,
    }
    if op is ChangeOp.UPSERT:
        fields["raw_pointer"] = "mem://ekb-raw/hr_docs/HR-PL-04/2.1"
    return ChangeEvent.model_validate(fields)


@pytest.mark.parametrize("action", [DiffAction.SKIP, DiffAction.ACL_ONLY, DiffAction.PROCESS])
def test_a_delete_can_only_be_purged(action: DiffAction) -> None:
    """The deletion gate in the contract: no T2 option can skip a delete."""
    with pytest.raises(ValidationError, match="a delete must be purged"):
        DiffDecision(action=action, reason="x", event=_event(ChangeOp.DELETE))


@pytest.mark.parametrize("op", [ChangeOp.UPSERT, ChangeOp.ACL_CHANGE])
def test_only_a_delete_can_be_purged(op: ChangeOp) -> None:
    with pytest.raises(ValidationError, match="only a delete can be purged"):
        DiffDecision(action=DiffAction.PURGE, reason="x", event=_event(op))


def test_only_an_upsert_can_be_processed() -> None:
    with pytest.raises(ValidationError, match="only an upsert"):
        DiffDecision(action=DiffAction.PROCESS, reason="x", event=_event(ChangeOp.ACL_CHANGE))


def test_valid_decisions_are_accepted() -> None:
    upsert, acl, delete = (
        _event(op) for op in (ChangeOp.UPSERT, ChangeOp.ACL_CHANGE, ChangeOp.DELETE)
    )
    for action, event in [
        (DiffAction.PROCESS, upsert),
        (DiffAction.ACL_ONLY, upsert),
        (DiffAction.SKIP, upsert),
        (DiffAction.ACL_ONLY, acl),
        (DiffAction.SKIP, acl),
        (DiffAction.PURGE, delete),
    ]:
        assert DiffDecision(action=action, reason="x", event=event).action is action
