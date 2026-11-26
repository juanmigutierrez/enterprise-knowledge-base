"""Options from config (S12): what each profile names, and what actually runs."""

from __future__ import annotations

import pytest

from ekb.app.choices import (
    ASSISTANT_STEPS,
    INFRA_STEPS,
    PIPELINE_STEPS,
    QUERY_STEPS,
    Choice,
    NoStandInError,
    choose,
    choose_assistant,
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
    infra = {"bus", "registry", "rawstore", "search", "entities", "permissions"}
    assert {c.why for c in choices.swapped if c.step in infra} == {"in memory: no --docker"}
    assert {c.why for c in choices.swapped if c.step not in infra} == {"not built yet"}
    assert choices.params("rawstore") == {}  # the s3 bucket means nothing to in_memory
    assert not choices.on_docker
    assert choices.calls_models


def test_docker_runs_the_real_infrastructure_in_the_given_namespace() -> None:
    """S17: boxes 3 and 5 run in memory unless asked, the way --models works for models."""
    choices = choose(load_settings(profile="local"), docker=True, namespace="demo-1a2b3c4d")
    assert {s: choices.option(s) for s in INFRA_STEPS} == {
        "bus": "kafka",
        "registry": "dynamodb",
        "rawstore": "s3",
        "search": "opensearch",
        "entities": "dynamodb",
        "permissions": "dynamodb",
    }
    assert choices.params("rawstore") == {"bucket": "ekb-raw", "namespace": "demo-1a2b3c4d"}
    assert choices.params("bus") == {"namespace": "demo-1a2b3c4d"}
    assert choices.on_docker
    assert choose(load_settings(profile="local"), docker=True).params("bus") == {}


def test_the_ci_profile_stays_in_memory_even_with_docker() -> None:
    choices = choose(load_settings(profile="ci"), docker=True, namespace="x")
    assert {choices.option(s) for s in INFRA_STEPS} == {"in_memory"}
    assert not choices.on_docker
    assert all(choices.params(s) == {} for s in INFRA_STEPS)


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
    """S13: acl_and_cedar runs as acl until S15b, the configured reranker as none until 4.6.
    S14: rewrite_when_needed runs as rules until card 4.8; Q4 redacts with rules."""
    choices = choose_query(load_settings(profile="local"))

    assert [c.step for c in choices.steps] == list(QUERY_STEPS)
    assert [c.describe() for c in choices.steps] == [
        "q1_plan rewrite_when_needed -> rules (not built yet)",
        "q2_prefilter acl_and_cedar -> acl (not built yet)",
        "q3_retrieve hybrid_rrf(top_k=5)",
        "q3_rerank bge_reranker -> none (not built yet)",
        "q4_postcheck source_recheck",
        "q4_redact rules",
    ]
    assert choose_query(load_settings(profile="ci"))["q1_plan"].describe() == "q1_plan none"
    assert choose_query(load_settings(profile="ci"))["q3_rerank"].describe() == "q3_rerank none"
    bedrock = choose_query(load_settings(profile="bedrock"))["q3_rerank"]
    assert (bedrock.configured, bedrock.option) == ("cohere_rerank_3_5", "none")


def test_a_query_path_without_a_rerank_setting_reranks_with_none() -> None:
    settings = load_settings(profile="ci")
    steps = {**settings.steps, "q3_retrieve": StepChoice(option="hybrid_rrf")}
    choices = choose_query(settings.model_copy(update={"steps": steps}))
    assert choices["q3_rerank"].describe() == "q3_rerank none"
    assert choices.params("q3_retrieve") == {}


def test_q4s_redact_setting_names_the_redactor_and_its_absence_means_rules() -> None:
    """S14: like Q3's rerank, the redactor is a setting of Q4, not a step of its own."""
    settings = load_settings(profile="ci")
    assert choose_query(settings)["q4_redact"].describe() == "q4_redact rules"
    steps = {
        **settings.steps,
        "q4_postcheck": StepChoice(option="source_recheck", params={"redact": "none"}),
    }
    choices = choose_query(settings.model_copy(update={"steps": steps}))
    assert choices["q4_redact"].describe() == "q4_redact none"
    assert choices.params("q4_postcheck") == {}
    steps["q4_postcheck"] = StepChoice(option="source_recheck", params={"redact": "presidio"})
    with pytest.raises(NoStandInError, match="q4_redact: 'presidio' is not built yet"):
        choose_query(settings.model_copy(update={"steps": steps}))


def test_the_knowledge_api_s_steps_come_from_the_profile_and_go_offline_on_request() -> None:
    """S15: the token check, the answer writer and the answer cache."""
    local = choose_assistant(load_settings(profile="local"))
    assert [c.step for c in local.steps] == list(ASSISTANT_STEPS)
    assert local.option("auth") == "static_tokens"
    assert local.params("auth")["users"] == ["alice", "bob", "carla", "emma", "olivia"]
    assert local["answer"].describe() == "answer model"
    assert local.calls_models
    offline = choose_assistant(load_settings(profile="local"), offline=True)
    assert offline["answer"].describe() == "answer model -> extractive (offline: no model)"
    assert not offline.calls_models
    ci = choose_assistant(load_settings(profile="ci"))
    assert ci["answer"].describe() == "answer extractive"
    assert ci["answer_cache"].describe() == "answer_cache in_memory"
