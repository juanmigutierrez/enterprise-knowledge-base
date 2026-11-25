"""The two model clients: the offline fake, and LangChain for every real provider.

No test reaches a provider: the LangChain client is given a fake chat model, and
errors are the providers' own exception classes, built by hand.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, cast

import groq
import httpx
import pytest
from google.genai import errors as google_errors
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.exceptions import (
    ContextOverflowError,
    ModelAuthenticationError,
    ModelNotFoundError,
    ModelRateLimitError,
)
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_mistralai.chat_models import (
    MistralAIInvalidRequestError,
    MistralAIRateLimitError,
)
from openrouter.errors import OpenRouterError
from pydantic import Field

from ekb.platform.gateway.errors import ModelCallError
from ekb.platform.gateway.models import ChatRequest
from ekb.platform.gateway.options import langchain as lc
from ekb.platform.gateway.options.fake import FakeClient
from ekb.platform.gateway.options.langchain import LangChainClient, classify, describe

REQUEST = ChatRequest(
    system="One line.", prompt="You may carry up to 5 unused days.", max_output_tokens=40
)
_HTTP = httpx.Request("POST", "https://provider.invalid/v1/chat")


def response(status: int) -> httpx.Response:
    return httpx.Response(status, request=_HTTP)


# --------------------------------------------------------------- a chat model


class ScriptedChatModel(BaseChatModel):
    """A LangChain chat model that answers (or fails) as told, with token counts."""

    reply: str = "Annual leave policy > Carrying leave over"
    error: Exception | None = None
    seen: list[list[BaseMessage]] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.seen.append(messages)
        if self.error is not None:
            raise self.error
        message = AIMessage(
            content=self.reply,
            usage_metadata={"input_tokens": 21, "output_tokens": 7, "total_tokens": 28},
            response_metadata={"model_name": "served-model-v2"},
        )
        return ChatResult(generations=[ChatGeneration(message=message)])


def factory(
    reply: str = "Annual leave policy > Carrying leave over", error: Exception | None = None
) -> tuple[Callable[..., BaseChatModel], list[dict[str, Any]]]:
    """A ``build`` function for the client, and the arguments each model was built with."""
    built: list[dict[str, Any]] = []

    def build(**kwargs: Any) -> BaseChatModel:
        built.append(kwargs)
        return ScriptedChatModel(reply=reply, error=error, seen=[])

    return build, built


def client(provider: str = "groq", **kwargs: Any) -> LangChainClient:
    options: dict[str, Any] = {"api_key": "test-key", "key_env": "GROQ_API_KEY"}
    options.update(kwargs)
    return LangChainClient(provider, "some-model", **options)


# ------------------------------------------------------------------- the fake


def test_the_fake_is_deterministic_and_counts_its_calls() -> None:
    fake = FakeClient("small")

    first, second = fake.invoke(REQUEST), fake.invoke(REQUEST)

    assert first == second
    assert first.text.startswith("small says: You may carry up to 5")
    assert first.input_tokens > 0
    assert fake.calls == 2
    assert fake.candidate == "fake:small"


def test_the_fake_fails_as_scripted_in_order() -> None:
    fake = FakeClient(fail=["rate_limited", "unavailable"])

    kinds = []
    for _ in range(2):
        with pytest.raises(ModelCallError) as raised:
            fake.invoke(REQUEST)
        kinds.append(raised.value.kind)

    assert kinds == ["rate_limited", "unavailable"]
    assert fake.invoke(REQUEST).text  # then it answers


def test_the_fake_answers_json_when_asked_for_a_schema() -> None:
    schema = {"type": "object", "properties": {"header": {"type": "string"}}}

    reply = FakeClient("small").invoke(REQUEST.model_copy(update={"json_schema": schema}))

    assert json.loads(reply.text) == {"header": "small header"}


def test_the_fake_fills_in_enums_lists_numbers_and_nested_objects() -> None:
    """So a step that checks its reply against the schema (S8) accepts the fake's."""
    schema = {
        "type": "object",
        "properties": {
            "level": {"type": "string", "enum": ["public", "internal"]},
            "kinds": {"type": "array", "items": {"type": "string"}},
            "count": {"type": "integer"},
            "ok": {"type": "boolean"},
            "chunk_0": {"type": "object", "properties": {"text": {"type": "string"}}},
        },
    }

    reply = FakeClient("small").invoke(REQUEST.model_copy(update={"json_schema": schema}))

    assert json.loads(reply.text) == {
        "level": "public",
        "kinds": [],
        "count": 0,
        "ok": False,
        "chunk_0": {"text": "small text"},
    }


