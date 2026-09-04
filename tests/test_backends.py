from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from backends.adapter import ClaudeAdapter, CodexAdapter, PiAdapter, QoderAdapter
from backends.codex_ledger import CodexTemporaryHome
from backends.model import AgentRunRequest, TokenUsage
from backends.process import ProcessObserver, ProcessResult, run_bounded
from backends.runtime import CodexRuntime


def test_every_backend_builds_one_fresh_noninteractive_command() -> None:
    commands = {
        "claude": ClaudeAdapter().build_command("prompt", "session", "high", ""),
        "qodercli": QoderAdapter().build_command("prompt", "session", "high", ""),
        "pi": PiAdapter().build_command("prompt", "session", "high", ""),
        "codex": CodexAdapter().build_command("prompt", "session", "high", ""),
    }

    assert commands["claude"][:2] == ["claude", "--print"]
    assert "--no-session-persistence" not in commands["claude"]
    assert commands["claude"][commands["claude"].index("--name") + 1] == "atrex-session"
    assert "--no-session-persistence" in commands["qodercli"]
    assert commands["pi"][:3] == ["pi", "--mode", "json"]
    assert commands["codex"][:3] == ["codex", "exec", "--json"]
    assert all(command[-1] == "prompt" for command in commands.values())


def test_every_backend_receives_the_lineage_selected_model() -> None:
    commands = {
        "claude": ClaudeAdapter().build_command("prompt", "session", "high", "", "m"),
        "qodercli": QoderAdapter().build_command("prompt", "session", "high", "", "m"),
        "pi": PiAdapter().build_command("prompt", "session", "high", "", "m"),
        "codex": CodexAdapter().build_command("prompt", "session", "high", "", "m"),
    }

    for backend in ("claude", "qodercli", "pi"):
        index = commands[backend].index("--model")
        assert commands[backend][index + 1] == "m"
    assert 'model="m"' in commands["codex"]


def test_native_system_prompt_channel_keeps_it_out_of_the_conversation() -> None:
    contract = "## Session tools\n\nrun the tool"

    for adapter in (ClaudeAdapter(), QoderAdapter()):
        command = adapter.build_command("prompt", "session", "high", "", None, contract)
        index = command.index("--append-system-prompt")
        assert command[index + 1] == contract
        assert command[-1] == "prompt"


def test_backends_without_a_system_prompt_flag_fold_it_into_the_prompt() -> None:
    contract = "## Session tools\n\nrun the tool"

    for adapter in (PiAdapter(), CodexAdapter()):
        command = adapter.build_command("prompt", "session", "high", "", None, contract)
        assert "--append-system-prompt" not in command
        assert command[-1] == contract + "\n\nprompt"


def test_an_absent_system_prompt_adds_no_argument() -> None:
    for adapter in (ClaudeAdapter(), QoderAdapter(), PiAdapter(), CodexAdapter()):
        command = adapter.build_command("prompt", "session", "high", "")
        assert "--append-system-prompt" not in command
        assert command[-1] == "prompt"


def test_structured_session_settings_cannot_override_runtime_model() -> None:
    with pytest.raises(ValueError, match="both Runtime and session settings"):
        PiAdapter().build_command("prompt", "session", "high", '{"model":"other"}', "m")
    with pytest.raises(ValueError, match="both Runtime and session settings"):
        CodexAdapter().build_command("prompt", "session", "high", '{"model":"other"}', "m")


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


def test_qoder_uses_native_credits_instead_of_zero_token_counters() -> None:
    stdout = "\n".join(
        (
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "id": "qoder-1",
                        "usage": {
                            "input_tokens": 0,
                            "output_tokens": 0,
                            "credits": 13.75,
                        },
                    },
                }
            ),
            json.dumps({"type": "result", "total_credits": 13.75, "usage": {"input_tokens": 0}}),
        )
    )

    events, terminal = QoderAdapter().normalize_stream(stdout)

    assert terminal == TokenUsage.credit(13.75)
    assert [event.kind for event in events] == ["usage_delta", "terminal_usage"]


def test_codex_installation_identity_is_a_writable_session_copy(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "auth.json").write_text("{}")
    (source / "installation_id").write_text("install-1")
    temporary = CodexTemporaryHome(source)
    home = temporary.open()
    try:
        assert (home / "auth.json").is_symlink()
        assert not (home / "installation_id").is_symlink()
        (home / "installation_id").write_text("session-install")
        assert (source / "installation_id").read_text() == "install-1"
    finally:
        assert temporary.close() is None


@pytest.mark.parametrize("installed", ["0", "1"])
def test_codex_hook_trust_is_invocation_local(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    installed: str,
) -> None:
    home = tmp_path / "private-codex"
    home.mkdir()
    (home / "hooks.json").write_text('{"hooks":{}}')
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.setenv("ATREX_OPTIMIZER_CODEX_HOOKS", installed)

    def runner(
        command: list[str],
        cwd: Path,
        timeout: int | None,
        env: dict[str, str] | None = None,
        observer: ProcessObserver | None = None,
    ) -> ProcessResult:
        assert ("--dangerously-bypass-hook-trust" in command) == (installed == "1")
        assert env is not None
        assert env["CODEX_HOME"] != str(home)
        assert (Path(env["CODEX_HOME"]) / "hooks.json").read_text() == '{"hooks":{}}'
        return ProcessResult("", "test process exited before model invocation", 1, False, False, ())

    CodexRuntime(process_runner=runner).run(AgentRunRequest(tmp_path, "test", 30))
    assert (home / "hooks.json").read_text() == '{"hooks":{}}'
    assert not (home / "config.toml").exists()


def test_process_stdout_capture_is_unbounded(tmp_path: Path) -> None:
    result = run_bounded(
        [sys.executable, "-c", "print('x' * 4096, flush=True)"],
        tmp_path,
        timeout=5,
    )

    assert len(result.stdout) == 4097
    assert result.stderr == ""
    assert result.output_overflow is False
    assert result.policy_diagnostics == ()
    assert result.returncode == 0
    assert result.timed_out is False


def test_process_filters_thinking_token_events_before_stdout_capture(tmp_path: Path) -> None:
    thinking = json.dumps({"type": "system", "subtype": "thinking_tokens"}) + "\n"
    terminal = json.dumps({"type": "result", "usage": {"input_tokens": 3}}) + "\n"
    payload = thinking * 1000 + terminal

    result = run_bounded(
        [sys.executable, "-c", f"import sys; sys.stdout.write({payload!r})"],
        tmp_path,
        timeout=5,
    )

    assert result.stdout == terminal
    assert result.returncode == 0
    assert result.output_overflow is False


def test_process_stderr_capture_remains_bounded(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import backends.process as process

    monkeypatch.setattr(process, "MAX_STDERR_CAPTURE_CHARS", 128)
    result = run_bounded(
        [sys.executable, "-c", "import sys; print('x' * 4096, file=sys.stderr, flush=True)"],
        tmp_path,
        timeout=5,
    )

    assert result.stdout == ""
    assert len(result.stderr) <= 128
    assert result.output_overflow is True
    assert "stderr exceeded the bounded capture limit" in result.policy_diagnostics[0]
    assert result.returncode != 0
    assert result.timed_out is False
