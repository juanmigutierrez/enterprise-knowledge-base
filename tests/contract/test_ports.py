"""Every registered option must satisfy its step's port.

When a new option is added under ``<step>/options/`` this test picks it up
automatically. When a new port is written, add it to ``PORTS``.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import httpx
import pytest

from ekb.app.bootstrap import load_options
from ekb.changelog.bus.port import EventBus
from ekb.changelog.rawstore.port import RawStore
from ekb.changelog.registry.port import DocumentRegistry
from ekb.connect.docs_api import DocsApi
from ekb.connect.hr_api import HrApi
from ekb.connect.hr_db import CdcReader
from ekb.connect.identity_api import IdentityApi
from ekb.connect.people import People, PersonMap
from ekb.connect.port import Connector
from ekb.platform import registry
from ekb.platform.gateway.port import CostLog, EmbeddingClient, ModelClient, ResponseCache
from ekb.process.t2_diff.port import Differ
from ekb.process.t3_parse.port import Parser
from ekb.process.t4_chunk.header import HeaderWriter
from ekb.process.t4_chunk.port import Chunker
from ekb.process.t5_classify.port import Classifier
from ekb.process.t6_entities.port import EntityLinker
from ekb.process.t7_embed.port import Embedder
from ekb.process.t8_permissions.port import PermissionResolver
from ekb.stores.entities.port import EntityStore
from ekb.stores.permissions.port import PermissionStore
from ekb.stores.search.port import SearchStore
from tests.unit.platform.gateway.helpers import Scripted, make_world

PORTS: dict[str, type] = {
    "bus": EventBus,
    "connect": Connector,
    "registry": DocumentRegistry,
    "rawstore": RawStore,
    "t2_diff": Differ,
    "t3_parse": Parser,
    "t4_chunk": Chunker,
    "t4_header": HeaderWriter,
    "t5_classify": Classifier,
    "t6_entities": EntityLinker,
    "t7_embed": Embedder,
    "t8_permissions": PermissionResolver,
    "entities": EntityStore,
    "search": SearchStore,
    "permissions": PermissionStore,
    "model_client": ModelClient,
    "embedding_client": EmbeddingClient,
    "model_cache": ResponseCache,
    "cost_log": CostLog,
}

# Building a connector opens no connection, so clients with dummy URLs will do.
_CLIENT = httpx.Client(base_url="http://source.invalid")
_COMMON = {
    "sink": lambda event: None,
    "store_original": lambda key, version, original: "mem://raw/x",
    "people": PersonMap(lambda: People(users={})),
}

# Params needed to build an option without external services, if any.
TEST_PARAMS: dict[tuple[str, str], dict[str, object]] = {
    ("connect", "docs_webhook"): {**_COMMON, "api": DocsApi(_CLIENT)},
    ("connect", "docs_full_listing"): {**_COMMON, "api": DocsApi(_CLIENT), "known": dict},
    ("connect", "hr_api_delta"): {**_COMMON, "api": HrApi(_CLIENT)},
    ("connect", "hr_api_full_listing"): {**_COMMON, "api": HrApi(_CLIENT), "known": dict},
    ("connect", "hr_db_cdc"): {**_COMMON, "reader": CdcReader(Path("missing.sqlite"))},
    ("connect", "identity_sync"): {**_COMMON, "api": IdentityApi(_CLIENT)},
    # Building a client sends nothing; a cache in memory; a log path that is never written.
    ("model_client", "langchain"): {"provider": "groq", "model": "openai/gpt-oss-20b"},
    ("embedding_client", "langchain"): {"provider": "ollama", "model": "bge-m3"},
    ("model_cache", "sqlite"): {"path": ":memory:"},
    ("cost_log", "jsonl"): {"path": str(Path(tempfile.gettempdir()) / "ekb-contract.jsonl")},
    # Options that call a model take the gateway; building them calls nothing.
    ("t4_header", "model"): {"gateway": Scripted()},
    ("t5_classify", "model_plus_rules"): {"gateway": Scripted()},
    ("t7_embed", "model"): {"gateway": make_world().gateway},
}

load_options()
CASES = [(step, name) for step in PORTS for name in registry.options(step)]


@pytest.mark.parametrize(("step", "name"), CASES, ids=[f"{s}:{n}" for s, n in CASES])
def test_option_implements_port(step: str, name: str) -> None:
    instance = registry.create(step, name, **TEST_PARAMS.get((step, name), {}))
    assert isinstance(instance, PORTS[step])
