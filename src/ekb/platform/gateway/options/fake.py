"""Offline, deterministic model client for tests, CI and ``ekb demo gateway --fake``.

It has no language skill: it echoes the start of the prompt's last line. Asked for
JSON, it fills the schema in: the first value of an enum, an empty list, zero, and
``"<model> <field>"`` for a string, so a step that checks its reply against the
schema (S8) accepts it. It exists so the gateway's routing, cache, quotas and
failover can be tested without a network, an API key or a quota, and so the ``ci``
profile never calls a real model.

``fail`` scripts failures: each call takes the next kind from it and raises it,
which is how the tests (and the demo) show what a 429 or an outage does.

:class:`FakeEmbeddingClient` (S10) does the same for vectors: a SHA-256 of each
text, spread over the declared dimensions and scaled to length 1. Two texts get
the same vector only if they are identical; meaning plays no part.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Iterable, Sequence
from typing import Any

from ekb.platform.gateway.errors import ModelCallError
from ekb.platform.gateway.models import ChatRequest, EmbeddingReply, FailureKind, ModelReply
from ekb.platform.registry import register

_WORD = re.compile(r"\w+|[^\w\s]")
ECHO_WORDS = 12


def _words(text: str) -> list[str]:
    return _WORD.findall(text)


@register("model_client", "fake")
class FakeClient:
    def __init__(
        self,
        model: str = "fake-small",
        *,
        fail: Iterable[FailureKind] = (),
        missing: str | None = None,
    ) -> None:
        self._model = model
        self._fail = list(fail)
        self._missing = missing
        self.calls = 0
        """How many requests reached this client (cache hits never do)."""

    @property
    def candidate(self) -> str:
        return f"fake:{self._model}"

    def check(self) -> str | None:
        return self._missing

    def fail_next(self, *kinds: FailureKind) -> None:
        self._fail.extend(kinds)

    def invoke(self, request: ChatRequest) -> ModelReply:
        self.calls += 1
        if self._fail:
            kind = self._fail.pop(0)
            raise ModelCallError(kind, f"FakeClient scripted failure ({kind})")
        if request.json_schema is not None:
            text = json.dumps(_fill(request.json_schema, "", self._model), ensure_ascii=False)
        else:
            last = request.prompt.strip().splitlines()[-1] if request.prompt.strip() else ""
            text = f"{self._model} says: {' '.join(last.split()[:ECHO_WORDS])}"
        output = _words(text)
        return ModelReply(
            text=text,
            input_tokens=len(_words(request.system)) + len(_words(request.prompt)),
            output_tokens=min(len(output), request.max_output_tokens),
            served_model=self._model,
        )


def _fill(schema: dict[str, Any], name: str, model: str) -> object:
    """A value that matches ``schema``: deterministic, and obviously not a model's."""
    if "enum" in schema:
        return schema["enum"][0]
    kind = schema.get("type", "object" if "properties" in schema else "string")
    if kind == "object":
        properties: dict[str, dict[str, Any]] = schema.get("properties", {})
        return {key: _fill(sub, key, model) for key, sub in properties.items()}
    if kind == "array":
        return []
    if kind in ("integer", "number"):
        return 0
    if kind == "boolean":
        return False
    return f"{model} {name}"


@register("embedding_client", "fake")
class FakeEmbeddingClient:
    """An offline embedding model: deterministic vectors with no meaning (S10).

    ``dimensions`` is the size it returns; ``wrong_size`` makes it return another,
    to test that the gateway refuses vectors of the wrong size.
    """

    def __init__(
        self,
        model: str = "fake-embed",
        *,
        dimensions: int = 32,
        fail: Iterable[FailureKind] = (),
        missing: str | None = None,
        wrong_size: int | None = None,
    ) -> None:
        if dimensions <= 0:
            raise ValueError("dimensions must be positive")
        self._model = model
        self._dimensions = dimensions
        self._fail = list(fail)
        self._missing = missing
        self._wrong_size = wrong_size
        self.calls = 0
        """How many requests reached this client (cache hits never do)."""
        self.sent: list[list[str]] = []
        """The texts of each request, in order: what the cache did not answer."""

    @property
    def candidate(self) -> str:
        return f"fake:{self._model}"

    def check(self) -> str | None:
        return self._missing

    def fail_next(self, *kinds: FailureKind) -> None:
        self._fail.extend(kinds)

    def embed(self, texts: Sequence[str]) -> EmbeddingReply:
        self.calls += 1
        self.sent.append(list(texts))
        if self._fail:
            kind = self._fail.pop(0)
            raise ModelCallError(kind, f"FakeEmbeddingClient scripted failure ({kind})")
        size = self._wrong_size or self._dimensions
        return EmbeddingReply(
            vectors=tuple(hashed_vector(text, size) for text in texts),
            input_tokens=sum(len(_words(text)) for text in texts),
            served_model=self._model,
        )


def hashed_vector(text: str, dimensions: int) -> tuple[float, ...]:
    """A unit-length vector made from SHA-256 digests of ``text``: same text, same vector."""
    values: list[float] = []
    counter = 0
    while len(values) < dimensions:
        digest = hashlib.sha256(f"{counter}:{text}".encode()).digest()
        values.extend((byte - 127.5) / 127.5 for byte in digest)
        counter += 1
    vector = values[:dimensions]
    norm = math.sqrt(sum(v * v for v in vector)) or 1.0
    return tuple(v / norm for v in vector)
