from __future__ import annotations

import json
from pathlib import Path

import pytest

from backends.adapter import ClaudeAdapter
from backends.claude_ledger import ClaudeSessionLedger, observe_claude_usage
from backends.model import AgentRunRequest, TokenUsage
from backends.process import ProcessObserver, ProcessResult
from backends.runtime import ClaudeRuntime, TokenBudgetObserver


def message(message_id: str = "m1", *, output: int = 7, cached: int = 20) -> str:
    return (
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "id": message_id,
                    "model": "test-model",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tool1",
                            "name": "Bash",
                            "input": {"command": "python3 --version"},
                        }
                    ],
                    "usage": {
                        "input_tokens": 3,
                        "output_tokens": output,
                        "cache_read_input_tokens": cached,
                        "cache_creation_input_tokens": 10,
                    },
                },
            }
        )
        + "\n"
    )


def ledger(tmp_path: Path) -> tuple[ClaudeSessionLedger, Path]:
    path = tmp_path / "projects/test/session-1.jsonl"
    path.parent.mkdir(parents=True)
    return ClaudeSessionLedger({"CLAUDE_CONFIG_DIR": str(tmp_path)}, "session-1"), path


def test_last_native_usage_wins_and_is_attributable(tmp_path: Path) -> None:
    observer, path = ledger(tmp_path)
    initial = message(output=0, cached=0)
    final = message()
    path.write_text(initial + final + final)
    stream, _ = ClaudeAdapter().normalize_stream(initial)
    terminal = TokenUsage(3, 7, 20, 10, 40, "exact")
    events, actual, complete, errors = observe_claude_usage(observer.capture(), stream, terminal)
    assert actual == terminal
    assert complete and not errors
    assert len(events) == 2
    assert events[0].usage == terminal
    assert events[0].message_id == "m1"
    assert events[0].source_path == "provider/claude-session.raw-jsonl"
    assert events[1].kind == "terminal_usage"
    assert [event.sequence for event in events] == [0, 1]


def test_native_main_and_children_but_not_other_sessions(tmp_path: Path) -> None:
    observer, path = ledger(tmp_path)
    path.write_text(message())
    child = path.with_suffix("") / "subagents/agent-child.jsonl"
    child.parent.mkdir(parents=True)
    child.write_text(message("child"))
    (path.parent / "unrelated.jsonl").write_text("DO NOT COPY")
    terminal = TokenUsage(6, 14, 40, 20, 80, "exact")
    files = observer.capture()
    events, actual, complete, _errors = observe_claude_usage(files, (), terminal)
    assert len(files) == 2
    assert complete and actual == terminal
    assert {event.message_id for event in events[:-1]} == {"m1", "child"}
    assert events[1].source_path == "provider/claude-subagents/agent-child.jsonl"


def test_live_tail_preserves_updates_without_copying_lines_twice(tmp_path: Path) -> None:
    observer, path = ledger(tmp_path)
    trace = tmp_path / "trace"
    path.write_text(message(output=0) + message()[:20])
    observer.sync_live(trace)
    assert (trace / "provider/claude-session.raw-jsonl").read_text() == message(output=0)
    with path.open("a") as output:
        output.write(message()[20:])
    observer.sync_live(trace)
    observer.sync_live(trace)
    assert (trace / "provider/claude-session.raw-jsonl").read_text() == message(
        output=0
    ) + message()


def test_capture_preserves_line_endings_and_filters_only_estimate_telemetry(tmp_path: Path) -> None:
    observer, path = ledger(tmp_path)
    payload = message().replace("\n", "\r\n").encode()
    noise = b'{"type":"system","subtype":"thinking_tokens"}\r\n'
    path.write_bytes(noise + payload)
    assert observer.capture()[0].payload == payload


def test_reconciliation_gap_does_not_replace_terminal_bill(tmp_path: Path) -> None:
    observer, path = ledger(tmp_path)
    path.write_text(message())
    terminal = TokenUsage(6, 14, 40, 20, 80, "exact")
    events, actual, complete, errors = observe_claude_usage(observer.capture(), (), terminal)
    assert not complete and errors
    assert actual == terminal
    assert events[0].usage is not None and events[0].usage.measurement == "partial"


