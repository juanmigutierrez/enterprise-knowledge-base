"""The T5 step (S8): labels go on the chunks, one set each, and nothing else changes."""

from __future__ import annotations

import pytest

from ekb.contracts import Acl, Chunk, ParsedDocument, PiiKind, Sensitivity
from ekb.process.t5_classify.options.rules import RulesClassifier
from ekb.process.t5_classify.port import Classification, Labels
from ekb.process.t5_classify.step import ClassifyError, ClassifyStep, summary
from tests.unit.process.t5_classify.conftest import cut, synthetic


class Fixed:
    """A classifier that returns whatever it is given."""

    classifier_id = "fixed@1"

    def __init__(self, *labels: Labels) -> None:
        self.labels = labels

    def classify(self, document: ParsedDocument, chunks: tuple[Chunk, ...]) -> Classification:
        return Classification(self.labels, self.classifier_id, "fixed")


def test_labels_go_on_the_chunks_and_nothing_else_changes() -> None:
    doc = synthetic("HR-CMP-01")
    chunks = cut(doc)
    result = ClassifyStep(RulesClassifier()).handle(doc, chunks)
    for before, after in zip(chunks, result.chunks, strict=True):
        assert (after.sensitivity, after.department, after.classifier_id) == (
            Sensitivity.RESTRICTED,
            "people",
            "rules@1",
        )
        reset = {"sensitivity": None, "department": None, "classifier_id": "", "pii": ()}
        assert after.model_copy(update=reset) == before
        assert not after.publishable  # a sensitivity alone opens no gate: no vector, no ACL
    assert result.reason == (
        "classified with rules@1: 4 restricted; department people (from the source)"
    )
    assert len(result.why) == 4


def test_pii_is_kept_once_each_in_a_fixed_order_and_the_department_lowercased() -> None:
    doc = synthetic("HR-PL-08")
    chunks = cut(doc)
    labels = [
        Labels(Sensitivity.INTERNAL, " People ", (PiiKind.EMAIL, PiiKind.IBAN, PiiKind.EMAIL))
    ] * len(chunks)
    out = ClassifyStep(Fixed(*labels)).handle(doc, chunks).chunks
    assert {chunk.pii for chunk in out} == {(PiiKind.IBAN, PiiKind.EMAIL)}
    assert {chunk.department for chunk in out} == {"people"}


def test_the_step_refuses_the_wrong_number_of_labels() -> None:
    doc = synthetic("HR-PL-01")
    with pytest.raises(ClassifyError, match="4 chunks but 1 labels from fixed@1"):
        ClassifyStep(Fixed(Labels(Sensitivity.INTERNAL))).handle(doc, cut(doc))


def test_the_step_refuses_labels_without_a_sensitivity() -> None:
    doc = synthetic("HR-PL-08")
    chunks = cut(doc)
    broken = [Labels(None)] * len(chunks)  # type: ignore[arg-type]  # a buggy classifier
    with pytest.raises(ClassifyError, match="no sensitivity"):
        ClassifyStep(Fixed(*broken)).handle(doc, chunks)


def test_the_step_refuses_chunks_from_another_document_or_already_past_t7_or_t8() -> None:
    doc, other = synthetic("HR-PL-01"), synthetic("HR-PL-02")
    step = ClassifyStep(RulesClassifier())
    with pytest.raises(ClassifyError, match="is from hr_docs:HR-PL-02"):
        step.handle(doc, cut(other))
    (first, *rest) = cut(doc)
    with pytest.raises(ClassifyError, match="T5 runs before T7 and T8"):
        step.handle(doc, (first.model_copy(update={"acl": Acl()}), *rest))
    with pytest.raises(ClassifyError, match="T5 runs before T7 and T8"):
        step.handle(doc, (first.model_copy(update={"vector": (0.1,)}), *rest))


def test_summary_counts_chunks_per_sensitivity() -> None:
    doc = synthetic("HR-PL-01")
    chunks = cut(doc)
    assert summary(chunks) == "4 unlabelled"
    labelled = ClassifyStep(RulesClassifier()).handle(doc, chunks).chunks
    mixed = (*labelled[:3], labelled[3].model_copy(update={"sensitivity": Sensitivity.RESTRICTED}))
    assert summary(mixed) == "3 internal, 1 restricted"
    assert summary(()) == "no chunks"
