"""Ports of the model gateway. Each has options in ``options/``, chosen in ``ekb.app.gateway``.

- :class:`ModelClient` calls one model. ``fake`` (offline, deterministic) and
  ``langchain`` (every real provider, through LangChain's chat models).
- :class:`EmbeddingClient` calls one embedding model (S10). ``fake`` and
  ``langchain`` (LangChain's embedding classes: Ollama for bge-m3).
- :class:`ResponseCache` keeps replies by cache key. ``in_memory`` and ``sqlite``.
- :class:`CostLog` keeps one record per call. ``in_memory`` and ``jsonl``.

And two ports the other way round, what a pipeline step needs from the gateway:
:class:`Completer` (S8) and :class:`TextEmbedder` (S10).
:class:`~ekb.platform.gateway.gateway.ModelGateway` implements both; a step's
tests can pass anything with the same methods.
"""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import AbstractContextManager
from typing import Any, Protocol, runtime_checkable

from ekb.platform.gateway.models import (
    CachedReply,
    CallRecord,
    ChatRequest,
    EmbeddingReply,
    EmbedReply,
    GatewayReply,
    ModelReply,
)


@runtime_checkable
class ModelClient(Protocol):
    """Calls one model of one provider."""

    @property
    def candidate(self) -> str:
        """``provider:model``, as the configuration names it."""
        ...

    def check(self) -> str | None:
        """Why this client cannot be used right now (no API key, a package not
        installed), or ``None``. Checked before every call, so a missing key
        moves the call to the next candidate instead of failing it."""
        ...

    def invoke(self, request: ChatRequest) -> ModelReply:
        """Call the model. Failures are raised as
        :class:`~ekb.platform.gateway.errors.ModelCallError`, already sorted
        into rate-limited, unavailable or invalid."""
        ...


@runtime_checkable
class EmbeddingClient(Protocol):
    """Calls one embedding model of one provider (S10)."""

    @property
    def candidate(self) -> str:
        """``provider:model``, as the configuration names it."""
        ...

    def check(self) -> str | None:
        """Why this client cannot be used right now, or ``None`` (as :class:`ModelClient`)."""
        ...

    def embed(self, texts: Sequence[str]) -> EmbeddingReply:
        """One vector per text, in order. Failures are raised as
        :class:`~ekb.platform.gateway.errors.ModelCallError`, as for a chat call."""
        ...


@runtime_checkable
class ResponseCache(Protocol):
    """Replies by cache key, each tagged with the documents that asked for it."""

    @property
    def guard(self) -> AbstractContextManager[object]:
        """Serialize gateway operations that share this store."""
        ...

    def get(self, key: str) -> CachedReply | None: ...

    def put(self, key: str, entry: CachedReply, document_key: str | None = None) -> None:
        """Keep ``entry``; tag it with ``document_key`` when the call was about one."""
        ...

    def tag(self, key: str, document_key: str) -> None:
        """Another document reused this entry (a hit): it now depends on it too."""
        ...

    def forget(self, document_key: str) -> int:
        """A document was deleted: drop its tag, and every entry left with no tag.

        An entry another live document still uses is kept; an entry never tied to
        a document (no tag at all) is not touched. Returns how many were dropped.
        """
        ...

    def size(self) -> int: ...


@runtime_checkable
class CostLog(Protocol):
    """Every call, in order. Quotas and the budget are counted from it."""

    @property
    def guard(self) -> AbstractContextManager[object]:
        """Serialize gateway operations that share this store."""
        ...

    def append(self, record: CallRecord) -> None: ...

    def records(self) -> list[CallRecord]: ...


@runtime_checkable
class Completer(Protocol):
    """What a step asks of the gateway: answer this prompt for this task.

    Raises :class:`~ekb.platform.gateway.errors.GatewayError` when no model can
    answer (every candidate skipped or failed) or the request itself is invalid.
    """

    def complete(
        self,
        task: str,
        prompt: str,
        *,
        prompt_version: str,
        max_output_tokens: int,
        system: str = "",
        temperature: float = 0.0,
        json_schema: dict[str, Any] | None = None,
        document_key: str | None = None,
    ) -> GatewayReply: ...


@runtime_checkable
class TextEmbedder(Protocol):
    """What T7 asks of the gateway (S10): the vectors of these texts, for this task.

    ``embedding_model`` answers before any call: an embedding task routes to
    exactly one model, so a step can name it, and its vector size, in the ID it
    stores with every vector. ``embed`` raises :class:`~ekb.platform.gateway.errors.GatewayError`
    when the model cannot answer.
    """

    def embedding_is_int8(self, task: str) -> bool: ...

    def embedding_model(self, task: str) -> tuple[str, int]:
        """The task's ``provider:model`` and its vector size."""
        ...

    def embed(
        self,
        task: str,
        texts: Sequence[str],
        *,
        input_version: str,
        document_key: str | None = None,
    ) -> EmbedReply: ...
