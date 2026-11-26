"""Every real provider, through LangChain's chat models. The only module that imports LangChain.

LangChain gives one interface to every provider: build the chat model, send the
messages, read the reply and its token counts (``usage_metadata``). Adding a
provider is one line in :data:`PROVIDERS` and its ``langchain-*`` package; adding
a model is only configuration.

What stays out of LangChain, on purpose, is everything the gateway decides:
which model, the cache, quotas and failover. So LangChain's own retries are off
(``max_retries=0``): a retry on a free tier spends a request the gateway did not
count (OpenRouter counts failed attempts too), and moving to another model is the
gateway's choice, not the library's.

Errors are sorted into the gateway's three kinds (see :func:`classify`):
LangChain's standard model errors where the integration raises them (Google,
Mistral), the HTTP status otherwise (Groq, OpenRouter), network errors last.

Embedding models (S10) go through LangChain's embedding classes the same way
(:class:`LangChainEmbeddingClient`, :data:`EMBEDDING_PROVIDERS`): Ollama for
bge-m3 in the POC, Bedrock for the production design. Their interface returns
vectors only. Bedrock's response headers can supply input usage for the spend cap.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from itertools import batched
from typing import Any, Protocol, cast

import httpx
from langchain_core.embeddings import Embeddings
from langchain_core.exceptions import (
    ContextOverflowError,
    ModelError,
    ModelInvalidRequestError,
    ModelRateLimitError,
)
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from ekb.platform.gateway.errors import ModelCallError
from ekb.platform.gateway.models import ChatRequest, EmbeddingReply, FailureKind, ModelReply
from ekb.platform.registry import register


@dataclass(frozen=True)
class ProviderClass:
    """Where a provider's chat model lives and how it names the common settings."""

    module: str
    cls: str
    package: str
    max_tokens: str = "max_tokens"
    timeout: str | None = "timeout"
    timeout_unit: float = 1.0
    """Seconds -> the class's unit: OpenRouter's timeout is in milliseconds."""
    retries: bool = True


PROVIDERS: dict[str, ProviderClass] = {
    "google_genai": ProviderClass(
        "langchain_google_genai", "ChatGoogleGenerativeAI", "langchain-google-genai"
    ),
    "groq": ProviderClass("langchain_groq", "ChatGroq", "langchain-groq"),
    "mistralai": ProviderClass("langchain_mistralai", "ChatMistralAI", "langchain-mistralai"),
    "openrouter": ProviderClass(
        "langchain_openrouter", "ChatOpenRouter", "langchain-openrouter", timeout_unit=1000.0
    ),
    "ollama": ProviderClass(
        "langchain_ollama",
        "ChatOllama",
        "langchain-ollama (uv sync --extra ollama)",
        max_tokens="num_predict",
        timeout=None,
        retries=False,
    ),
    # The production design (D15, architecture Part A); not installed in the POC.
    "bedrock_converse": ProviderClass(
        "langchain_aws", "ChatBedrockConverse", "langchain-aws (the aws extra)"
    ),
}

ChatModelFactory = Callable[..., BaseChatModel]


