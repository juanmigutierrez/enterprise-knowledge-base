"""``t7_embed: model``: vectors from the model gateway's ``embed`` task (S10, card 4.4).

The option does not choose a model: the gateway's configuration does
(``tasks: embed`` in ``config/*.yaml``). Locally that is bge-m3 on Ollama,
multilingual, 1024 numbers per vector, free; in the production design, Cohere
Embed v4 on Bedrock. An embedding task routes to exactly one model, so the
option knows it, and its size, when it is built, and names both in its ID.

The gateway caches each text's vector (tagged with the document), so
re-processing an edited document sends only the chunks whose text changed, and
deleting a document forgets its vectors. When the model cannot answer (Ollama not
running, bge-m3 not pulled), the gateway raises and so does this option: a
chunk without a vector must not move on.
"""

from __future__ import annotations

from collections.abc import Sequence

from ekb.platform.gateway.port import TextEmbedder
from ekb.platform.registry import register
from ekb.process.t7_embed.input import INPUT_VERSION
from ekb.process.t7_embed.port import Vectors

VERSION = 1
TASK = "embed"


def model_id(candidate: str, dimensions: int, *, int8: bool = False) -> str:
    """``model@1(input@1, ollama:bge-m3, 1024)``."""
    encoding = ", int8" if int8 else ""
    return f"model@{VERSION}({INPUT_VERSION}, {candidate}, {dimensions}{encoding})"


@register("t7_embed", "model")
class ModelEmbedder:
    def __init__(self, gateway: TextEmbedder, task: str = TASK) -> None:
        self._gateway = gateway
        self._task = task
        self._int8 = gateway.embedding_is_int8(task)
        self._candidate, self._dimensions = gateway.embedding_model(task)

    @property
    def model_id(self) -> str:
        return model_id(self._candidate, self._dimensions, int8=self._int8)

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def embed(self, texts: Sequence[str], *, document_key: str | None = None) -> Vectors:
        reply = self._gateway.embed(
            self._task, texts, input_version=INPUT_VERSION, document_key=document_key
        )
        return Vectors(
            vectors=reply.vectors,
            model_id=model_id(reply.candidate, reply.dimensions, int8=self._int8),
            int8=self._int8,
            sent=reply.sent,
            cached=reply.cached,
            note=f"{reply.candidate}: {reply.sent} sent, {reply.cached} from the cache",
        )
