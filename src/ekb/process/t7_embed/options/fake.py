"""Deterministic, offline embedder for unit tests and CI.

It has no semantic quality: two texts are similar only if they are identical.
It exists so the pipeline, permission gates and deletion tests can run in CI
without a model, an API key or a GPU. Its ID says so (``fake@1(...)``), so a
chunk embedded by it is never mistaken for one embedded by a real model.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence

from ekb.platform.registry import register
from ekb.process.t7_embed.input import INPUT_VERSION
from ekb.process.t7_embed.port import Vectors

VERSION = 1


@register("t7_embed", "fake")
class FakeEmbedder:
    def __init__(self, dimensions: int = 32) -> None:
        if dimensions <= 0:
            raise ValueError("dimensions must be positive")
        self._dimensions = dimensions

    @property
    def model_id(self) -> str:
        return f"fake@{VERSION}({INPUT_VERSION}, sha256, {self._dimensions})"

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def embed(self, texts: Sequence[str], *, document_key: str | None = None) -> Vectors:
        return Vectors(
            vectors=tuple(self._embed_one(text) for text in texts),
            model_id=self.model_id,
            sent=len(texts),
            note="fake vectors (a SHA-256 of each text): no model, no meaning",
        )

    def _embed_one(self, text: str) -> tuple[float, ...]:
        values: list[float] = []
        counter = 0
        while len(values) < self._dimensions:
            digest = hashlib.sha256(f"{counter}:{text}".encode()).digest()
            values.extend((byte - 127.5) / 127.5 for byte in digest)
            counter += 1
        vector = values[: self._dimensions]
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return tuple(v / norm for v in vector)
