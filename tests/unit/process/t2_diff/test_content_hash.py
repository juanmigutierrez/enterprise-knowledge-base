"""T2's decision table, one test per row: what each event needs given the registry."""

from __future__ import annotations

import pytest

from ekb.changelog.registry.port import DocumentRecord, record_after
from ekb.contracts import Acl, ChangeEvent, DiffAction, DiffDecision
from ekb.process.t2_diff.options.content_hash import ContentHashDiffer, readers_diff
from tests.unit.changelog.factories import ALL_STAFF, acl_change, delete, upsert

HR_TEAM = Acl(groups=frozenset({"hr-team"}))
ES = Acl(groups=frozenset({"es-employees"}))
HR_AND_ES = Acl(groups=frozenset({"hr-team", "es-employees"}))


def _decide(previous: DocumentRecord | None, event: ChangeEvent) -> DiffDecision:
    return ContentHashDiffer().decide(previous, event)


def _processed(*events: ChangeEvent) -> DocumentRecord:
    """The registry record after these events were processed, in order."""
    record: DocumentRecord | None = None
    for event in events:
        record = record_after(record, event)
    assert record is not None
    return record


LIVE = _processed(upsert("HR-PL-04", "1.1"))
TOMBSTONE = _processed(upsert("HR-PL-04", "1.1"), delete("HR-PL-04", "1.1"))


# ---------------------------------------------------------------------- deletes


@pytest.mark.parametrize(
    ("previous", "reason"),
    [
        (LIVE, "deleted in the source"),
        (None, "before it was ever processed"),
        (TOMBSTONE, "deleted again"),
    ],
    ids=["live", "never-processed", "already-deleted"],
)
def test_a_delete_is_always_purged(previous: DocumentRecord | None, reason: str) -> None:
    """Deletion gate: whatever the registry says, a delete goes to every store."""
    decision = _decide(previous, delete("HR-PL-04", "1.1"))
    assert decision.action is DiffAction.PURGE
    assert reason in decision.reason


# ---------------------------------------------------------------------- upserts


def test_a_new_item_is_processed() -> None:
    decision = _decide(None, upsert("HR-PL-04", "1.1"))
    assert (decision.action, decision.reason) == (DiffAction.PROCESS, "new: never processed")
    assert decision.previous_version is None


def test_an_item_back_after_a_delete_is_processed() -> None:
    decision = _decide(TOMBSTONE, upsert("HR-PL-04", "2.1", content_hash=LIVE.content_hash))
    assert decision.action is DiffAction.PROCESS
    assert "back after a delete" in decision.reason


def test_changed_content_is_processed() -> None:
    decision = _decide(LIVE, upsert("HR-PL-04", "2.1"))
    assert decision.action is DiffAction.PROCESS
    assert decision.reason.startswith("content changed: hash ")
    assert decision.previous_version == "1.1"


def test_an_upsert_without_a_hash_is_processed() -> None:
    decision = _decide(LIVE, upsert("HR-PL-04", "2.1", content_hash=None))
    assert decision.action is DiffAction.PROCESS
    assert "-> none" in decision.reason


def test_content_never_processed_is_processed_even_with_a_known_hash() -> None:
    """An ACL change for an unknown item leaves a live record with a hash but no original.
    The upsert that brings the original has that same hash, and must not be skipped."""
    first = acl_change("HR-PL-04", "1.1", ALL_STAFF, content_hash="sha256:HR-PL-04-1.1")
    previous = _processed(first)
    assert previous.raw_pointer is None
    decision = _decide(previous, upsert("HR-PL-04", "1.1"))
    assert decision.action is DiffAction.PROCESS
    assert "never processed" in decision.reason


def test_same_content_with_other_readers_takes_the_fast_lane() -> None:
    event = upsert("HR-PL-04", "1.2", content_hash=LIVE.content_hash, acl=HR_TEAM)
    decision = _decide(LIVE, event)
    assert decision.action is DiffAction.ACL_ONLY
    assert decision.reason == "same content, readers changed (+hr-team -all-staff)"


