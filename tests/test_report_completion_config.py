"""Bounded report-completion configuration and Runtime overrides."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from agent_config import AgentConfig

ROOT = Path(__file__).resolve().parents[1]


def _config(tmp_path: Path, **overrides: object) -> Path:
    value = json.loads((ROOT / "atrex-agent.json").read_text())
    value.pop("report_completion_retries", None)
    value.update(overrides)
    shutil.copytree(ROOT / "prompts", tmp_path / "prompts")
    (tmp_path / "atrex-agent.json").write_text(json.dumps(value))
    return tmp_path


def test_missing_report_completion_configuration_defaults_to_two(tmp_path: Path) -> None:
    assert AgentConfig.load(_config(tmp_path), environment={}).report_completion_retries == 2


@pytest.mark.parametrize("retries", (0, 1, 2, 10))
def test_report_completion_configuration(tmp_path: Path, retries: int) -> None:
    repository = _config(tmp_path, report_completion_retries=retries)
    assert AgentConfig.load(repository, environment={}).report_completion_retries == retries


@pytest.mark.parametrize("retries", (-1, 11, True, 2.0, "2", None))
def test_invalid_report_completion_configuration(tmp_path: Path, retries: object) -> None:
    with pytest.raises(ValueError, match="report_completion_retries"):
        AgentConfig.load(_config(tmp_path, report_completion_retries=retries), environment={})


@pytest.mark.parametrize("retries", ("0", "3", "10"))
def test_runtime_override(tmp_path: Path, retries: str) -> None:
    config = AgentConfig.load(
        _config(tmp_path, report_completion_retries=2),
        environment={"ATREX_REPORT_COMPLETION_RETRIES": retries},
    )
    assert config.report_completion_retries == int(retries)


@pytest.mark.parametrize("retries", ("-1", "11", "true", "2.0", "", "\uff12"))
def test_invalid_runtime_override(tmp_path: Path, retries: str) -> None:
    with pytest.raises(ValueError, match="report_completion_retries"):
        AgentConfig.load(
            _config(tmp_path),
            environment={"ATREX_REPORT_COMPLETION_RETRIES": retries},
        )
