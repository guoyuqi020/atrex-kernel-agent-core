from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pytest

import backends
from agent_config import AgentConfig
from backends.adapter import ClaudeAdapter
from backends.codex_ledger import CodexLedgerError, CodexSessionLedgerObserver
from backends.model import (
    AgentRunRequest,
    AgentRunResult,
    AgentRuntimeCapabilities,
    RawSessionFile,
    TokenUsage,
)
from backends.process import ProcessObserver, ProcessResult
from backends.runtime import CliAgentRuntime
from sessions.common import execute_agent_session, write_trace


@dataclass(frozen=True)
class _Context:
    workspace: Path
    token_usage_path: Path
    session_trace_path: Path | None
    manifest: dict[str, Any]
    usage_unit: str = "provider_tokens"
    usage_budget: float = 1_000
    timeout_seconds: float = 60


def test_core_session_preserves_unredacted_prompt_and_provider_streams(
    tmp_path: Path,
) -> None:
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    prompt = "private optimizer prompt"
    stdout = (
        "\n".join(
            (
                json.dumps(
                    {
                        "type": "assistant",
                        "provider_credential": "Bearer raw-provider-secret",
                        "message": {
                            "id": "message-1",
                            "content": [
                                {"type": "thinking", "thinking": "raw reasoning"},
                                {"type": "tool_use", "input": {"token": "raw-tool-secret"}},
                                {"type": "tool_result", "content": "raw tool result"},
                            ],
                            "usage": {
                                "input_tokens": 20,
                                "output_tokens": 5,
                                "cache_read_input_tokens": 2,
                                "cache_creation_input_tokens": 1,
                            },
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "result",
                        "usage": {
                            "input_tokens": 20,
                            "output_tokens": 5,
                            "cache_read_input_tokens": 2,
                            "cache_creation_input_tokens": 1,
                        },
                    }
                ),
            )
        )
        + "\n"
    )

    def fake_runner(
        command: list[str],
        cwd: Path,
        timeout: int | None,
        env: dict[str, str] | None = None,
        observer: ProcessObserver | None = None,
    ) -> ProcessResult:
        del command, cwd, timeout, env, observer
        return ProcessResult(
            stdout=stdout,
            stderr="raw stderr credential",
            returncode=0,
            timed_out=False,
            output_overflow=False,
            policy_diagnostics=(),
        )

    runtime = CliAgentRuntime(ClaudeAdapter(), process_runner=fake_runner)
    result = runtime.run(
        AgentRunRequest(
            workspace=tmp_path,
            prompt=prompt,
            timeout_s=30,
            session_id="session-test",
            usage_budget=1_000,
        )
    )
    result = replace(
        result,
        raw_session_files=(
            RawSessionFile(
                "provider/codex-rollout.raw-jsonl",
                b'{"raw":"codex reasoning and tool result"}\n',
            ),
        ),
    )
    context = _Context(
        workspace=tmp_path,
        token_usage_path=tmp_path / "scratch/token-usage.json",
        session_trace_path=sessions / "core",
        manifest={},
    )

    config = AgentConfig("claude", "max", "", {}, model="lineage-model")
    write_trace(context, result, prompt, config)

    trace = sessions / "core"
    assert (trace / "input/prompt.md").read_text() == prompt
    assert (trace / "provider/stdout.stream-json").read_text() == stdout
    assert "raw reasoning" in (trace / "provider/stdout.stream-json").read_text()
    assert "raw-tool-secret" in (trace / "provider/stdout.stream-json").read_text()
    assert "raw stderr credential" in (trace / "provider/stderr.log").read_text()
    assert "codex reasoning" in (trace / "provider/codex-rollout.raw-jsonl").read_text()
    conversation = [
        json.loads(line) for line in (trace / "conversation.jsonl").read_text().splitlines()
    ]
    assert conversation[0]["type"] == "session_start"
    assert conversation[0]["provider_system_prompt"]["captured"] is False
    assert conversation[1]["type"] == "message"
    assert conversation[1]["role"] == "user"
    assert conversation[1]["content"] == [{"type": "text", "text": prompt}]
    assert conversation[2]["event"]["provider_credential"] == "Bearer raw-provider-secret"
    assert any(
        row.get("path") == "provider/codex-rollout.raw-jsonl"
        and row.get("event", {}).get("raw") == "codex reasoning and tool result"
        for row in conversation
    )
    assert conversation[-1]["type"] == "session_end"
    assert conversation[-1]["raw_provider_capture_complete"] is True
    normalized = (trace / "events.jsonl").read_text().splitlines()
    assert json.loads(normalized[0]) == {
        "id": "session-test",
        "type": "session",
        "version": 0,
    }
    assert all(json.loads(line)["ignorable"] is True for line in normalized[1:])
    metadata = json.loads((trace / "session.json").read_text())
    assert metadata["raw_provider_capture_complete"] is True
    assert metadata["conversation_capture_complete"] is True
    assert metadata["provider_system_prompt_capture"] == "provider_managed_unavailable"
    assert metadata["runtime_id"] == "claude"
    assert metadata["reasoning_effort"] == "max"
    assert metadata["model"] == "lineage-model"

    blocked = replace(context, session_trace_path=sessions / "blocked")
    assert blocked.session_trace_path is not None
    blocked.session_trace_path.mkdir()
    with pytest.raises(ValueError, match="must not be created"):
        write_trace(blocked, result, prompt, config)

    unsafe = replace(
        result,
        raw_session_files=(RawSessionFile("provider/../escaped", b"escape"),),
    )
    unsafe_context = replace(context, session_trace_path=sessions / "unsafe")
    with pytest.raises(ValueError, match="unsafe path"):
        write_trace(unsafe_context, unsafe, prompt, config)
    assert not (sessions / "unsafe").exists()


def test_codex_rollout_is_captured_before_temporary_home_cleanup(tmp_path: Path) -> None:
    thread_id = "01234567-89ab-cdef-0123-456789abcdef"
    rollout = tmp_path / "sessions/2026" / f"rollout-test-{thread_id}.jsonl"
    rollout.parent.mkdir(parents=True)
    payload = b'{"type":"response_item","payload":{"secret":"raw"}}\n'
    rollout.write_bytes(payload)
    observer = CodexSessionLedgerObserver(tmp_path)

    assert observer.capture_raw_rollout(thread_id, max_bytes=1024) == payload
    with pytest.raises(CodexLedgerError, match="byte limit"):
        observer.capture_raw_rollout(thread_id, max_bytes=1)


def test_core_projects_provider_streams_while_session_is_running(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    stdout = (
        json.dumps(
            {
                "type": "result",
                "usage": {
                    "input_tokens": 20,
                    "output_tokens": 5,
                    "cache_read_input_tokens": 2,
                    "cache_creation_input_tokens": 1,
                },
            }
        )
        + "\n"
    )
    stderr = "live provider diagnostic\n"

    def fake_runner(
        command: list[str],
        cwd: Path,
        timeout: int | None,
        env: dict[str, str] | None = None,
        observer: ProcessObserver | None = None,
    ) -> ProcessResult:
        del command, cwd, timeout, env
        trace = sessions / "core"
        assert json.loads((trace / "session.json").read_text())["state"] == "running"
        assert observer is not None
        assert observer.on_stdout_line(stdout) is False
        assert observer.on_stderr_line(stderr) is False
        assert (trace / "provider/stdout.stream-json").read_text() == stdout
        assert (trace / "provider/stderr.log").read_text() == stderr
        live_conversation = [
            json.loads(line) for line in (trace / "conversation.jsonl").read_text().splitlines()
        ]
        assert live_conversation[1]["role"] == "user"
        assert live_conversation[-1]["event"]["type"] == "result"
        return ProcessResult(stdout, stderr, 0, False, False, ())

    runtime = CliAgentRuntime(ClaudeAdapter(), process_runner=fake_runner)
    monkeypatch.setattr(backends, "build_agent_runtime", lambda _backend: runtime)
    context = _Context(
        workspace=tmp_path,
        token_usage_path=tmp_path / "scratch/token-usage.json",
        session_trace_path=sessions / "core",
        manifest={},
    )

    assert execute_agent_session(context, AgentConfig("claude", "max", "", {}), "prompt") == 0

    trace = sessions / "core"
    assert json.loads((trace / "session.json").read_text())["state"] == "finished"
    assert not (trace / ".runtime-live-session").exists()
    assert (trace / "provider/stdout.stream-json").read_text() == stdout
    assert (trace / "provider/stderr.log").read_text() == stderr
    conversation = [
        json.loads(line) for line in (trace / "conversation.jsonl").read_text().splitlines()
    ]
    assert conversation[1]["content"] == [{"type": "text", "text": "prompt"}]
    assert conversation[-1]["type"] == "session_end"


def test_codex_ledger_normalizes_current_usage_without_cache_write(tmp_path: Path) -> None:
    thread_id = "01234567-89ab-cdef-0123-456789abcdef"
    rollout = tmp_path / "sessions/2026" / f"rollout-test-{thread_id}.jsonl"
    rollout.parent.mkdir(parents=True)
    usage = {
        "input_tokens": 16_002,
        "cached_input_tokens": 9_984,
        "output_tokens": 723,
        "reasoning_output_tokens": 348,
        "total_tokens": 16_725,
    }
    rollout.write_text(
        json.dumps(
            {
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "last_token_usage": usage,
                        "total_token_usage": usage,
                    },
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    observation = CodexSessionLedgerObserver(tmp_path).observe(thread_id)

    assert observation.terminal_usage == TokenUsage(
        input_tokens=6_018,
        output_tokens=723,
        cache_read_tokens=9_984,
        cache_write_tokens=0,
        total_tokens=16_725,
        measurement="exact",
    )
    assert [event.kind for event in observation.events] == [
        "usage_delta",
        "terminal_usage",
    ]


def test_incomplete_raw_provider_capture_fails_the_phase(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    result = AgentRunResult(
        runtime_id="codex",
        exit_status=0,
        timed_out=False,
        terminal_usage=TokenUsage.zero(),
        events=(),
        capabilities=AgentRuntimeCapabilities(terminal_usage=True, usage_delta=True),
        observation_errors=("codex_raw_rollout_capture_unavailable",),
        stdout='{"type":"thread.started"}\n',
        stderr="",
        raw_session_files=(),
        raw_provider_capture_complete=False,
        policy_diagnostics=(),
        session_id="session-incomplete",
    )

    class _Runtime:
        id = "codex"

        def run(self, request: AgentRunRequest) -> AgentRunResult:
            del request
            return result

    monkeypatch.setattr(backends, "build_agent_runtime", lambda _backend: _Runtime())
    context = _Context(
        workspace=tmp_path,
        token_usage_path=tmp_path / "scratch/token-usage.json",
        session_trace_path=sessions / "core",
        manifest={},
    )
    config = AgentConfig("codex", "max", "", {})

    assert execute_agent_session(context, config, "prompt") == 126
    metadata = json.loads((sessions / "core/session.json").read_text())
    assert metadata["raw_provider_capture_complete"] is False
    assert context.token_usage_path.is_file()
