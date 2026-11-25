"""T5's rules (S8): markers, personal data and the department, on made-up and synthetic pages."""

from __future__ import annotations

import pytest

from ekb.contracts import PiiKind, Sensitivity
from ekb.process.t5_classify import rules
from ekb.process.t5_classify.options.rules import RulesClassifier
from tests.unit.process.t4_chunk.conftest import PAGES
from tests.unit.process.t5_classify.conftest import cut, document, synthetic


def _levels(body: str, title: str = "Policy") -> list[Sensitivity]:
    doc = document(body, title=title)
    return [labels.sensitivity for labels in RulesClassifier().classify(doc, cut(doc)).labels]


def test_a_restricted_page_is_restricted_down_to_its_last_chunk() -> None:
    """HR-CMP-01 says it once, at the top; the review cycle never says it, and is restricted."""
    doc = synthetic("HR-CMP-01")
    result = RulesClassifier().classify(doc, cut(doc))
    assert [labels.sensitivity for labels in result.labels] == [Sensitivity.RESTRICTED] * 4
    assert result.labels[-1].why == "restricted: the document says “(restricted)”"
    assert result.classifier_id == "rules@1"


@pytest.mark.parametrize("page_id", sorted(p.id for p in PAGES if p.id != "HR-CMP-01"))
def test_every_other_synthetic_page_is_internal(page_id: str) -> None:
    doc = synthetic(page_id)
    labels = RulesClassifier().classify(doc, cut(doc)).labels
    assert {item.sensitivity for item in labels} == {Sensitivity.INTERNAL}
    assert {item.pii for item in labels} == {()}  # no personal data in the policies


@pytest.mark.parametrize(
    ("body", "title"),
    [
        ("# Pay\n\nRestricted to the compensation team.\n", "Pay"),
        ("# Pay\n\nDo not forward this page.\n", "Pay"),
        ("# Pay\n\nSome text.\n", "Pay bands (restricted)"),
        ("# Paga\n\nDocumento riservato al team HR. Non inoltrare.\n", "Paga"),
        ("# Sueldos\n\nAcceso restringido al equipo de RR. HH.\n", "Sueldos"),
        ("# Sueldos\n\nNo reenviar.\n", "Sueldos"),
    ],
)
def test_restriction_markers_in_three_languages(body: str, title: str) -> None:
    assert set(_levels(body, title)) == {Sensitivity.RESTRICTED}


@pytest.mark.parametrize(
    ("body", "title"),
    [
        ("# Plan\n\nConfidential: for managers.\n", "Plan"),
        ("# Plan\n\nText.\n", "Reorganisation plan (confidential)"),
        ("# Piano\n\nTesto.\n", "Piano [confidenziale]"),
    ],
)
def test_confidential_markers(body: str, title: str) -> None:
    assert set(_levels(body, title)) == {Sensitivity.CONFIDENTIAL}


@pytest.mark.parametrize(
    "body",
    [
        "# Conduct\n\n## Data and confidentiality\nShopper data are confidential.\n",
        "# Remote\n\nThe allowance is paid with the salary.\n",
        "# Access\n\nAccess to the portal is restricted after hours.\n",
    ],
)
def test_topic_words_are_not_markers(body: str) -> None:
    """The near misses of the synthetic pages: friction is a cost too (card 4.9)."""
    assert set(_levels(body)) == {Sensitivity.INTERNAL}


def test_high_risk_personal_data_raises_only_its_own_chunk() -> None:
    body = (
        "# Payroll\n\n## Contacts\nWrite to payroll@example.com or call +39 02 1234 5678.\n\n"
        "## Example\nPaid to IBAN IT60 X054 2811 1010 0000 0123 456.\n"
    )
    doc = document(body)
    contacts, example = RulesClassifier().classify(doc, cut(doc)).labels
    assert (contacts.sensitivity, contacts.pii) == (
        Sensitivity.INTERNAL,  # flagged, not raised: a team's address is not confidential
        (PiiKind.EMAIL, PiiKind.PHONE),
    )
    assert (example.sensitivity, example.pii) == (Sensitivity.CONFIDENTIAL, (PiiKind.IBAN,))
    assert example.why == "confidential: iban in this chunk"


def test_a_document_marker_beats_a_chunk_s_personal_data() -> None:
    body = "# Pay\n\nRestricted to HR.\n\n## Example\nIBAN IT60 X054 2811 1010 0000 0123 456.\n"
    assert set(_levels(body)) == {Sensitivity.RESTRICTED}


def test_public_is_never_guessed() -> None:
    assert set(_levels("# News\n\nPublic announcement: our new office opens.\n")) == {
        Sensitivity.INTERNAL
    }


def test_the_source_s_department_wins() -> None:
    doc = synthetic("HR-PL-06")  # expenses and travel, filed under finance by the docs tool
    labels = RulesClassifier().classify(doc, cut(doc))
    assert {item.department for item in labels.labels} == {"finance"}
    assert labels.note == "department finance (from the source)"
    filed = document("# Leave\n\nAnnual leave.\n", department="  People ")
    assert rules.department(filed) == ("people", "from the source")


@pytest.mark.parametrize(
    ("title", "body", "expected"),
    [
        ("Expenses policy", "Submit every receipt for reimbursement.", "finance"),
        ("Ferie", "Le ferie si chiedono nel portale.", "people"),
        ("Flyer processing", "How flyers are checked.", "operations"),
    ],
)
def test_keywords_name_the_department_when_the_source_has_none(
    title: str, body: str, expected: str
) -> None:
    doc = document(f"# {title}\n\n{body}\n", title=title, department=None)
    assert rules.department(doc) == (expected, "from keywords (the source has no label)")


def test_no_keyword_or_a_tie_means_no_department() -> None:
    nothing = document("# Note\n\nHello.\n", title="Note", department=None)
    assert rules.department(nothing) == (
        None,
        "none: the source has no label and no keyword matched",
    )
    tie = document("# Note\n\nleave and budget\n", title="Note", department=None)
    assert rules.department(tie) == (None, "none: keywords tie between people and finance")
