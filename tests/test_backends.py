from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from backends.adapter import ClaudeAdapter, CodexAdapter, PiAdapter, QoderAdapter
from backends.model import TokenUsage
from backends.process import run_bounded


def test_every_backend_builds_one_fresh_noninteractive_command() -> None:
    commands = {
        "claude": ClaudeAdapter().build_command("prompt", "session", "high", ""),
        "qodercli": QoderAdapter().build_command("prompt", "session", "high", ""),
        "pi": PiAdapter().build_command("prompt", "session", "high", ""),
        "codex": CodexAdapter().build_command("prompt", "session", "high", ""),
    }

    assert commands["claude"][:2] == ["claude", "--print"]
    assert "--no-session-persistence" in commands["qodercli"]
    assert commands["pi"][:3] == ["pi", "--mode", "json"]
    assert commands["codex"][:3] == ["codex", "exec", "--json"]
    assert all(command[-1] == "prompt" for command in commands.values())


def test_codex_terminal_usage_uses_disjoint_cache_buckets() -> None:
    stdout = json.dumps(
        {
            "type": "turn.completed",
            "usage": {
                "input_tokens": 16_002,
                "cached_input_tokens": 9_984,
                "output_tokens": 723,
            },
        }
    )

    events, terminal = CodexAdapter().normalize_stream(stdout)

    assert terminal == TokenUsage(6_018, 723, 9_984, 0, 16_725, "exact")
    assert len(events) == 1 and events[0].kind == "terminal_usage"


def test_process_capture_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import backends.process as process

    monkeypatch.setattr(process, "MAX_CAPTURE_CHARS", 128)
    result = run_bounded(
        [sys.executable, "-c", "print('x' * 4096, flush=True)"],
        tmp_path,
        timeout=5,
    )

    assert len(result.stdout) <= 128
    assert result.stderr == ""
    assert result.output_overflow is True
    assert "bounded capture limit" in result.policy_diagnostics[0]
    assert result.returncode != 0
    assert result.timed_out is False
