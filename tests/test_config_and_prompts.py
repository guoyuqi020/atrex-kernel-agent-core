from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from agent_config import AgentConfig
from runtime_tools import _ATTEMPT_COMMANDS
from sessions.attempt import _render_prompt_fragment, _tool_instructions

CORE_ROOT = Path(__file__).resolve().parents[1]


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
    assert "The exact Runtime tool commands and schemas appear once" in baseline
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
