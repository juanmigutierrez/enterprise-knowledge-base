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

The query path (S13, S14) is chosen the same way, by :func:`choose_query`: Q1's
planner, Q2's pre-filter, Q3's retriever and, read from Q3's ``rerank``
setting, its reranker; Q4's post-check and, read from its ``redact`` setting
(``rules`` when absent), its redactor.

The Knowledge API's own steps (S15) by :func:`choose_assistant`: the token
check, the HR assistant's answer writer (offline: ``extractive``) and the
answer cache.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Any

from ekb.app.bootstrap import load_options
from ekb.platform import registry
from ekb.platform.config import Settings
from ekb.platform.gateway.config import OptionChoice

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

QUERY_STEPS = ("q1_plan", "q2_prefilter", "q3_retrieve", "q3_rerank", "q4_postcheck", "q4_redact")
"""What the query path needs (S13, S14), in the order a question meets it."""
ASSISTANT_STEPS = ("auth", "answer", "answer_cache")
"""What the Knowledge API adds around the query path (S15): who is asking, the written
answer, and the answers kept for reuse."""
SETTING_STEPS: Mapping[str, tuple[str, str, str]] = MappingProxyType(
    {
        "q3_rerank": ("q3_retrieve", "rerank", "none"),
        "q4_redact": ("q4_postcheck", "redact", "rules"),
    }
)
"""Query steps that are another step's setting in ``config/``, not steps of their own:
``step: (owner step, setting, option when the setting is absent)``."""

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
        "q1_plan": "rules",  # rewrite_when_needed (card 4.8)
        "q2_prefilter": "acl",  # acl_and_cedar (S15b)
        "q3_rerank": "none",  # bge_reranker, cohere_rerank_3_5 (card 4.6)
    }
)
"""The option that runs while the configured one is not built yet."""

MODEL_OPTIONS: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "t4_header": frozenset({"model"}),
        "t5_classify": frozenset({"model_plus_rules"}),
        "t7_embed": frozenset({"model"}),
        "answer": frozenset({"model"}),
    }
)
"""Options that call a model through the gateway (D15)."""

OFFLINE: Mapping[str, str] = MappingProxyType(
    {"t4_header": "none", "t5_classify": "rules", "t7_embed": "fake", "answer": "extractive"}
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
    model_cache: OptionChoice | None = None
    """The write worker also purges this cache when its model calls are disabled."""

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
    return replace(
        _choose(settings, PIPELINE_STEPS, offline=offline), model_cache=settings.gateway.cache
    )


def choose_assistant(settings: Settings, *, offline: bool = False) -> Choices:
    """The Knowledge API's steps (S15): the token check, the answer writer (``offline``:
    ``extractive``, which calls no model) and the answer cache."""
    return _choose(settings, ASSISTANT_STEPS, offline=offline)


def _choose(settings: Settings, names: tuple[str, ...], *, offline: bool) -> Choices:
    load_options()  # every option registers itself on import
    steps: list[Choice] = []
    for step in names:
        configured = settings.steps.get(step)
        name = configured.option if configured is not None else ""
        params: Mapping[str, Any] = configured.params if configured is not None else {}
        choice = _resolve(step, name, params)
        if offline and choice.option in MODEL_OPTIONS.get(step, frozenset()):
            choice = replace(choice, option=OFFLINE[step], params={}, why="offline: no model")
        steps.append(choice)
    return Choices(settings.profile, tuple(steps))


def choose_query(settings: Settings) -> Choices:
    """The query path's options (S13, S14), Q1 to Q4, each with its stand-in while not built.

    Two are settings of another step (:data:`SETTING_STEPS`): Q3's ``rerank``
    (``bge_reranker`` locally) is chosen as ``q3_rerank``, and Q4's ``redact``
    (``rules`` when absent) as ``q4_redact``; each is taken out of its owner's
    settings. Nothing here calls a model: the question's vector comes from T7's
    embedder, which :func:`choose` decides.
    """
    load_options()
    wanted: dict[str, tuple[str, dict[str, Any]]] = {}
    for step in QUERY_STEPS:
        if step in SETTING_STEPS:
            continue
        configured = settings.steps.get(step)
        name = configured.option if configured is not None else ""
        wanted[step] = (name, dict(configured.params) if configured is not None else {})
    for step, (owner, setting, absent) in SETTING_STEPS.items():
        owner_name, owner_params = wanted[owner]
        params = dict(owner_params)
        option = str(params.pop(setting, absent))
        wanted[owner] = (owner_name, params)
        wanted[step] = (option, {})
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
