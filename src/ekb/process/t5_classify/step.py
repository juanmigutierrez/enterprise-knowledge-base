"""Running T5 for one document's chunks, and checking what the classifier returned.

::

    chunks = T4's result                          # cut, with their headers
    result = classifier.classify(parsed, chunks)  # the option: rules, model_plus_rules
    chunks = labelled(parsed, chunks, result)     # one set of labels per chunk

T5 runs right after T4, on the same document, so only where T2 decided
``process``. A permission change (the fast lane) never re-labels a document: the
labels come from its content, which didn't change.

**The step checks, the classifier only answers.** Every chunk gets exactly one
set of labels, in order; each has a sensitivity (the publication gate needs it);
PII kinds are kept once each, in a fixed order. The classifier returns labels,
not chunks, so it cannot touch a chunk's text, header, ACL or vector. And T5
refuses chunks that already carry an ACL or a vector: it runs before T7 and T8,
so such a chunk means the steps were wired in the wrong order.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from ekb.contracts import Chunk, ParsedDocument, PiiKind, Sensitivity
from ekb.process.t5_classify.port import Classification, Classifier


@dataclass(frozen=True)
class ClassifyResult:
    document: ParsedDocument
    chunks: tuple[Chunk, ...]
    """T4's chunks with their labels."""
    classifier_id: str
    why: tuple[str, ...]
    """Per chunk, in plain words: why it got its sensitivity."""
    note: str
    reason: str


def summary(chunks: Sequence[Chunk]) -> str:
    """``3 internal, 1 confidential``: chunks per sensitivity, strictest last."""
    counts = Counter(chunk.sensitivity for chunk in chunks if chunk.sensitivity is not None)
    parts = [f"{counts[level]} {level}" for level in Sensitivity if counts[level]]
    unlabelled = sum(1 for chunk in chunks if chunk.sensitivity is None)
    if unlabelled:
        parts.append(f"{unlabelled} unlabelled")
    return ", ".join(parts) or "no chunks"


class ClassifyStep:
    """T5 with one classifier."""

    def __init__(self, classifier: Classifier) -> None:
        self._classifier = classifier

    @property
    def classifier_id(self) -> str:
        return self._classifier.classifier_id

    def handle(self, document: ParsedDocument, chunks: tuple[Chunk, ...]) -> ClassifyResult:
        check_input(document, chunks)
        result = self._classifier.classify(document, chunks)
        out = labelled(document, chunks, result)
        return ClassifyResult(
            document=document,
            chunks=out,
            classifier_id=result.classifier_id,
            why=tuple(labels.why for labels in result.labels),
            note=result.note,
            reason=f"classified with {result.classifier_id}: {summary(out)}; {result.note}",
        )


class ClassifyError(ValueError):
    """T5 was given chunks it must not label, or a classifier broke its promise."""


def check_input(document: ParsedDocument, chunks: Sequence[Chunk]) -> None:
    for chunk in chunks:
        where = f"{document.document_key} v{document.version}, {chunk.chunk_id}"
        if (chunk.document_key, chunk.document_version) != (
            document.document_key,
            document.version,
        ):
            raise ClassifyError(f"{where} is from {chunk.document_key} v{chunk.document_version}")
        if chunk.acl is not None or chunk.vector is not None:
            raise ClassifyError(f"{where} already has an ACL or a vector: T5 runs before T7 and T8")


def labelled(
    document: ParsedDocument, chunks: tuple[Chunk, ...], result: Classification
) -> tuple[Chunk, ...]:
    """``chunks`` with ``result``'s labels on them, after checking the labels."""
    if len(result.labels) != len(chunks):
        raise ClassifyError(
            f"{document.document_key} v{document.version}: {len(chunks)} chunks but "
            f"{len(result.labels)} labels from {result.classifier_id}"
        )
    out: list[Chunk] = []
    for chunk, labels in zip(chunks, result.labels, strict=True):
        if not isinstance(labels.sensitivity, Sensitivity):
            raise ClassifyError(f"{chunk.chunk_id}: no sensitivity from {result.classifier_id}")
        department = (labels.department or "").strip().lower() or None
        kinds = {PiiKind(kind) for kind in labels.pii}
        out.append(
            chunk.model_copy(
                update={
                    "department": department,
                    "sensitivity": labels.sensitivity,
                    "pii": tuple(kind for kind in PiiKind if kind in kinds),
                    "classifier_id": result.classifier_id,
                }
            )
        )
    return tuple(out)
