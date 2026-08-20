from __future__ import annotations

from pathlib import Path

import pytest

from agent_config import AgentConfig

CORE_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("backend", ("claude", "codex", "qodercli", "pi"))
def test_runtime_binding_overrides_bundle_backend(backend: str) -> None:
    config = AgentConfig.load(
        CORE_ROOT,
        {
            "ATREX_AGENT_BACKEND": backend,
            "ATREX_AGENT_MODEL": "runtime-model",
            "ATREX_AGENT_REASONING_EFFORT": "high",
            "ATREX_AGENT_SESSION_SETTINGS": "",
        },
    )

    assert config.agent_backend == backend
    assert config.model == "runtime-model"
    assert config.reasoning_effort == "high"
    assert config.runtime_bound is True


def test_runtime_binding_must_be_complete() -> None:
    with pytest.raises(ValueError, match="incomplete Runtime Agent binding"):
        AgentConfig.load(CORE_ROOT, {"ATREX_AGENT_BACKEND": "codex"})
