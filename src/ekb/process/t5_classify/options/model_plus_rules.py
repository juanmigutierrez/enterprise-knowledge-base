"""T5 option ``model_plus_rules``: the rules' labels, made stricter by a model where it disagrees.

The configured default (card 4.9's starting choice). The rules run first and
always answer: explicit markers, personal data patterns, the source's
department. Then a model, through the gateway's ``classify`` task, labels the
same chunks in **one call per document** (D16 #4), and the two are combined
chunk by chunk:

- **sensitivity:** the stricter of the two. A model can raise a chunk (it reads
  "the salary of Alice Rossi is ..." as confidential where no pattern fires),
  never lower one below what the rules found;
- **personal data:** everything either found. A high-risk kind found only by
  the model still makes the chunk confidential;
- **department:** the rules' (the source's label first); the model's only when
  the rules have none.

If no model can answer (no key, quotas used up) or the reply is not the JSON
asked for, the rules' labels stand and the note says so: a model outage makes
labels less sharp, never missing. A chunk the reply leaves out keeps its rules
labels too.
"""

from __future__ import annotations

import json
from typing import Any

from ekb.contracts import Chunk, ParsedDocument, PiiKind, Sensitivity
from ekb.platform.gateway.errors import GatewayError
from ekb.platform.gateway.port import Completer
from ekb.platform.registry import register
from ekb.process.t5_classify import rules
from ekb.process.t5_classify.port import DEPARTMENTS, Classification, Labels, passage

VERSION = 1
PROMPT_VERSION = "classify@2"
"""Part of the cache key. Bump it when the prompt or the way the reply is read changes."""
TASK = "classify"
NO_DEPARTMENT = "none"

SYSTEM = f"""\
You label the passages of one company document for an access-control system.
First choose the department the document belongs to: {", ".join(DEPARTMENTS)}, or \
{NO_DEPARTMENT} if none fits.
Then, for every passage, choose its sensitivity:
- public: meant for people outside the company;
- internal: for every employee;
- confidential: personal data about a person, or business information only some \
employees need;
- restricted: marked for a named group only, or pay and performance data about people.
When unsure, choose the stricter level.
Also list the kinds of personal data each passage contains, from: \
{", ".join(kind.value for kind in PiiKind)}. An empty list if there is none.
Reply with JSON only."""


def key(position: int) -> str:
    return f"chunk_{position}"


@register("t5_classify", "model_plus_rules")
class ModelPlusRulesClassifier:
    def __init__(self, gateway: Completer, *, task: str = TASK) -> None:
        self._gateway = gateway
        self._task = task

    @property
    def classifier_id(self) -> str:
        return _classifier_id(None)

    def classify(self, document: ParsedDocument, chunks: tuple[Chunk, ...]) -> Classification:
        base, note = rules.labels(document, chunks)
        if not chunks:
            return Classification(base, self.classifier_id, note)
        try:
            reply = self._gateway.complete(
                self._task,
                prompt(document, chunks),
                system=SYSTEM,
                prompt_version=PROMPT_VERSION,
                max_output_tokens=min(4096, 256 + 64 * len(chunks)),
                json_schema=schema(len(chunks)),
                document_key=document.document_key,
            )
        except GatewayError as exc:
            return self._rules_only(base, note, f"no model could answer ({_short(str(exc))})")
        parsed = _read(reply.text)
        if parsed is None:
            return self._rules_only(
                base, note, f"{reply.candidate}'s reply was not the JSON asked for"
            )
        department, per_chunk = parsed
        combined = tuple(
            combine(labels, per_chunk.get(position), department)
            for position, labels in enumerate(base)
        )
        how = "from the cache, no request" if reply.cached else "one request"
        answered = sum(1 for position in range(len(base)) if position in per_chunk)
        note += f"; model {reply.candidate} ({how}) labelled {answered} of {len(base)} chunks"
        return Classification(combined, _classifier_id(reply.candidate), note)

    def _rules_only(self, base: tuple[Labels, ...], note: str, why: str) -> Classification:
        classifier_id = _classifier_id("no model")
        return Classification(base, classifier_id, f"{note}; rules only: {why}")


def _classifier_id(model: str | None) -> str:
    """``model_plus_rules@1(classify@2, groq:openai/gpt-oss-20b, rules@1)``; without a
    model name, the option as configured."""
    parts = [PROMPT_VERSION, *([model] if model else []), f"rules@{rules.VERSION}"]
    return f"model_plus_rules@{VERSION}({', '.join(parts)})"


ModelLabels = tuple[Sensitivity, tuple[PiiKind, ...]]


def combine(labels: Labels, model: ModelLabels | None, department: str | None) -> Labels:
    """The rules' labels, made stricter by the model's where it disagrees."""
    name = labels.department or department
    if model is None:
        return Labels(labels.sensitivity, name, labels.pii, labels.why)
    level, kinds = model
    found = set(labels.pii) | set(kinds)
    pii = tuple(kind for kind in PiiKind if kind in found)
    sensitivity, why = labels.sensitivity, labels.why
    if level.rank > sensitivity.rank:
        sensitivity, why = level, f"{level}: the model said so (rules: {labels.sensitivity})"
    risky = [kind.value for kind in pii if kind.high_risk and kind not in labels.pii]
    if risky and Sensitivity.CONFIDENTIAL.rank > sensitivity.rank:
        sensitivity = Sensitivity.CONFIDENTIAL
        why = f"confidential: the model found {', '.join(risky)} in this chunk"
    return Labels(sensitivity, name, pii, why)


def _read(text: str) -> tuple[str | None, dict[int, ModelLabels]] | None:
    """The model's department and its labels per chunk position. ``None`` if the reply
    is not a JSON object; a chunk whose labels don't parse is simply left out."""
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    department = data.get("department")
    name = department if isinstance(department, str) and department in DEPARTMENTS else None
    per_chunk: dict[int, ModelLabels] = {}
    for name_key, value in data.items():
        if not name_key.startswith("chunk_") or not isinstance(value, dict):
            continue
        try:
            position = int(name_key.removeprefix("chunk_"))
            level = Sensitivity(str(value.get("sensitivity")))
            kinds = tuple(PiiKind(str(kind)) for kind in value.get("pii") or ())
        except (ValueError, TypeError):
            continue
        per_chunk[position] = (level, kinds)
    return name, per_chunk


def prompt(document: ParsedDocument, chunks: tuple[Chunk, ...]) -> str:
    passages = "\n\n".join(
        f"<{key(chunk.position)}>\n{passage(chunk)}\n</{key(chunk.position)}>" for chunk in chunks
    )
    return (
        f'<document title="{document.title}">\n{document.text}\n</document>\n\n'
        f"Label each of these {len(chunks)} passages of the document:\n\n{passages}"
    )


def schema(count: int) -> dict[str, Any]:
    passage = {
        "type": "object",
        "properties": {
            "sensitivity": {"type": "string", "enum": [level.value for level in Sensitivity]},
            "pii": {
                "type": "array",
                "items": {"type": "string", "enum": [k.value for k in PiiKind]},
            },
        },
        "required": ["sensitivity", "pii"],
    }
    return {
        "title": "classification",
        "description": "The document's department and each passage's labels.",
        "type": "object",
        "properties": {
            "department": {"type": "string", "enum": [NO_DEPARTMENT, *DEPARTMENTS]},
            **{key(position): passage for position in range(count)},
        },
        "required": ["department", *(key(position) for position in range(count))],
    }


def _short(text: str, limit: int = 300) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"
