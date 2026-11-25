"""T5 option ``rules``: markers, personal data patterns and the source's department.

No model, no network: the ``ci`` profile's choice, and the floor that
``model_plus_rules`` starts from. The rules themselves are in
``ekb.process.t5_classify.rules``; changing them means a new ``VERSION``, so
chunks labelled by the old rules can be found and re-labelled.
"""

from __future__ import annotations

from ekb.contracts import Chunk, ParsedDocument
from ekb.platform.registry import register
from ekb.process.t5_classify import rules
from ekb.process.t5_classify.port import Classification


@register("t5_classify", "rules")
class RulesClassifier:
    @property
    def classifier_id(self) -> str:
        return f"rules@{rules.VERSION}"

    def classify(self, document: ParsedDocument, chunks: tuple[Chunk, ...]) -> Classification:
        labels, note = rules.labels(document, chunks)
        return Classification(labels=labels, classifier_id=self.classifier_id, note=note)