@register("model_client", "langchain")
class LangChainClient:
    """One ``provider:model``, called through its LangChain chat model.

    ``build`` replaces the provider's class (tests pass a fake chat model).
    """

    def __init__(
        self,
        provider: str,
        model: str,
        *,
        api_key: str | None = None,
        key_env: str | None = None,
        params: Mapping[str, Any] | None = None,
        settings: Mapping[str, str] | None = None,
        timeout_s: float = 60.0,
        build: ChatModelFactory | None = None,
    ) -> None:
        if provider not in PROVIDERS:
            known = ", ".join(sorted(PROVIDERS))
            raise ValueError(f"no LangChain provider {provider!r} (known: {known})")
        self._provider = provider
        self._spec = PROVIDERS[provider]
        self._model = model
        self._api_key = api_key
        self._key_env = key_env
        self._params = dict(params or {})
        self._settings = dict(settings or {})
        self._timeout_s = timeout_s
        self._build = build

    @property
    def candidate(self) -> str:
        return f"{self._provider}:{self._model}"

    def check(self) -> str | None:
        if self._key_env is not None and not self._api_key:
            return f"{self._key_env} is not set"
        if self._build is None and importlib.util.find_spec(self._spec.module) is None:
            return f"{self._spec.package} is not installed"
        return None

    def invoke(self, request: ChatRequest) -> ModelReply:
        try:
            chat = self._chat_model(request)
            messages: list[BaseMessage] = []
            if request.system:
                messages.append(SystemMessage(request.system))
            messages.append(HumanMessage(request.prompt))
            if request.json_schema is not None:
                raw, text = self._structured(chat, messages, request.json_schema)
            else:
                raw = chat.invoke(messages)
                text = raw.text
        except ModelCallError:
            raise
        except Exception as exc:
            raise ModelCallError(classify(exc), describe(exc)) from exc
        usage = raw.usage_metadata  # LangChain's standard token counts, the same for every provider
        metadata = raw.response_metadata or {}
        served = metadata.get("model_name") or metadata.get("model")
        return ModelReply(
            text=text,
            usage_reported=usage is not None,
            input_tokens=usage["input_tokens"] if usage else 0,
            output_tokens=usage["output_tokens"] if usage else 0,
            served_model=str(served) if served else None,
        )

    def _chat_model(self, request: ChatRequest) -> BaseChatModel:
        spec = self._spec
        kwargs: dict[str, Any] = {
            "model": self._model,
            **self._settings,
            **self._params,
            spec.max_tokens: request.max_output_tokens,
            "temperature": request.temperature,
        }
        if self._api_key:
            kwargs["api_key"] = self._api_key
        if spec.timeout is not None:
            timeout = self._timeout_s * spec.timeout_unit
            kwargs[spec.timeout] = int(timeout) if spec.timeout_unit != 1.0 else timeout
        if spec.retries:
            kwargs["max_retries"] = 0
        factory = self._build or cast(
            ChatModelFactory, getattr(importlib.import_module(spec.module), spec.cls)
        )
        return factory(**kwargs)

    @staticmethod
    def _structured(
        chat: BaseChatModel, messages: list[BaseMessage], schema: dict[str, Any]
    ) -> tuple[AIMessage, str]:
        runnable = chat.with_structured_output(schema, method="json_schema", include_raw=True)
        out = cast(dict[str, Any], runnable.invoke(messages))
        raw = cast(AIMessage, out["raw"])
        if out.get("parsing_error") is not None or out.get("parsed") is None:
            # The model answered, but not in the shape asked for: another may do better.
            usage = raw.usage_metadata
            raise ModelCallError(
                "unavailable",
                "reply did not match the JSON schema",
                received_reply=True,
                usage=(usage["input_tokens"], usage["output_tokens"]) if usage else None,
            )
        return raw, json.dumps(out["parsed"], ensure_ascii=False, sort_keys=True)


@dataclass(frozen=True)
class EmbeddingClass:
    """Where a provider's LangChain embedding class lives and how it names its settings."""

    module: str
    cls: str
    package: str
    model_arg: str = "model"
    timeout: Callable[[float], dict[str, Any]] | None = None
    """How the class takes a timeout in seconds, if it takes one."""


EMBEDDING_PROVIDERS: dict[str, EmbeddingClass] = {
    # bge-m3 and any other local embedding model (S10): `ollama pull bge-m3`.
    "ollama": EmbeddingClass(
        "langchain_ollama",
        "OllamaEmbeddings",
        "langchain-ollama (uv sync --extra ollama)",
        timeout=lambda seconds: {"client_kwargs": {"timeout": seconds}},
    ),
    # Cohere Embed v4 in the production design (D10); not installed in the POC.
    "bedrock": EmbeddingClass(
        "langchain_aws", "BedrockEmbeddings", "langchain-aws (the aws extra)", model_arg="model_id"
    ),
}


