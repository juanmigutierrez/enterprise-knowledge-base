"""Which option each step of the write path runs, read from ``config/`` (S12).

Until S11 every demo and the inspector chose their options by hand. The worker
pipeline reads them from the active profile instead (``config/default.yaml``,
then ``config/profiles/$EKB_PROFILE.yaml``), so swapping an option is a config
change, as ``docs/repo-structure.md`` §2 promises.

Two things can make the option that runs differ from the one configured, and
both are printed, never silent:

- **Not built yet.** The configuration names the target design (Kafka,
  OpenSearch, Docling, the entity cascade …); some of it comes in later steps.
  Until then the step's **stand-in**, the option the POC has built, runs in its
  place (``STAND_INS``). The configuration is not changed: a default changes
  only with an experiment result (AGENTS.md).
- **Offline.** ``choose(…, offline=True)`` swaps every option that calls a model
  for its offline counterpart (``OFFLINE``): no header, the rules classifier,
  fake vectors. ``ekb demo ingest`` runs that way unless asked for models.

The inspector's ``--header``, ``--classifier`` and ``--embed`` flags override
the configuration last (:meth:`Choices.with_options`).

The query path (S13) is chosen the same way, by :func:`choose_query`: Q2's
pre-filter, Q3's retriever and, read from Q3's ``rerank`` setting, its reranker.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Any

from ekb.app.bootstrap import load_options
from ekb.platform import registry
from ekb.platform.config import Settings

PIPELINE_STEPS = (
    "bus",
    "registry",
    "rawstore",
    "t2_diff",
    "t3_parse",
    "t4_chunk",
    "t4_header",
    "t5_classify",
    "t6_entities",
    "t7_embed",
    "t8_permissions",
    "search",
    "entities",
    "permissions",
)
"""What the write path needs, in the order data meets it: box 3, box 4, then box 5."""

QUERY_STEPS = ("q2_prefilter", "q3_retrieve", "q3_rerank")
"""What the query path needs (S13), in the order a question meets it. ``q3_rerank`` is
not a step of its own in ``config/``: it is ``q3_retrieve``'s ``rerank`` setting."""
RERANK_SETTING = "rerank"

STAND_INS: Mapping[str, str] = MappingProxyType(
    {
        "bus": "in_memory",  # kafka (S17)
        "registry": "in_memory",  # dynamodb (S17)
        "rawstore": "in_memory",  # s3 (S17)
        "t3_parse": "markdown",  # docling (card 4.1)
        "t6_entities": "exact_match",  # cascade (card 4.10)
        "search": "in_memory",  # opensearch (S17)
        "entities": "in_memory",  # dynamodb (S17)
        "permissions": "in_memory",  # dynamodb (S17)
        "q2_prefilter": "acl",  # acl_and_cedar (S15)
        "q3_rerank": "none",  # bge_reranker, cohere_rerank_3_5 (card 4.6)
    }
)
"""The option that runs while the configured one is not built yet."""

MODEL_OPTIONS: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "t4_header": frozenset({"model"}),
        "t5_classify": frozenset({"model_plus_rules"}),
        "t7_embed": frozenset({"model"}),
    }
)
"""Options that call a model through the gateway (D15)."""

OFFLINE: Mapping[str, str] = MappingProxyType(
    {"t4_header": "none", "t5_classify": "rules", "t7_embed": "fake"}
)
"""What runs instead of a model option when no model is wanted."""


class NoStandInError(LookupError):
    """The configuration names an option that is not built, and the step has no stand-in."""


@dataclass(frozen=True)
class Choice:
    """One step's option: what the configuration names and what actually runs."""

    step: str
    option: str
    """The option that runs."""
    params: Mapping[str, Any] = field(default_factory=dict)
    configured: str = ""
    """The option the configuration names."""
    why: str = ""
    """Empty when the configured option runs; otherwise why another one does."""

    @property
    def swapped(self) -> bool:
        return self.option != self.configured

    def describe(self) -> str:
        """``t3_parse docling -> markdown (not built yet)`` or ``t2_diff content_hash``."""
        settings = ", ".join(f"{k}={v}" for k, v in sorted(self.params.items()))
        option = f"{self.option}({settings})" if settings else self.option
        if not self.swapped:
            return f"{self.step} {option}"
        return f"{self.step} {self.configured} -> {option} ({self.why})"


