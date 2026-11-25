"""T5's ``model_plus_rules`` (S8): the rules' labels, made stricter by a model, never laxer."""

from __future__ import annotations

import json

import pytest

from ekb.contracts import PiiKind, Sensitivity
from ekb.platform.gateway.errors import NoModelAvailableError
from ekb.process.t5_classify.options.model_plus_rules import ModelPlusRulesClassifier, schema
from tests.unit.platform.gateway.helpers import Scripted, make_config, make_world
from tests.unit.process.t5_classify.conftest import cut, document, synthetic

BODY = "# Team news\n\n## Moves\nAlice Rossi moves to Madrid.\n\n## Lunch\nPizza on Friday.\n"


def _reply(department: str = "none", **chunks: dict[str, object]) -> str:
    return json.dumps({"department": department, **chunks})


def test_the_model_can_raise_a_chunk_never_lower_it() -> None:
    reply = _reply(
        chunk_0={"sensitivity": "confidential", "pii": ["person_name"]},
        chunk_1={"sensitivity": "public", "pii": []},  # laxer than the rules: ignored
    )
    doc = document(BODY)
    moves, lunch = ModelPlusRulesClassifier(Scripted(reply)).classify(doc, cut(doc)).labels
    assert (moves.sensitivity, moves.pii) == (Sensitivity.CONFIDENTIAL, (PiiKind.PERSON_NAME,))
    assert moves.why == "confidential: the model said so (rules: internal)"
    assert lunch.sensitivity is Sensitivity.INTERNAL
    assert lunch.why == "internal: no marker and no high-risk personal data"


def test_classify_the_header_with_the_passage_that_will_display_it() -> None:
    doc = document(BODY)
    moves, lunch = cut(doc)
    header = "Payroll account ES9121000418450200051332"
    headed = lunch.model_copy(update={"context_header": header})
    model = Scripted(_reply(chunk_1={"sensitivity": "public", "pii": []}))
    labels = ModelPlusRulesClassifier(model).classify(doc, (moves, headed)).labels
    assert labels[0].sensitivity is Sensitivity.INTERNAL
    assert (labels[1].sensitivity, labels[1].pii) == (Sensitivity.CONFIDENTIAL, (PiiKind.IBAN,))
    assert f"<chunk_1>\n{header}\n\n{lunch.text}\n</chunk_1>" in model.asked[0]["prompt"]


def test_a_restricted_page_stays_restricted_whatever_the_model_says() -> None:
    doc = synthetic("HR-CMP-01")
    reply = json.dumps(
        {"department": "none"}
        | {f"chunk_{i}": {"sensitivity": "public", "pii": []} for i in range(4)}
    )
    labels = ModelPlusRulesClassifier(Scripted(reply)).classify(doc, cut(doc)).labels
    assert {item.sensitivity for item in labels} == {Sensitivity.RESTRICTED}


def test_high_risk_data_only_the_model_found_still_makes_the_chunk_confidential() -> None:
    reply = _reply(chunk_0={"sensitivity": "internal", "pii": ["it_fiscal_code"]})
    doc = document(BODY)
    moves, _ = ModelPlusRulesClassifier(Scripted(reply)).classify(doc, cut(doc)).labels
    assert moves.sensitivity is Sensitivity.CONFIDENTIAL
    assert moves.why == "confidential: the model found it_fiscal_code in this chunk"


def test_personal_data_is_everything_either_found() -> None:
    body = "# Payroll\n\nWrite to payroll@example.com.\n"
    reply = _reply(chunk_0={"sensitivity": "internal", "pii": ["person_name", "email"]})
    doc = document(body)
    (labels,) = ModelPlusRulesClassifier(Scripted(reply)).classify(doc, cut(doc)).labels
    assert labels.pii == (PiiKind.EMAIL, PiiKind.PERSON_NAME)


