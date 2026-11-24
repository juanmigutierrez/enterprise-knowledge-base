from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from ekb.contracts import (
    Acl,
    Block,
    BlockKind,
    ChangeEvent,
    ChangeOp,
    DiffAction,
    DiffDecision,
    ParsedDocument,
)

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


# ----------------------------------------------------------------- T3: parsed documents


@pytest.mark.parametrize(
    ("fields", "problem"),
    [
        ({"kind": BlockKind.PARAGRAPH, "text": "  "}, "must have text"),
        ({"kind": BlockKind.HEADING, "text": "x", "level": 0}, "level is 1 to 6"),
        ({"kind": BlockKind.HEADING, "text": "x", "level": 7}, "level is 1 to 6"),
        ({"kind": BlockKind.LIST_ITEM, "text": "x"}, "nesting depth"),
        ({"kind": BlockKind.TABLE, "text": "x"}, "a table has rows"),
        ({"kind": BlockKind.PARAGRAPH, "text": "x", "rows": (("a",),)}, "only a table"),
    ],
)
def test_a_block_must_have_a_consistent_shape(fields: dict[str, object], problem: str) -> None:
    with pytest.raises(ValidationError, match=problem):
        Block.model_validate(fields)


def test_the_clean_text_keeps_list_items_together_and_indents_nested_ones() -> None:
    def item(text: str, level: int = 1, marker: str = "-") -> Block:
        return Block(kind=BlockKind.LIST_ITEM, text=text, level=level, marker=marker)

    parsed = ParsedDocument(
        document_key="hr_docs:HR-PL-01",
        version="1.1",
        title="Leave",
        media_type="text/markdown",
        parser_id="test",
        blocks=(
            Block(kind=BlockKind.HEADING, text="Leave", level=1),
            item("first"),
            item("nested", level=2),
            item("more about nested", level=2, marker=""),
            item("second", marker="2."),
            Block(kind=BlockKind.TABLE, text="a | b\n1 | 2", rows=(("a", "b"), ("1", "2"))),
        ),
    )
    assert parsed.text == (
        "Leave\n\n- first\n  - nested\n    more about nested\n2. second\n\na | b\n1 | 2"
    )
    assert ParsedDocument.model_validate(parsed.model_dump()) == parsed
