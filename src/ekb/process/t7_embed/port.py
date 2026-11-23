"""Port for T7 · Embed. Every embedding option implements this protocol."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable


@runtime_checkable
class Embedder(Protocol):
    """Turns chunk texts into vectors.

    ``model_id`` is stored next to every vector so a model change is detected
    and only the affected chunks are re-embedded (decisions.md D16 #8).
    """

    @property
    def model_id(self) -> str: ...

    @property
    def dimensions(self) -> int: ...

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...