class _BedrockTransport(Protocol):
    """BedrockEmbeddings merges model_kwargs and decodes the Bedrock JSON reply."""

    def _invoke_model(self, input_body: dict[str, Any]) -> dict[str, Any]: ...


EmbeddingsFactory = Callable[..., Embeddings]


@register("embedding_client", "langchain")
class LangChainEmbeddingClient:
    """One embedding ``provider:model``, called through its LangChain embedding class.

    LangChain's ``embed_documents`` takes many texts and returns one vector each,
    nothing else: no token count, so ``usage_reported`` is ``False``. ``build``
    replaces the provider's class (tests pass a fake).
    """

    def __init__(
        self,
        provider: str,
        model: str,
        *,
        api_key: str | None = None,
        key_env: str | None = None,
        params: Mapping[str, Any] | None = None,
        settings: Mapping[str, str] | None = None,
        timeout_s: float = 60.0,
        build: EmbeddingsFactory | None = None,
    ) -> None:
        if provider not in EMBEDDING_PROVIDERS:
            known = ", ".join(sorted(EMBEDDING_PROVIDERS))
            raise ValueError(f"no LangChain embedding provider {provider!r} (known: {known})")
        self._provider = provider
        self._spec = EMBEDDING_PROVIDERS[provider]
        self._model = model
        self._api_key = api_key
        self._key_env = key_env
        self._params = dict(params or {})
        self._settings = dict(settings or {})
        self._timeout_s = timeout_s
        self._build = build

    @property
    def candidate(self) -> str:
        return f"{self._provider}:{self._model}"

    def check(self) -> str | None:
        if self._key_env is not None and not self._api_key:
            return f"{self._key_env} is not set"
        if self._build is None and importlib.util.find_spec(self._spec.module) is None:
            return f"{self._spec.package} is not installed"
        return None

    def embed(self, texts: Sequence[str]) -> EmbeddingReply:
        try:
            model = self._embeddings()
            if self._provider == "bedrock":
                return self._bedrock(model, texts)
            vectors = model.embed_documents(list(texts))
            return EmbeddingReply(
                vectors=tuple(tuple(vector) for vector in vectors),
                usage_reported=False,
                served_model=self._model,
            )
        except ModelCallError:
            raise
        except Exception as exc:
            raise ModelCallError(classify(exc), describe(exc)) from exc

    def _bedrock(self, model: Embeddings, texts: Sequence[str]) -> EmbeddingReply:
        # LangChain returns vectors only. Observe successful InvokeModel responses
        # on this call's client before the SDK discards their billing headers.
        counts: list[int | None] = []

        def observe(parsed: dict[str, Any], **_: Any) -> None:
            metadata = parsed.get("ResponseMetadata", {})
            if metadata.get("HTTPStatusCode") != 200:
                return
            value = str(metadata.get("HTTPHeaders", {}).get("x-amzn-bedrock-input-token-count"))
            counts.append(int(value) if value.isascii() and value.isdecimal() else None)

        events = getattr(getattr(getattr(model, "client", None), "meta", None), "events", None)
        event = "after-call.bedrock-runtime.InvokeModel"
        if events is not None:
            events.register(event, observe)
        try:
            if self._params.get("model_kwargs", {}).get("embedding_types") == ["int8"]:
                reply = self._embed_int8(cast(_BedrockTransport, model), texts)
            else:
                vectors = model.embed_documents(list(texts))
                reply = EmbeddingReply(
                    vectors=tuple(tuple(v) for v in vectors),
                    usage_reported=False,
                    served_model=self._model,
                )
        except Exception as exc:
            if not counts:
                raise
            kind = exc.kind if isinstance(exc, ModelCallError) else classify(exc)
            known = None if None in counts else (sum(n for n in counts if n is not None), 0)
            raise ModelCallError(kind, describe(exc), received_reply=True, usage=known) from exc
        finally:
            if events is not None:
                events.unregister(event, observe)
        if not counts or None in counts:
            return reply
        return reply.model_copy(
            update={"usage_reported": True, "input_tokens": sum(n for n in counts if n is not None)}
        )

    def _embed_int8(self, model: _BedrockTransport, texts: Sequence[str]) -> EmbeddingReply:
        # BedrockEmbeddings.embed_documents only unwraps the float response.
        vectors: list[tuple[int, ...]] = []
        received = False
        try:
            for batch in batched(texts, 96, strict=False):
                response = model._invoke_model(
                    {"input_type": "search_document", "texts": list(batch)}
                )
                received = True
                rows = response.get("embeddings", {}).get("int8")
                if not isinstance(rows, list) or len(rows) != len(batch):
                    raise ModelCallError("unavailable", "missing int8 vectors", received_reply=True)
                for row in rows:
                    if not isinstance(row, list) or not all(type(v) is int for v in row):
                        raise ModelCallError(
                            "unavailable", "invalid int8 components", received_reply=True
                        )
                    vectors.append(tuple(row))
        except ModelCallError:
            raise
        except Exception as exc:
            # Earlier batches may already have been billed.
            raise ModelCallError(classify(exc), describe(exc), received_reply=received) from exc
        return EmbeddingReply(
            vectors=tuple(vectors), usage_reported=False, served_model=self._model
        )

    def _embeddings(self) -> Embeddings:
        spec = self._spec
        kwargs: dict[str, Any] = {spec.model_arg: self._model, **self._settings, **self._params}
        if self._api_key:
            kwargs["api_key"] = self._api_key
        if spec.timeout is not None:
            kwargs.update(spec.timeout(self._timeout_s))
        factory = self._build or cast(
            EmbeddingsFactory, getattr(importlib.import_module(spec.module), spec.cls)
        )
        return factory(**kwargs)