@pytest.mark.parametrize("acl", [ALL_STAFF, HR_TEAM])
def test_same_content_about_another_person_is_processed(acl: Acl) -> None:
    previous = _processed(upsert("employees/E-1001", "t1", "hr_api", person_id="alice"))
    event = upsert(
        "employees/E-1001",
        "t2",
        "hr_api",
        content_hash=previous.content_hash,
        person_id="bob",
        acl=acl,
    )
    decision = _decide(previous, event)
    assert decision.action is DiffAction.PROCESS
    assert decision.reason == "same content, now about bob (was alice)"


def test_a_repeat_is_skipped() -> None:
    decision = _decide(LIVE, upsert("HR-PL-04", "1.1"))
    assert (decision.action, decision.reason) == (
        DiffAction.SKIP,
        "repeat: v1.1 was already processed",
    )


def test_a_new_version_with_the_same_text_is_skipped() -> None:
    """The docs tool raises the version on every save, even with identical text."""
    event = upsert("HR-PL-04", "2.1", content_hash=LIVE.content_hash)
    decision = _decide(LIVE, event)
    assert decision.action is DiffAction.SKIP
    assert decision.reason == "new version, same content and readers as v1.1"


# ------------------------------------------------------------------ ACL changes


def test_an_acl_change_on_a_deleted_item_is_skipped() -> None:
    decision = _decide(TOMBSTONE, acl_change("HR-PL-04", "1.2", HR_TEAM))
    assert decision.action is DiffAction.SKIP
    assert "don't bring it back" in decision.reason


def test_an_acl_change_with_the_same_readers_is_skipped() -> None:
    event = acl_change("HR-PL-04", "1.2", ALL_STAFF, content_hash=LIVE.content_hash)
    decision = _decide(LIVE, event)
    assert (decision.action, decision.reason) == (DiffAction.SKIP, "readers unchanged")


def test_a_revocation_takes_the_fast_lane() -> None:
    """Revocation gate: narrowing the readers is never skipped."""
    previous = _processed(upsert("HR-PL-05", "1.1", acl=HR_AND_ES))
    event = acl_change("HR-PL-05", "1.2", ES, content_hash=previous.content_hash)
    decision = _decide(previous, event)
    assert decision.action is DiffAction.ACL_ONLY
    assert decision.reason == "readers changed (-hr-team)"


def test_an_acl_change_that_reports_new_content_still_takes_the_fast_lane() -> None:
    event = acl_change("HR-PL-04", "2.2", HR_TEAM, content_hash="sha256:other")
    decision = _decide(LIVE, event)
    assert decision.action is DiffAction.ACL_ONLY
    assert decision.reason.endswith("the content changed too, the full listing fetches it")


def test_same_readers_but_new_content_reported_is_skipped_and_says_why() -> None:
    event = acl_change("HR-PL-04", "2.2", ALL_STAFF, content_hash="sha256:other")
    decision = _decide(LIVE, event)
    assert decision.action is DiffAction.SKIP
    assert decision.reason == (
        "readers unchanged; the content changed too, the full listing fetches it"
    )


def test_an_acl_change_for_an_unknown_item_applies_its_readers() -> None:
    decision = _decide(None, acl_change("HR-PL-04", "1.2", HR_TEAM))
    assert decision.action is DiffAction.ACL_ONLY
    assert "the full listing fetches its content" in decision.reason


# ---------------------------------------------------------------------- helpers


def test_readers_diff_names_who_gained_and_who_lost_access() -> None:
    before = Acl(users=frozenset({"alice"}), groups=frozenset({"hr-team"}))
    after = Acl(users=frozenset({"bob"}), groups=frozenset({"hr-team", "it"}))
    assert readers_diff(before, after) == "+it +user:bob -user:alice"
    assert readers_diff(after, after) == "no change"