def test_the_fake_never_counts_more_output_than_allowed() -> None:
    reply = FakeClient().invoke(REQUEST.model_copy(update={"max_output_tokens": 2}))

    assert reply.output_tokens == 2


# ------------------------------------------------------------- the langchain one


def test_langchain_reply_text_tokens_and_served_model() -> None:
    build, _ = factory()

    reply = client(build=build).invoke(REQUEST)

    assert reply.text == "Annual leave policy > Carrying leave over"
    assert (reply.input_tokens, reply.output_tokens) == (21, 7)
    assert reply.served_model == "served-model-v2"


def test_langchain_sends_the_system_and_user_messages() -> None:
    model = ScriptedChatModel(seen=[])

    client(build=lambda **_: model).invoke(REQUEST)

    (messages,) = model.seen
    assert [(m.type, m.content) for m in messages] == [
        ("system", "One line."),
        ("human", "You may carry up to 5 unused days."),
    ]


def test_langchain_settings_retries_off_timeout_and_output_limit() -> None:
    build, built = factory()

    client(build=build, params={"reasoning_effort": "low"}, timeout_s=30).invoke(REQUEST)

    assert built == [
        {
            "model": "some-model",
            "reasoning_effort": "low",
            "max_tokens": 40,
            "temperature": 0.0,
            "api_key": "test-key",
            "timeout": 30,
            "max_retries": 0,  # the gateway decides what happens after a failure
        }
    ]


def test_openrouter_timeout_is_in_milliseconds() -> None:
    build, built = factory()

    client("openrouter", build=build, key_env="OPENROUTER_API_KEY", timeout_s=30).invoke(REQUEST)

    assert built[0]["timeout"] == 30_000


def test_ollama_has_its_own_names_and_settings_from_the_environment() -> None:
    build, built = factory()

    LangChainClient(
        "ollama", "gemma4:e4b", settings={"base_url": "http://127.0.0.1:11434"}, build=build
    ).invoke(REQUEST)

    assert built[0] == {
        "model": "gemma4:e4b",
        "base_url": "http://127.0.0.1:11434",
        "num_predict": 40,
        "temperature": 0.0,
    }


def test_langchain_asks_for_json_through_structured_output() -> None:
    raw = AIMessage(
        content='{"header": "Annual leave"}',
        usage_metadata={"input_tokens": 5, "output_tokens": 3, "total_tokens": 8},
    )
    asked: list[dict[str, Any]] = []

    class Structured:
        def invoke(self, messages: list[BaseMessage]) -> dict[str, Any]:
            return {"raw": raw, "parsed": {"header": "Annual leave"}, "parsing_error": None}

    class Chat:
        def with_structured_output(self, schema: dict[str, Any], **kwargs: Any) -> Structured:
            asked.append({"schema": schema, **kwargs})
            return Structured()

    schema = {"type": "object", "properties": {"header": {"type": "string"}}}
    request = REQUEST.model_copy(update={"json_schema": schema})

    reply = client(build=lambda **_: cast(BaseChatModel, Chat())).invoke(request)

    assert json.loads(reply.text) == {"header": "Annual leave"}
    assert reply.output_tokens == 3
    assert asked == [{"schema": schema, "method": "json_schema", "include_raw": True}]


