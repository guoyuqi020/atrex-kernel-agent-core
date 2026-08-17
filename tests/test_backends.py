from __future__ import annotations

import sys
from pathlib import Path

import pytest

from backends.adapter import ClaudeAdapter, CodexAdapter, PiAdapter, QoderAdapter
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


def test_process_capture_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import backends.process as process

    monkeypatch.setattr(process, "MAX_CAPTURE_CHARS", 128)
    stdout, stderr, returncode, timed_out = run_bounded(
        [sys.executable, "-c", "print('x' * 4096, flush=True)"],
        tmp_path,
        timeout=5,
    )

    assert len(stdout) <= 128
    assert "bounded capture limit" in stderr
    assert returncode != 0
    assert timed_out is False
