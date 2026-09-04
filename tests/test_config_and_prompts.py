from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from agent_config import AgentConfig
from runtime_tools import _ATTEMPT_COMMANDS
from sessions.attempt import _render_prompt_fragment, _tool_instructions
from sessions.operator_contract import public_operator_contract

CORE_ROOT = Path(__file__).resolve().parents[1]


def test_managed_prompt_paths_read_workspace_state(tmp_path: Path) -> None:
    repository = tmp_path / "agent/optimizer"
    repository.mkdir(parents=True)
    value = json.loads((CORE_ROOT / "atrex-agent.json").read_text())
    value["prompt_root"] = "workspace"
    (repository / "atrex-agent.json").write_text(json.dumps(value))
    shutil.copytree(CORE_ROOT / "prompts", tmp_path / "prompts")
    config = AgentConfig.load(repository, environment={}, workspace=tmp_path)
    assert config.prompt_path("optimization_attempt") == tmp_path / "prompts/episode.md"
    assert config.prompt_fragment_path("attempt_tools") == tmp_path / "prompts/attempt-tools.md"
    (tmp_path / "prompts/episode.md").write_text("Next session's learned methodology")
    next_config = AgentConfig.load(repository, environment={}, workspace=tmp_path)
    assert next_config.prompt_path("optimization_attempt").read_text() == (
        "Next session's learned methodology"
    )
    assert not (repository / "prompts").exists()
    with pytest.raises(ValueError, match="requires the Runtime session workspace"):
        AgentConfig.load(repository, environment={})
    value["prompts"]["optimization_attempt"] = "prompts/../input/private.json"
    (repository / "atrex-agent.json").write_text(json.dumps(value))
    with pytest.raises(ValueError, match="safe relative path"):
        AgentConfig.load(repository, environment={}, workspace=tmp_path)


def test_core_contains_indexed_initial_runtime_state() -> None:
    for name in ("prompts", "memory", "knowledge", "skills", "tools", "hooks"):
        readme = (CORE_ROOT / name / "README.md").read_text()
        assert "Whenever you add, change, rename, or remove" in readme
        assert "README" in readme
    assert (CORE_ROOT / "docs/design.md").is_file()


def test_public_operator_contract_hides_non_actionable_provenance() -> None:
    prompt = public_operator_contract(
        {
            "schema_version": "atrex.shape_train.v1",
            "generator": {
                "name": "benchmark-converter-shape-train",
                "version": 2,
                "domain_policy": "trace_bounded_non_enumerating",
            },
            "objective": "Optimize the public domain.",
            "operator_contract": {
                "operation": "normalization",
                "fixed_init_kwargs": None,
                "fixed_parameters": {"heads": 4, "head_dim": 128},
            },
            "shape_domain": {
                "tokens": {
                    "type": "integer",
                    "min": 1,
                    "max": 16384,
                    "range_evidence": {"lower": {"artifact": "private provenance"}},
                },
                "heads": {
                    "type": "integer",
                    "values": [4],
                    "value_evidence": {"artifact": "private provenance"},
                },
                "head_dim": {"type": "integer", "values": [128]},
            },
            "invariants": [
                "tokens >= 1",
                "tokens <= 16384",
                "heads == 4",
                "head_dim == 128",
                "preserve output layout",
            ],
        }
    )

    assert '"objective": "Optimize the public domain."' in prompt
    assert '"tokens": {' in prompt
    assert '"min": 1' in prompt
    assert '"max": 16384' in prompt
    assert '"preserve output layout"' in prompt
    assert "schema_version" not in prompt
    assert "benchmark-converter-shape-train" not in prompt
    assert "domain_policy" not in prompt
    assert "fixed_init_kwargs" not in prompt
    assert '"init_kwargs": null' not in prompt
    assert "range_evidence" not in prompt
    assert "value_evidence" not in prompt
    assert '"operator_contract"' not in prompt
    assert '"fixed_parameters"' not in prompt
    assert '"heads": 4' in prompt
    assert '"head_dim": 128' in prompt
    assert '"tokens >= 1"' not in prompt
    assert '"tokens <= 16384"' not in prompt
    assert '"heads == 4"' not in prompt
    assert '"head_dim == 128"' not in prompt


def test_public_operator_contract_preserves_cross_field_invariants() -> None:
    prompt = public_operator_contract(
        {
            "operator_contract": {"fixed_parameters": {"heads": 4}},
            "shape_domain": {"tokens": {"type": "integer", "min": 1, "max": 16384}},
            "invariants": [
                "tokens >= 1 and tokens <= 16384",
                "packed_tokens == batch_size * sequence_length",
                "normalize each head independently",
            ],
        }
    )

    assert "tokens >= 1 and tokens <= 16384" not in prompt
    assert "packed_tokens == batch_size * sequence_length" in prompt
    assert "normalize each head independently" in prompt


def test_public_operator_contract_keeps_non_shape_abi_semantics() -> None:
    prompt = public_operator_contract(
        {
            "objective": "Optimize an in-place update.",
            "operator_contract": {
                "operation": "state update",
                "category": "UPDATE",
                "fixed_init_kwargs": {"activation": "silu"},
                "fixed_parameters": {"width": 128},
                "mutates_inputs": ["state"],
                "returns_none": True,
            },
            "shape_domain": {"tokens": {"type": "integer", "min": 1, "max": 4096}},
        }
    )

    assert '"width": 128' in prompt
    assert '"fixed_parameters"' not in prompt
    assert '"operation"' not in prompt
    assert '"category"' not in prompt
    assert '"fixed_init_kwargs": {' in prompt
    assert '"mutates_inputs": [' in prompt
    assert '"returns_none": true' in prompt