def test_interrupted_native_usage_stays_partial(tmp_path: Path) -> None:
    observer, path = ledger(tmp_path)
    path.write_text(message() + '{"type":')
    events, actual, complete, errors = observe_claude_usage(
        observer.capture(),
        (),
        TokenUsage.unavailable(),
    )
    assert not complete and errors
    assert actual.total_tokens == 40 and actual.measurement == "partial"
    assert events[0].message_id == "m1"


def test_stream_only_response_not_silently_lost(tmp_path: Path) -> None:
    observer, path = ledger(tmp_path)
    path.write_text(message())
    stream, _ = ClaudeAdapter().normalize_stream(message("missing-native"))
    events, _actual, complete, _errors = observe_claude_usage(
        observer.capture(),
        stream,
        TokenUsage(6, 14, 40, 20, 80, "exact"),
    )
    assert not complete
    assert {event.message_id for event in events[:-1]} == {"m1", "missing-native"}


def test_reject_symlink_and_ambiguous_session(tmp_path: Path) -> None:
    observer, path = ledger(tmp_path)
    other = tmp_path / "external.jsonl"
    other.write_text(message())
    path.symlink_to(other)
    with pytest.raises(ValueError, match="escapes"):
        observer.capture()
    path.unlink()
    path.write_text(message())
    duplicate = tmp_path / "projects/other/session-1.jsonl"
    duplicate.parent.mkdir()
    duplicate.write_text(message())
    with pytest.raises(ValueError, match="ambiguous"):
        observer.capture()


def test_stdout_revisions_and_live_budget_do_not_charge_duplicates() -> None:
    first, last = message(output=0), message(output=10)
    events, total = ClaudeAdapter().normalize_stream(first + last + last)
    assert len(events) == 1 and events[0].message_id == "m1"
    assert total.total_tokens == 43 and total.measurement == "partial"
    budget = TokenBudgetObserver(ClaudeAdapter(), 50)
    assert not budget.on_stdout_line(first)
    assert not budget.on_stdout_line(last)
    assert not budget.on_stdout_line(last)
    assert budget.on_stdout_line(message("m2"))
    growing = TokenBudgetObserver(ClaudeAdapter(), 40)
    assert not growing.on_stdout_line(first)
    assert growing.on_stdout_line(last)


@pytest.mark.parametrize("ending", ["success", "timeout", "exception", "missing"])
def test_runtime_native_capture_including_abnormal_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ending: str,
) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    trace = tmp_path / "trace"
    (trace / "provider").mkdir(parents=True)
    (trace / "conversation.jsonl").write_text("")
    usage = json.loads(message())["message"]["usage"]
    terminal = json.dumps({"type": "result", "usage": usage}) + "\n"

    def runner(
        command: list[str],
        cwd: Path,
        timeout: int | None,
        env: dict[str, str] | None = None,
        observer: ProcessObserver | None = None,
    ) -> ProcessResult:
        assert "--no-session-persistence" not in command
        assert "--resume" not in command
        assert env is not None and observer is not None
        path = Path(env["CLAUDE_CONFIG_DIR"]) / "projects/test/session-1.jsonl"
        if ending != "missing":
            path.parent.mkdir(parents=True)
            path.write_text(message())
        observer.on_stdout_line(message(output=0))
        if ending == "exception":
            # No poll before failure: final cleanup must still capture the file.
            raise RuntimeError("runner failed")
        observer.poll()
        if ending != "missing":
            assert (trace / "provider/claude-session.raw-jsonl").read_text() == message()
        return ProcessResult(
            message(output=0) + (terminal if ending != "timeout" else ""),
            "",
            0 if ending != "timeout" else -15,
            ending == "timeout",
            False,
            (),
        )

    runtime = ClaudeRuntime(process_runner=runner)
    request = AgentRunRequest(tmp_path, "test", 30, session_id="session-1", live_trace_path=trace)
    if ending == "exception":
        with pytest.raises(RuntimeError, match="runner failed"):
            runtime.run(request)
        assert (trace / "provider/claude-session.raw-jsonl").read_text() == message()
        return
    result = runtime.run(request)
    assert result.raw_provider_capture_complete == (ending != "missing")
    assert result.response_usage_complete == (ending == "success")
    if ending == "success":
        assert result.terminal_usage.total_tokens == 40
        assert result.events[0].usage is not None and result.events[0].usage.output_tokens == 7
    elif ending == "timeout":
        assert result.timed_out
        assert result.terminal_usage.measurement == "partial"
        assert result.terminal_usage.total_tokens == 40
    else:
        assert result.terminal_usage.total_tokens == 40
        assert result.observation_errors
