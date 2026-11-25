"""T7 wired behind T6 (S10), and a check that the embedding model can answer.

Two choices are made here, in the composition root, and nowhere else:

- **Which embedder.** By name: ``model`` (the gateway's ``embed`` task: bge-m3
  on Ollama in ``config/default.yaml``) or ``fake`` (offline, no meaning: the
  ``ci`` profile and the tests). The worker pipeline (S12) passes the one the
  profile names.
- **What a purge forgets.** Nothing new to wire: each vector is cached in the
  gateway tagged with its document, and :func:`ekb.app.classify.forgetter`
  already calls ``gateway.forget`` on a purge, so a deleted document's vectors
  go with its cached headers and labels.

T7 runs after T6 inside :func:`ekb.app.chunk.chunk_after_t3`. Unlike the header,
it fails closed: if the model cannot answer, nothing is kept and T2's event comes
back. :func:`probe` lets ``ekb inspector`` and ``ekb demo t7`` find that out
before they start, instead of failing on every document.
"""

from __future__ import annotations

import uuid

from ekb.platform import registry
from ekb.platform.gateway.errors import GatewayError
from ekb.platform.gateway.gateway import ModelGateway
from ekb.platform.gateway.port import TextEmbedder
from ekb.process.t7_embed.options.fake import FakeEmbedder
from ekb.process.t7_embed.options.model import TASK, ModelEmbedder
from ekb.process.t7_embed.port import Embedder
from ekb.process.t7_embed.step import EmbedStep

EMBEDDERS = (ModelEmbedder, FakeEmbedder)  # imported, so both are registered
DEFAULT_EMBEDDER = "model"
EMBEDDER_OPTIONS = ("model", "fake")
MODEL_EMBEDDERS = frozenset({"model"})
"""Embedder options that call a model, and so need the gateway."""
PROBE_VERSION = "probe@1"


def embedder(
    name: str = DEFAULT_EMBEDDER, gateway: TextEmbedder | None = None, **params: int
) -> Embedder:
    """A registered T7 option by name; ``model`` calls ``gateway`` (its ``embed`` task)."""
    if name in MODEL_EMBEDDERS:
        if gateway is None:
            raise ValueError(f"the {name!r} embedder needs the model gateway")
        built = registry.create("t7_embed", name, gateway=gateway)
    else:
        built = registry.create("t7_embed", name, **params)
    if not isinstance(built, Embedder):
        raise TypeError(f"t7_embed option {name!r} does not implement the Embedder port")
    return built


def embed_step(
    name: str = DEFAULT_EMBEDDER, gateway: TextEmbedder | None = None, **params: int
) -> EmbedStep:
    return EmbedStep(embedder(name, gateway, **params))


def probe(gateway: ModelGateway, task: str = TASK) -> str | None:
    """Why the embedding model cannot answer right now, or ``None`` when it can.

    One real call through the gateway, with a text no document holds, so the cache
    cannot answer for a model that has since stopped. Its vector is forgotten at
    once; the call stays in the cost log, like any other.
    """
    probe_key = f"probe:{uuid.uuid4().hex}"
    try:
        gateway.embed(
            task,
            [f"Is the embedding model up? {probe_key}"],
            input_version=PROBE_VERSION,
            document_key=probe_key,
        )
    except GatewayError as exc:
        return str(exc)
    finally:
        gateway.forget(probe_key)
    return None


def embed_help(command: str, offline: str) -> str:
    """What to do when T7's model cannot answer, for ``command`` (``ekb inspector``)."""
    return (
        "Start Ollama and pull the model once (`ollama pull bge-m3`; see "
        "docs/walkthrough/S10-t7-embed.md), set OLLAMA_URL=http://localhost:11434 in .env "
        f"and run `uv run --extra ollama --env-file .env {command}`; or use {offline} "
        "(vectors with no meaning)."
    )
