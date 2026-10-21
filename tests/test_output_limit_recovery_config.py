"""Bounded output-limit recovery configuration and Runtime overrides."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from agent_config import AgentConfig

ROOT = Path(__file__).resolve().parents[1]


def _config(tmp_path: Path, **overrides: object) -> Path:
    value = json.loads((ROOT / "atrex-agent.json").read_text())
    value.pop("output_limit_recovery_retries", None)
    value.update(overrides)
    shutil.copytree(ROOT / "prompts", tmp_path / "prompts")
    (tmp_path / "atrex-agent.json").write_text(json.dumps(value))
    return tmp_path


def test_missing_output_limit_recovery_configuration_defaults_to_two(tmp_path: Path) -> None:
    assert AgentConfig.load(_config(tmp_path), environment={}).output_limit_recovery_retries == 2


@pytest.mark.parametrize("retries", (0, 1, 2, 10))
def test_output_limit_recovery_configuration(tmp_path: Path, retries: int) -> None:
    repository = _config(tmp_path, output_limit_recovery_retries=retries)
    assert AgentConfig.load(repository, environment={}).output_limit_recovery_retries == retries


@pytest.mark.parametrize("retries", (-1, 11, True, 2.0, "2", None))
def test_invalid_output_limit_recovery_configuration(tmp_path: Path, retries: object) -> None:
    with pytest.raises(ValueError, match="output_limit_recovery_retries"):
        AgentConfig.load(_config(tmp_path, output_limit_recovery_retries=retries), environment={})


@pytest.mark.parametrize("retries", ("0", "3", "10"))
def test_runtime_override(tmp_path: Path, retries: str) -> None:
    config = AgentConfig.load(
        _config(tmp_path, output_limit_recovery_retries=2),
        environment={"ATREX_OUTPUT_LIMIT_RECOVERY_RETRIES": retries},
    )
    assert config.output_limit_recovery_retries == int(retries)


@pytest.mark.parametrize("retries", ("-1", "11", "true", "2.0", "", "\uff12"))
def test_invalid_runtime_override(tmp_path: Path, retries: str) -> None:
    with pytest.raises(ValueError, match="output_limit_recovery_retries"):
        AgentConfig.load(
            _config(tmp_path),
            environment={"ATREX_OUTPUT_LIMIT_RECOVERY_RETRIES": retries},
        )


def test_recovery_and_report_completion_limits_are_independent(tmp_path: Path) -> None:
    config = AgentConfig.load(
        _config(tmp_path, output_limit_recovery_retries=1, report_completion_retries=3),
        environment={"ATREX_OUTPUT_LIMIT_RECOVERY_RETRIES": "2"},
    )
    assert config.output_limit_recovery_retries == 2
    assert config.report_completion_retries == 3