@dataclass(frozen=True)
class Choices:
    """Every step's :class:`Choice`, in :data:`PIPELINE_STEPS` order, for one profile."""

    profile: str
    steps: tuple[Choice, ...]

    def __getitem__(self, step: str) -> Choice:
        for choice in self.steps:
            if choice.step == step:
                return choice
        raise KeyError(step)

    def option(self, step: str) -> str:
        return self[step].option

    def params(self, step: str) -> dict[str, Any]:
        return dict(self[step].params)

    @property
    def swapped(self) -> tuple[Choice, ...]:
        """The steps where something other than the configured option runs."""
        return tuple(choice for choice in self.steps if choice.swapped)

    @property
    def calls_models(self) -> bool:
        """Whether any step runs an option that calls a model (and so needs the gateway)."""
        return any(c.option in MODEL_OPTIONS.get(c.step, frozenset()) for c in self.steps)

    def with_options(self, why: str = "set by hand", **options: str | None) -> Choices:
        """These steps' options replaced (``None`` keeps the current one).

        As in the profiles, another option keeps none of the previous one's
        settings: they would mean nothing to it.
        """
        unknown = set(options) - {choice.step for choice in self.steps}
        if unknown:
            raise KeyError(f"no such step: {', '.join(sorted(unknown))}")
        steps = []
        for choice in self.steps:
            wanted = options.get(choice.step)
            if wanted is None or wanted == choice.option:
                steps.append(choice)
                continue
            steps.append(replace(choice, option=wanted, params={}, why=why))
        return replace(self, steps=tuple(steps))


def choose(settings: Settings, *, offline: bool = False) -> Choices:
    """The options the active profile names, each replaced by its stand-in while it is
    not built, and, ``offline``, every model option by its offline counterpart."""
    load_options()  # every option registers itself on import
    steps: list[Choice] = []
    for step in PIPELINE_STEPS:
        configured = settings.steps.get(step)
        name = configured.option if configured is not None else ""
        params: Mapping[str, Any] = configured.params if configured is not None else {}
        choice = _resolve(step, name, params)
        if offline and choice.option in MODEL_OPTIONS.get(step, frozenset()):
            choice = replace(choice, option=OFFLINE[step], params={}, why="offline: no model")
        steps.append(choice)
    return Choices(settings.profile, tuple(steps))


def choose_query(settings: Settings) -> Choices:
    """The query path's options (S13): Q2's pre-filter, Q3's retriever and its reranker.

    Q3's ``rerank`` setting names the reranker (``bge_reranker`` locally), so it is
    taken out of Q3's settings and chosen as ``q3_rerank``, with a stand-in while it
    is not built. Nothing here calls a model: the question's vector comes from T7's
    embedder, which :func:`choose` decides.
    """
    load_options()
    wanted: dict[str, tuple[str, Mapping[str, Any]]] = {}
    for step in QUERY_STEPS[:2]:
        configured = settings.steps.get(step)
        name = configured.option if configured is not None else ""
        wanted[step] = (name, dict(configured.params) if configured is not None else {})
    retrieve_name, retrieve_params = wanted["q3_retrieve"]
    params = dict(retrieve_params)
    rerank = str(params.pop(RERANK_SETTING, "none"))
    wanted["q3_retrieve"] = (retrieve_name, params)
    wanted["q3_rerank"] = (rerank, {})
    steps = tuple(_resolve(step, *wanted[step]) for step in QUERY_STEPS)
    return Choices(settings.profile, steps)


def _resolve(step: str, name: str, params: Mapping[str, Any]) -> Choice:
    """The configured option if it is built, else the step's stand-in, saying why."""
    choice = Choice(step, name, dict(params), configured=name)
    if name in registry.options(step):
        return choice
    stand_in = STAND_INS.get(step)
    if stand_in is None:
        built = ", ".join(registry.options(step)) or "none"
        raise NoStandInError(
            f"{step}: {name or 'no option'!r} is not built yet and has no stand-in "
            f"(built: {built}); choose a built one in config/"
        )
    why = "not built yet" if name else "not configured"
    return replace(choice, option=stand_in, params={}, why=why)
