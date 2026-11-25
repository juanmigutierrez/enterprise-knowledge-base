"""Options from config (S12): what each profile names, and what actually runs."""

from __future__ import annotations

import pytest

from ekb.app.choices import (
    PIPELINE_STEPS,
    QUERY_STEPS,
    Choice,
    NoStandInError,
    choose,
    choose_query,
)
from ekb.platform.config import StepChoice, load_settings


def test_the_local_profile_runs_its_built_options_and_stand_ins_for_the_rest() -> None:
    choices = choose(load_settings(profile="local"))

    assert [c.step for c in choices.steps] == list(PIPELINE_STEPS)
    assert choices.option("t2_diff") == "content_hash"
    assert choices.option("t4_chunk") == "structure_aware"
    assert choices.params("t4_chunk") == {"max_tokens": 400}
    assert choices.option("t7_embed") == "model"
    assert {c.step: (c.configured, c.option) for c in choices.swapped} == {
        "bus": ("kafka", "in_memory"),
        "registry": ("dynamodb", "in_memory"),
        "rawstore": ("s3", "in_memory"),
        "t3_parse": ("docling", "markdown"),
        "t6_entities": ("cascade", "exact_match"),
        "search": ("opensearch", "in_memory"),
        "entities": ("dynamodb", "in_memory"),
        "permissions": ("dynamodb", "in_memory"),
    }
    assert {c.why for c in choices.swapped} == {"not built yet"}
    assert choices.params("rawstore") == {}  # the s3 bucket means nothing to in_memory
    assert choices.calls_models


def test_offline_swaps_every_model_option_and_says_so() -> None:
    choices = choose(load_settings(profile="local"), offline=True)

    assert [choices.option(s) for s in ("t4_header", "t5_classify", "t7_embed")] == [
        "none",
        "rules",
        "fake",
    ]
    assert choices["t5_classify"].describe() == (
        "t5_classify model_plus_rules -> rules (offline: no model)"
    )
    assert not choices.calls_models


def test_the_ci_profile_needs_no_swap_for_its_processing_steps() -> None:
    choices = choose(load_settings(profile="ci"))

    assert choices.profile == "ci"
    assert choices["t3_parse"].describe() == "t3_parse plain_text"
    assert choices["t4_chunk"].describe() == "t4_chunk fixed(max_tokens=400)"
    assert choices["t7_embed"].describe() == "t7_embed fake(dimensions=32)"
    assert not choices.calls_models


def test_flags_override_last_and_drop_the_other_options_settings() -> None:
    choices = choose(load_settings(profile="ci")).with_options(
        "command line", t7_embed="model", t4_header=None, t4_chunk="fixed"
    )

    assert choices["t7_embed"] == Choice(
        "t7_embed", "model", {}, configured="fake", why="command line"
    )
    assert choices.params("t4_chunk") == {"max_tokens": 400}  # same option: kept
    assert choices.option("t4_header") == "none"  # None: unchanged
    with pytest.raises(KeyError, match="no such step: t9"):
        choices.with_options(t9="x")


def test_an_unbuilt_option_without_a_stand_in_is_refused() -> None:
    settings = load_settings(profile="ci")
    steps = {**settings.steps, "t2_diff": StepChoice(option="semantic_diff")}
    with pytest.raises(NoStandInError, match="t2_diff: 'semantic_diff' is not built yet"):
        choose(settings.model_copy(update={"steps": steps}))


def test_a_step_the_config_leaves_out_gets_its_stand_in() -> None:
    settings = load_settings(profile="ci")
    steps = {k: v for k, v in settings.steps.items() if k != "bus"}
    choices = choose(settings.model_copy(update={"steps": steps}))
    assert choices["bus"].describe() == "bus  -> in_memory (not configured)"


def test_the_query_path_runs_its_stand_ins_and_takes_the_reranker_out_of_q3() -> None:
    """S13: acl_and_cedar runs as acl until S15, the configured reranker as none until 4.6."""
    choices = choose_query(load_settings(profile="local"))

    assert [c.step for c in choices.steps] == list(QUERY_STEPS)
    assert [c.describe() for c in choices.steps] == [
        "q2_prefilter acl_and_cedar -> acl (not built yet)",
        "q3_retrieve hybrid_rrf(top_k=5)",
        "q3_rerank bge_reranker -> none (not built yet)",
    ]
    assert choose_query(load_settings(profile="ci"))["q3_rerank"].describe() == "q3_rerank none"
    bedrock = choose_query(load_settings(profile="bedrock"))["q3_rerank"]
    assert (bedrock.configured, bedrock.option) == ("cohere_rerank_3_5", "none")


def test_a_query_path_without_a_rerank_setting_reranks_with_none() -> None:
    settings = load_settings(profile="ci")
    steps = {**settings.steps, "q3_retrieve": StepChoice(option="hybrid_rrf")}
    choices = choose_query(settings.model_copy(update={"steps": steps}))
    assert choices["q3_rerank"].describe() == "q3_rerank none"
    assert choices.params("q3_retrieve") == {}