def test_the_department_is_the_source_s_and_the_model_s_only_when_there_is_none() -> None:
    reply = _reply("sales", chunk_0={"sensitivity": "internal", "pii": []})
    filed = document("# Note\n\nHello.\n", department="people")
    unfiled = document("# Note\n\nHello.\n", department=None)
    classifier = ModelPlusRulesClassifier(Scripted(reply))
    assert classifier.classify(filed, cut(filed)).labels[0].department == "people"
    assert classifier.classify(unfiled, cut(unfiled)).labels[0].department == "sales"
    unknown = ModelPlusRulesClassifier(Scripted(_reply("legal")))  # not a known department
    assert unknown.classify(unfiled, cut(unfiled)).labels[0].department is None


@pytest.mark.parametrize(
    ("scripted", "why"),
    [
        (Scripted(error=NoModelAvailableError("classify", (), None)), "no model could answer"),
        (Scripted("not json"), "was not the JSON asked for"),
        (Scripted("[1, 2]"), "was not the JSON asked for"),
    ],
)
def test_without_a_model_the_rules_labels_stand(scripted: Scripted, why: str) -> None:
    doc = synthetic("HR-CMP-01")
    result = ModelPlusRulesClassifier(scripted).classify(doc, cut(doc))
    assert {item.sensitivity for item in result.labels} == {Sensitivity.RESTRICTED}
    assert result.classifier_id == "model_plus_rules@1(classify@2, no model, rules@1)"
    assert "; rules only: " in result.note
    assert why in result.note


def test_a_chunk_the_reply_leaves_out_or_garbles_keeps_its_rules_labels() -> None:
    reply = _reply(
        chunk_0={"sensitivity": "secret", "pii": []},  # not a level
        chunk_9={"sensitivity": "restricted", "pii": []},  # no such chunk
    )
    doc = document(BODY)
    result = ModelPlusRulesClassifier(Scripted(reply)).classify(doc, cut(doc))
    assert {item.sensitivity for item in result.labels} == {Sensitivity.INTERNAL}
    assert "labelled 0 of 2 chunks" in result.note


def test_one_call_per_document_through_the_classify_task_cached_and_tagged() -> None:
    world = make_world(make_config(tasks={"classify": ("fake:one",)}))
    doc = synthetic("HR-PL-01")
    classifier = ModelPlusRulesClassifier(world.gateway)

    first = classifier.classify(doc, cut(doc))
    again = classifier.classify(doc, cut(doc))

    # The fake fills the schema in: department "none", every chunk "public", no PII,
    # so the rules decide every label.
    assert {item.sensitivity for item in first.labels} == {Sensitivity.INTERNAL}
    assert first.classifier_id == "model_plus_rules@1(classify@2, fake:one, rules@1)"
    assert "model fake:one (one request) labelled 4 of 4 chunks" in first.note
    assert "(from the cache, no request)" in again.note
    assert world.clients["fake:one"].calls == 1
    (record, hit) = world.log.records()
    assert (record.task, record.prompt_version, record.document_key) == (
        "classify",
        "classify@2",
        "hr_docs:HR-PL-01",
    )
    assert hit.outcome == "hit"
    assert world.gateway.forget("hr_docs:HR-PL-01") == 1  # deleted with the document


def test_the_schema_asks_for_a_department_and_every_chunk() -> None:
    asked = schema(2)
    assert asked["required"] == ["department", "chunk_0", "chunk_1"]
    passage = asked["properties"]["chunk_0"]
    assert passage["properties"]["sensitivity"]["enum"] == [
        "public",
        "internal",
        "confidential",
        "restricted",
    ]
    assert "person_name" in passage["properties"]["pii"]["items"]["enum"]
    assert asked["properties"]["department"]["enum"][0] == "none"


def test_no_chunks_no_call() -> None:
    scripted = Scripted()
    doc = document("# Only a title\n")
    assert ModelPlusRulesClassifier(scripted).classify(doc, ()).labels == ()
    assert scripted.asked == []
