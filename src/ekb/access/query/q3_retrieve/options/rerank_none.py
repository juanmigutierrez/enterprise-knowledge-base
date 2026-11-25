"""``q3_rerank: none``: keep the fused order (S13).

A reranker reads the question with each candidate passage (a cross-encoder such
as bge-reranker-v2-m3 or Cohere Rerank) and reorders them; its value is letting
fewer, better chunks go to the answering model (card 4.6, with 4.7). None is
built yet: Ollama serves no reranking models officially, and a local
cross-encoder needs its own runtime. Until card 4.6 decides, the configured
reranker (``bge_reranker`` locally) runs as this stand-in, and the swap is
printed (``ekb.app.choices``).
"""

from __future__ import annotations

from collections.abc import Sequence

from ekb.contracts import Hit
from ekb.platform.registry import register

VERSION = 1


@register("q3_rerank", "none")
class NoReranker:
    @property
    def reranker_id(self) -> str:
        return f"none@{VERSION}"

    def rerank(self, question: str, hits: Sequence[Hit]) -> tuple[Hit, ...]:
        return tuple(hits)