def status_of(exc: BaseException) -> int | None:
    """The HTTP status an SDK error carries, wherever that SDK keeps it."""
    for value in (
        getattr(exc, "status_code", None),
        getattr(exc, "code", None),
        getattr(getattr(exc, "response", None), "status_code", None),
    ):
        if isinstance(value, int) and 100 <= value <= 599:
            return value
    return None


def classify(exc: BaseException) -> FailureKind:
    """Sort a library error into the gateway's three kinds.

    Anything that is not recognisably the provider's or the network's is
    ``invalid``: a bug of ours (a wrong setting, a wrong argument) must stop the
    call, not be hidden by moving to the next model.
    """
    if isinstance(exc, ModelRateLimitError):
        return "rate_limited"
    if isinstance(exc, (ModelInvalidRequestError, ContextOverflowError)):
        return "invalid"
    if isinstance(exc, ModelError):
        return "unavailable"  # authentication, permission, model not found, 5xx, timeout
    status = status_of(exc)
    if status == 429:
        return "rate_limited"
    if status in (400, 413, 422):
        return "invalid"
    if status is not None and status >= 400:
        return "unavailable"  # 401/403 bad key, 402 no credits, 404 model gone, 5xx
    name = type(exc).__name__
    if isinstance(exc, (httpx.TransportError, TimeoutError, ConnectionError)) or any(
        word in name for word in ("Timeout", "Connection")
    ):
        return "unavailable"
    if "RESOURCE_EXHAUSTED" in str(exc) or "rate limit" in str(exc).lower():
        return "rate_limited"
    return "invalid"


def describe(exc: BaseException) -> str:
    """SDK messages may echo prompts and credentials. Persist only type and HTTP status."""
    status = status_of(exc)
    where = f" (HTTP {status})" if status is not None else ""
    return f"{type(exc).__name__}{where}"
