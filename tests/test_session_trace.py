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
    token_budget: int = 1_000
    timeout_seconds: float = 60


def test_core_session_preserves_unredacted_prompt_and_provider_streams(
    tmp_path: Path,
) -> None:
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    prompt = "private optimizer prompt"
    stdout = "\n".join(
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
    ) + "\n"

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
            token_budget=1_000,
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

    write_trace(context, result, prompt)

    trace = sessions / "core"
    assert (trace / "input/prompt.md").read_text() == prompt
    assert (trace / "provider/stdout.stream-json").read_text() == stdout
    assert "raw reasoning" in (trace / "provider/stdout.stream-json").read_text()
    assert "raw-tool-secret" in (trace / "provider/stdout.stream-json").read_text()
    assert "raw stderr credential" in (trace / "provider/stderr.log").read_text()
    assert "codex reasoning" in (
        trace / "provider/codex-rollout.raw-jsonl"
    ).read_text()
    normalized = (trace / "events.jsonl").read_text().splitlines()
    assert json.loads(normalized[0]) == {
        "id": "session-test",
        "type": "session",
        "version": 0,
    }
    assert all(json.loads(line)["ignorable"] is True for line in normalized[1:])
    metadata = json.loads((trace / "session.json").read_text())
    assert metadata["raw_provider_capture_complete"] is True

    blocked = replace(context, session_trace_path=sessions / "blocked")
    assert blocked.session_trace_path is not None
    blocked.session_trace_path.mkdir()
    with pytest.raises(ValueError, match="must not be created"):
        write_trace(blocked, result, prompt)

    unsafe = replace(
        result,
        raw_session_files=(RawSessionFile("provider/../escaped", b"escape"),),
    )
    unsafe_context = replace(context, session_trace_path=sessions / "unsafe")
    with pytest.raises(ValueError, match="unsafe path"):
        write_trace(unsafe_context, unsafe, prompt)
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
