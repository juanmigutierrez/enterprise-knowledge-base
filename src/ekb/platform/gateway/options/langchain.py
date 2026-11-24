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
"""

from __future__ import annotations

import importlib
import importlib.util
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, cast

import httpx
from langchain_core.exceptions import (
    ContextOverflowError,
    ModelError,
    ModelInvalidRequestError,
    ModelRateLimitError,
)
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from ekb.platform.gateway.errors import ModelCallError
from ekb.platform.gateway.models import ChatRequest, FailureKind, ModelReply
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