def test_legacy_agent_problem_uses_shape_domain_for_all_fixed_parameters() -> None:
    prompt = public_operator_contract(
        {
            "schema_version": "atrex.agent_problem.v1",
            "objective": "Optimize paired normalization while exact cases remain private.",
            "operator_contract": {
                "operation": "paired normalization",
                "input_dtype": "bfloat16",
                "output_dtype": "bfloat16",
                "accumulation_dtype": "float32",
                "num_heads": 16,
                "hidden_size": 128,
                "eps": 1e-6,
                "input_layout": "[1, tokens, 16, 128]",
            },
            "shape_domain": {
                "tokens": {"type": "integer", "min": 1, "max": 8192},
                "num_heads": {"type": "integer", "values": [16]},
            },
            "invariants": ["normalize each head independently"],
        }
    )

    assert '"operator_contract"' not in prompt
    assert '"tokens": {' in prompt
    assert '"num_heads": 16' in prompt
    assert '"hidden_size": 128' in prompt
    assert '"input_dtype": "bfloat16"' in prompt
    assert '"accumulation_dtype": "float32"' in prompt
    assert '"eps": 1e-06' in prompt
    assert '"input_layout": "[1, tokens, 16, 128]"' in prompt
    assert '"normalize each head independently"' in prompt


def test_agent_config_loads_every_supported_phase() -> None:
    config = AgentConfig.load(CORE_ROOT)

    assert config.agent_backend == "codex"
    assert set(config.prompt_paths) == {
        "problem_generalization",
        "framework_baseline",
        "optimization_attempt",
    }
    assert set(config.prompt_fragment_paths) == {"attempt_tools"}


def test_prompts_use_only_real_runtime_tool_names() -> None:
    text = "\n".join(path.read_text(encoding="utf-8") for path in CORE_ROOT.glob("prompts/*.md"))

    assert "gateway_execute" not in text
    assert "wiki_query" not in text
    assert "wiki_read" not in text


def test_prompts_do_not_reveal_product_or_control_plane_identity() -> None:
    text = "\n".join(path.read_text(encoding="utf-8") for path in CORE_ROOT.glob("prompts/*.md"))

    assert (
        re.search(
            r"atrex|kernel agent|runtime-owned|runtime-injected|core revision|control plane",
            text,
            re.IGNORECASE,
        )
        is None
    )


def test_attempt_tool_example_uses_the_trusted_lineage_dsl() -> None:
    config = AgentConfig.load(CORE_ROOT)
    instructions = _tool_instructions(config, "triton")

    assert '"query": "triton vectorized load requirements' in instructions
    assert "wiki-read" not in instructions
    assert "complete safe served Record" in instructions
    assert "CUDA vectorized load requirements" not in instructions
    assert "{{" not in instructions
    assert "}}" not in instructions


def test_attempt_tool_template_lists_exact_agent_facing_cli_commands() -> None:
    config = AgentConfig.load(CORE_ROOT)
    instructions = _tool_instructions(config, "triton")
    documented = tuple(
        re.findall(
            r"^python3 agent/optimizer/src/runtime_tools\.py ([a-z-]+) --request",
            instructions,
            re.MULTILINE,
        )
    )

    assert documented == _ATTEMPT_COMMANDS


def test_attempt_tool_template_json_examples_are_valid() -> None:
    config = AgentConfig.load(CORE_ROOT)
    instructions = _tool_instructions(config, "triton")

    examples = re.findall(r"```json\n(.*?)\n```", instructions, re.DOTALL)
    assert examples
    for example in examples:
        assert isinstance(json.loads(example), dict)


def test_bootstrap_prompt_matches_special_attempt_protocol() -> None:
    baseline = (CORE_ROOT / "prompts/framework_baseline.md").read_text(encoding="utf-8")
    tools = (CORE_ROOT / "prompts/attempt-tools.md").read_text(encoding="utf-8")

    assert '`action="baseline"`' in baseline
    assert "`before=null`" in baseline
    assert "`scratch/baseline-experiment.json`" in baseline
    assert "invoke `record-experiment` with that request" in baseline
    assert "The receipt returns an `experiment_id`" in baseline
    assert "when completing the Direction" in baseline
    assert "may evaluate that unchanged copy" in baseline
    assert "does not register `v0`" in baseline
    assert "When `wiki-query` is available" in baseline
    assert "Wiki absence alone is not a blocker" in baseline
    assert "Do not use `pivot` during Bootstrap" in baseline
    assert "complete private Bootstrap Gate independently" in baseline
    assert "Set `profile_evidence` to `null` unless profiling was actually" in baseline
    assert "Use the shared tool contract below" in baseline
    assert "provenance and decisions" not in baseline
    assert "Example exploratory evaluation request" in tools
    assert "authoritative evaluation request" not in tools
    assert "Runtime-authorized Lineage" in tools
    assert "injected Evidence view" not in tools


def test_prompt_fragment_rejects_placeholder_drift() -> None:
    with pytest.raises(ValueError, match="placeholder contract mismatch"):
        _render_prompt_fragment(
            "{{DSL}} {{UNKNOWN}}",
            {"DSL": "triton", "RUNTIME_TOOL": "tool.py"},
        )
