"""Offline, deterministic model client for tests, CI and ``ekb demo gateway --fake``.

It has no language skill: it echoes the start of the prompt's last line. It exists so the
gateway's routing, cache, quotas and failover can be tested without a network,
an API key or a quota, and so the ``ci`` profile never calls a real model.

``fail`` scripts failures: each call takes the next kind from it and raises it,
which is how the tests (and the demo) show what a 429 or an outage does.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable

from ekb.platform.gateway.errors import ModelCallError
from ekb.platform.gateway.models import ChatRequest, FailureKind, ModelReply
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
            properties = request.json_schema.get("properties", {})
            text = json.dumps({name: f"{self._model} {name}" for name in properties})
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