def test_a_reply_that_breaks_the_schema_is_unavailable_so_another_model_can_try() -> None:
    class Structured:
        def invoke(self, messages: list[BaseMessage]) -> dict[str, Any]:
            return {
                "raw": AIMessage(
                    content="no json",
                    usage_metadata={"input_tokens": 10, "output_tokens": 3, "total_tokens": 13},
                ),
                "parsed": None,
                "parsing_error": ValueError(),
            }

    class Chat:
        def with_structured_output(self, schema: dict[str, Any], **kwargs: Any) -> Structured:
            return Structured()

    request = REQUEST.model_copy(update={"json_schema": {"type": "object"}})

    with pytest.raises(ModelCallError) as raised:
        client(build=lambda **_: cast(BaseChatModel, Chat())).invoke(request)

    assert raised.value.kind == "unavailable"
    assert raised.value.usage == (10, 3)


def test_a_missing_key_is_reported_before_any_call() -> None:
    assert client(api_key=None).check() == "GROQ_API_KEY is not set"
    assert client().check() is None


def test_a_provider_whose_package_is_missing_says_which_to_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(
        lc.PROVIDERS, "ghost", lc.ProviderClass("no_such_module_ekb", "Chat", "langchain-ghost")
    )

    assert LangChainClient("ghost", "m").check() == "langchain-ghost is not installed"


def test_an_unknown_provider_fails_when_built() -> None:
    with pytest.raises(ValueError, match="no LangChain provider 'telepathy'"):
        LangChainClient("telepathy", "m")


def test_a_library_error_becomes_a_model_call_error_without_the_prompt() -> None:
    build, _ = factory(
        error=groq.RateLimitError("Rate limit reached", response=response(429), body=None)
    )

    with pytest.raises(ModelCallError) as raised:
        client(build=build).invoke(REQUEST)

    assert raised.value.kind == "rate_limited"
    assert raised.value.detail.startswith("RateLimitError (HTTP 429)")
    assert "carry up to 5" not in raised.value.detail


# ---------------------------------------------------------------- the sorting


@pytest.mark.parametrize(
    ("error", "kind"),
    [
        # LangChain's standard errors (Google and Mistral raise these)
        (ModelRateLimitError("quota"), "rate_limited"),
        (ModelAuthenticationError("bad key"), "unavailable"),
        (ModelNotFoundError("model gone"), "unavailable"),
        (ContextOverflowError("too long"), "invalid"),
        (
            MistralAIRateLimitError("429", request=_HTTP, response=response(429)),
            "rate_limited",
        ),
        (
            MistralAIInvalidRequestError("400", request=_HTTP, response=response(400)),
            "invalid",
        ),
        # SDK errors with an HTTP status (Groq, OpenRouter, Google's own client)
        (groq.RateLimitError("slow down", response=response(429), body=None), "rate_limited"),
        (groq.APIStatusError("server", response=response(503), body=None), "unavailable"),
        (groq.BadRequestError("bad", response=response(400), body=None), "invalid"),
        (OpenRouterError("Rate limit exceeded", response(429)), "rate_limited"),
        (OpenRouterError("Insufficient credits", response(402)), "unavailable"),
        (OpenRouterError("No endpoints found", response(404)), "unavailable"),
        (
            google_errors.ClientError(429, {"error": {"status": "RESOURCE_EXHAUSTED"}}),
            "rate_limited",
        ),
        # the network
        (groq.APIConnectionError(request=_HTTP), "unavailable"),
        (httpx.ConnectTimeout("timed out"), "unavailable"),
        (TimeoutError(), "unavailable"),
        # a bug of ours must stop the call
        (TypeError("unexpected keyword argument 'max_tokns'"), "invalid"),
    ],
    ids=lambda value: type(value).__name__ if isinstance(value, BaseException) else value,
)
def test_errors_are_sorted_into_three_kinds(error: Exception, kind: str) -> None:
    assert classify(error) == kind


def test_describe_discards_provider_messages_that_can_echo_document_text() -> None:
    error = OpenRouterError("No endpoints found\nfor this model " + "x" * 500, response(404))

    detail = describe(error)

    assert detail == "OpenRouterError (HTTP 404)"
    assert "\n" not in detail
    assert len(detail) < 220
