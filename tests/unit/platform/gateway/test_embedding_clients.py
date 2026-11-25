"""The two embedding clients (S10): the offline fake, and LangChain's embedding classes.

No test reaches Ollama: the LangChain client is given a fake ``Embeddings`` class, and
the errors are built by hand, as in ``test_clients.py``.
"""

from __future__ import annotations

import math
from typing import Any

import httpx
import pytest
from langchain_core.embeddings import Embeddings

from ekb.platform.gateway.errors import ModelCallError
from ekb.platform.gateway.options import langchain as lc
from ekb.platform.gateway.options.fake import FakeEmbeddingClient, hashed_vector
from ekb.platform.gateway.options.langchain import LangChainEmbeddingClient, classify


class ScriptedEmbeddings(Embeddings):
    """A LangChain embedding class that remembers how it was built and what it was sent."""

    built: list[dict[str, Any]] = []  # noqa: RUF012 - shared on purpose: one per test via reset

    def __init__(self, **kwargs: Any) -> None:
        ScriptedEmbeddings.built.append(kwargs)
        self.error: Exception | None = kwargs.pop("error", None)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if self.error is not None:
            raise self.error
        return [[float(len(text)), 1.0, 0.0] for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]


class ResponseError(Exception):
    """Shaped like ``ollama.ResponseError`` (an optional extra, not installed in CI)."""

    def __init__(self, error: str, status_code: int) -> None:
        super().__init__(error)
        self.status_code = status_code


@pytest.fixture(autouse=True)
def _reset() -> None:
    ScriptedEmbeddings.built = []


def ollama_client(**kwargs: Any) -> LangChainEmbeddingClient:
    return LangChainEmbeddingClient(
        "ollama",
        "bge-m3",
        settings={"base_url": "http://localhost:11434"},
        timeout_s=30.0,
        build=ScriptedEmbeddings,
        **kwargs,
    )


# --------------------------------------------------------------------- fake


def test_the_fake_returns_one_unit_vector_per_text_and_counts_requests() -> None:
    client = FakeEmbeddingClient(dimensions=16)
    reply = client.embed(["leave policy", "leave policy", "expenses"])

    first, second, other = reply.vectors
    assert first == second
    assert first != other
    assert all(len(v) == 16 for v in reply.vectors)
    assert math.isclose(math.sqrt(sum(v * v for v in first)), 1.0, rel_tol=1e-9)
    assert client.calls == 1
    assert client.sent == [["leave policy", "leave policy", "expenses"]]
    assert client.candidate == "fake:fake-embed"


def test_the_fake_fails_as_scripted_and_reports_a_missing_reason() -> None:
    client = FakeEmbeddingClient(fail=["rate_limited"], missing="not today")

    with pytest.raises(ModelCallError) as caught:
        client.embed(["x"])
    assert caught.value.kind == "rate_limited"
    assert client.check() == "not today"
    assert len(client.embed(["x"]).vectors) == 1


def test_hashed_vectors_depend_on_the_text_only() -> None:
    assert hashed_vector("a", 40) == hashed_vector("a", 40)
    assert len(hashed_vector("a", 40)) == 40
    with pytest.raises(ValueError, match="positive"):
        FakeEmbeddingClient(dimensions=0)


# ----------------------------------------------------------------- langchain


def test_ollama_gets_the_model_its_url_and_a_timeout_and_returns_no_token_counts() -> None:
    reply = ollama_client().embed(["You may carry 5 days.", "Hi"])

    assert ScriptedEmbeddings.built == [
        {
            "model": "bge-m3",
            "base_url": "http://localhost:11434",
            "client_kwargs": {"timeout": 30.0},
        }
    ]
    assert reply.vectors == ((21.0, 1.0, 0.0), (2.0, 1.0, 0.0))
    assert not reply.usage_reported
    assert reply.served_model == "bge-m3"


def test_bedrock_reads_signed_int8_batches_using_coheres_response_shape() -> None:
    batches = []

    class Bedrock(ScriptedEmbeddings):
        def _invoke_model(self, input_body: dict[str, Any]) -> dict[str, Any]:
            assert input_body["input_type"] == "search_document"
            batch = input_body["texts"]
            batches.append(batch)
            return {"embeddings": {"int8": [[int(text), -128] for text in batch]}}

    client = LangChainEmbeddingClient(
        "bedrock",
        "cohere.embed-v4:0",
        params={"model_kwargs": {"embedding_types": ["int8"], "output_dimension": 1024}},
        build=Bedrock,
    )
    texts = [str(i) for i in range(100)]
    reply = client.embed(texts)
    assert batches == [texts[:96], texts[96:]]
    assert reply.vectors == tuple((i, -128) for i in range(100))
    assert ScriptedEmbeddings.built[0]["model_kwargs"]["embedding_types"] == ["int8"]


def test_error_after_a_bedrock_batch_preserves_unknown_spend() -> None:
    class Interrupted(ScriptedEmbeddings):
        def _invoke_model(self, input_body: dict[str, Any]) -> dict[str, Any]:
            if len(input_body["texts"]) < 96:
                raise TimeoutError
            return {"embeddings": {"int8": [[1]] * 96}}

    client = LangChainEmbeddingClient(
        "bedrock",
        "cohere.embed-v4:0",
        params={"model_kwargs": {"embedding_types": ["int8"]}},
        build=Interrupted,
    )
    with pytest.raises(ModelCallError) as error:
        client.embed(["synthetic"] * 97)
    assert error.value.received_reply
    assert error.value.usage is None


def test_bedrock_names_its_model_model_id() -> None:
    client = LangChainEmbeddingClient(
        "bedrock",
        "cohere.embed-v4:0",
        params={"model_kwargs": {"output_dimension": 1024}},
        build=ScriptedEmbeddings,
    )
    client.embed(["x"])

    assert ScriptedEmbeddings.built == [
        {"model_id": "cohere.embed-v4:0", "model_kwargs": {"output_dimension": 1024}}
    ]


def test_an_embedding_provider_whose_package_is_missing_says_which_to_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(
        lc.EMBEDDING_PROVIDERS,
        "ghost",
        lc.EmbeddingClass("no_such_module_ekb", "Embeddings", "langchain-ghost"),
    )

    assert LangChainEmbeddingClient("ghost", "m").check() == "langchain-ghost is not installed"


def test_an_unknown_embedding_provider_fails_when_built() -> None:
    with pytest.raises(ValueError, match="no LangChain embedding provider 'groq'"):
        LangChainEmbeddingClient("groq", "x")


@pytest.mark.parametrize(
    ("error", "kind"),
    [
        (ConnectionError("Failed to connect to Ollama"), "unavailable"),  # Ollama not running
        (
            ResponseError('model "bge-m3" not found, try pulling it first', 404),
            "unavailable",
        ),
        (ResponseError("input too long", 400), "invalid"),
        (httpx.ConnectTimeout("timed out"), "unavailable"),
    ],
)
def test_ollama_errors_become_the_gateways_kinds_without_their_message(
    error: Exception, kind: str
) -> None:
    client = LangChainEmbeddingClient(
        "ollama", "bge-m3", build=lambda **kw: ScriptedEmbeddings(**kw, error=error)
    )

    with pytest.raises(ModelCallError) as caught:
        client.embed(["You may carry 5 days."])

    assert caught.value.kind == kind
    assert classify(error) == kind
    assert "carry" not in str(caught.value)
    assert "pulling" not in str(caught.value)  # only the type and status are kept
