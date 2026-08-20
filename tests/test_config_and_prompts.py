from __future__ import annotations

import re
from pathlib import Path

from agent_config import AgentConfig
from sessions.attempt import _tool_instructions

CORE_ROOT = Path(__file__).resolve().parents[1]


def test_agent_config_loads_every_supported_phase() -> None:
    config = AgentConfig.load(CORE_ROOT)

    assert config.agent_backend == "codex"
    assert set(config.prompt_paths) == {
        "problem_generalization",
        "framework_baseline",
        "optimization_attempt",
    }


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
    instructions = _tool_instructions("triton")

    assert '"query": "triton vectorized load requirements' in instructions
    assert "wiki-read" not in instructions
    assert "complete safe served Record" in instructions
    assert "CUDA vectorized load requirements" not in instructions
