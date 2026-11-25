"""Q4 · ``source_recheck`` (S14): nothing leaves unless the source says yes, now.

The gates: a denied, deleted or unanswered document is held back; so is one the
check forgot or answered for another document, and everything when the check
itself fails. What is held back is never named in anything the asker sees.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import pytest

from ekb.access.query.q4_postcheck.options.redact_none import NoRedactor
from ekb.access.query.q4_postcheck.options.redact_rules import RulesRedactor
from ekb.access.query.q4_postcheck.options.source_recheck import SourceRecheck
from ekb.contracts import (
    AccessVerdict,
    Chunk,
    Hit,
    PiiKind,
    Redaction,
    Scope,
    SourceAccess,
)
from tests.unit.stores.search.test_ranking import ALICE, chunk

ALLOWED, DENIED, DELETED, UNAVAILABLE = (
    AccessVerdict.ALLOWED,
    AccessVerdict.DENIED,
    AccessVerdict.DELETED,
    AccessVerdict.UNAVAILABLE,
)
AS_ALICE = Scope(user_id="alice", principals=ALICE, reason="reads as alice", prefilter_id="acl@1")
NOBODY = Scope(user_id="olivia", principals=None, reason="reads nothing", prefilter_id="acl@1")
IBAN = "IT60 X054 2811 1010 0000 0123 456"


class Source:
    """An access check answering from a table, recording every question it is asked."""

    checker_id = "table@1"

    def __init__(self, verdicts: Mapping[str, AccessVerdict], **hashes: str) -> None:
        self.verdicts = verdicts
        self.hashes = hashes
        self.asked: list[tuple[str, tuple[str, ...]]] = []

    def check(self, user_id: str, document_keys: Sequence[str]) -> Mapping[str, SourceAccess]:
        self.asked.append((user_id, tuple(document_keys)))
        return {
            key: SourceAccess(
                document_key=key,
                verdict=self.verdicts[key],
                reason=f"{self.verdicts[key].value} by the table",
                title=f"Title of {key.partition(':')[2]}" if self.verdicts[key] is ALLOWED else "",
                content_hash=self.hashes.get(key.partition(":")[2]),
                first_rev=1,
                checker_id=self.checker_id,
            )
            for key in document_keys
            if key in self.verdicts
        }


def hits(*chunks: Chunk) -> tuple[Hit, ...]:
    return tuple(Hit(chunk=c, rank=n, score=1 / (60 + n)) for n, c in enumerate(chunks, start=1))


A1 = chunk("hr_docs:A", "annual leave: 25 days", section=("Title of A", "How much"))
A2 = chunk("hr_docs:A", "carry over 5 days", position=1, section=("Title of A", "Carry"))
B1 = chunk("hr_docs:B", "remote work two days")
C1 = chunk("hr_docs:C", "travel 2024")
D1 = chunk("hr_docs:D", "salary bands")


def recheck(source: Source) -> SourceRecheck:
    return SourceRecheck(source, RulesRedactor())


def test_confirmed_documents_are_released_in_rank_order_with_numbered_citations() -> None:
    source = Source({"hr_docs:A": ALLOWED, "hr_docs:B": ALLOWED})
    released = recheck(source).release("leave?", AS_ALICE, hits(A1, B1, A2))

    assert [p.citation.chunk_id for p in released.passages] == [
        "hr_docs:A#0",
        "hr_docs:B#0",
        "hr_docs:A#1",
    ]
    first = released.passages[0]
    assert first.citation.number == 1
    assert first.citation.title == "Title of A"  # as the source gave it now
    assert first.citation.section == ("Title of A", "How much")
    assert first.citation.document_version == "1.1"
    assert first.citation.label == "[1] A · Title of A > How much"  # the title is not repeated
    assert (first.text, first.redactions) == ("annual leave: 25 days", ())
    assert released.withheld == ()
    assert released.notes == ()
    assert released.postcheck_id == "source_recheck@1(access=table@1, redact=rules@1)"


def test_each_document_is_asked_about_once_for_the_person_asking() -> None:
    source = Source({"hr_docs:A": ALLOWED, "hr_docs:B": ALLOWED})
    recheck(source).release("leave?", AS_ALICE, hits(A1, B1, A2))
    assert source.asked == [("alice", ("hr_docs:A", "hr_docs:B"))]


def test_denied_deleted_and_unanswered_documents_are_held_back_and_never_named() -> None:
    """The gate: only a yes from the source releases text (D8, card 4.13)."""
    source = Source(
        {"hr_docs:A": ALLOWED, "hr_docs:B": DENIED, "hr_docs:C": DELETED, "hr_docs:D": UNAVAILABLE}
    )
    released = recheck(source).release("q", AS_ALICE, hits(B1, A1, C1, D1, A2))

    assert [p.citation.chunk_id for p in released.passages] == ["hr_docs:A#0", "hr_docs:A#1"]
    assert all({"rank", "score"}.isdisjoint(p.model_dump()) for p in released.passages)
    assert [p.citation.number for p in released.passages] == [1, 2]  # no gaps
    assert [(w.hit.chunk.chunk_id, w.access.verdict) for w in released.withheld] == [
        ("hr_docs:B#0", DENIED),
        ("hr_docs:C#0", DELETED),
        ("hr_docs:D#0", UNAVAILABLE),
    ]
    assert released.unchecked == 1
    assert released.notes == (
        "1 passage could not be checked against the source, which did not answer: "
        "the answer may be incomplete",
    )
    assert [(a.document_key, a.verdict) for a in released.checked] == [
        ("hr_docs:B", DENIED),
        ("hr_docs:A", ALLOWED),
        ("hr_docs:C", DELETED),
        ("hr_docs:D", UNAVAILABLE),
    ]
    agent_side = released.model_dump_json(exclude={"withheld", "checked"})
    for held_back in ("hr_docs:B", "hr_docs:C", "hr_docs:D", "remote work", "salary"):
        assert held_back not in agent_side


def test_an_answer_missing_or_for_another_document_counts_as_no_answer() -> None:
    class Sloppy(Source):
        def check(self, user_id: str, document_keys: Sequence[str]) -> Mapping[str, SourceAccess]:
            answer = SourceAccess(document_key="hr_docs:B", verdict=ALLOWED, reason="yes")
            return {"hr_docs:A": answer}  # nothing for C; A answered with B's key

    released = recheck(Sloppy({})).release("q", AS_ALICE, hits(A1, C1))
    assert released.passages == ()
    assert [w.access.reason for w in released.withheld] == [
        "the access check answered for another document",
        "the access check gave no answer for this document",
    ]
    assert released.unchecked == 2
    assert released.notes[0].startswith("2 passages could not be checked")


def test_a_check_that_fails_releases_nothing() -> None:
    class Broken(Source):
        def check(self, user_id: str, document_keys: Sequence[str]) -> Mapping[str, SourceAccess]:
            raise RuntimeError("bug")

    released = recheck(Broken({})).release("q", AS_ALICE, hits(A1, B1))
    assert released.passages == ()
    assert {w.access.verdict for w in released.withheld} == {UNAVAILABLE}
    assert released.withheld[0].access.reason == "the access check failed (RuntimeError): held back"


def test_nothing_is_asked_for_someone_who_reads_nothing_or_for_no_hits() -> None:
    source = Source({"hr_docs:A": ALLOWED})
    assert recheck(source).release("q", NOBODY, hits(A1)).passages == ()
    assert recheck(source).release("q", AS_ALICE, ()).passages == ()
    assert source.asked == []


def test_a_source_changed_since_indexing_is_released_and_marked() -> None:
    indexed = A1.model_copy(update={"source_content_hash": "sha256:old"})
    current = B1.model_copy(update={"source_content_hash": "sha256:b"})
    unknown = C1  # indexed without a hash: nothing to compare
    source = Source(
        {"hr_docs:A": ALLOWED, "hr_docs:B": ALLOWED, "hr_docs:C": ALLOWED},
        A="sha256:new",
        B="sha256:b",
    )
    released = recheck(source).release("q", AS_ALICE, hits(indexed, current, unknown))
    assert [p.citation.source_changed for p in released.passages] == [True, False, False]
    assert released.notes == (
        "the source of [1] has changed since it was indexed: the passage may be out of date "
        "until it is indexed again",
    )


def test_personal_data_is_removed_from_text_trail_and_title_but_not_from_the_index() -> None:
    page = chunk("hr_docs:P", f"Sent again to IBAN {IBAN}.", section=(f"Paid to {IBAN}",))
    released = recheck(Source({"hr_docs:P": ALLOWED})).release("q", AS_ALICE, hits(page))
    passage = released.passages[0]
    assert passage.text == "Sent again to IBAN [IBAN removed]."
    assert passage.citation.section == ("Paid to [IBAN removed]",)
    assert passage.redactions == (Redaction(kind=PiiKind.IBAN, count=2),)
    assert IBAN not in released.model_dump_json()
    assert IBAN in page.text  # what is released changes, not what is stored

    kept = SourceRecheck(Source({"hr_docs:P": ALLOWED}), NoRedactor())
    assert kept.release("q", AS_ALICE, hits(page)).passages[0].text == page.text


@pytest.mark.parametrize("verdict", [DENIED, DELETED, UNAVAILABLE])
def test_no_verdict_but_allowed_releases(verdict: AccessVerdict) -> None:
    released = recheck(Source({"hr_docs:A": verdict})).release("q", AS_ALICE, hits(A1))
    assert released.passages == ()
    assert not verdict.released
    assert ALLOWED.released
