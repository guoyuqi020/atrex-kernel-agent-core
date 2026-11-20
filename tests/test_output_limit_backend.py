from __future__ import annotations

import json
from pathlib import Path

import pytest

from backends.adapter import ClaudeAdapter, CodexAdapter, PiAdapter, QoderAdapter
from backends.model import AgentRunRequest
from backends.process import ProcessObserver, ProcessResult
from backends.runtime import ClaudeRuntime


def _error(**overrides: object) -> dict[str, object]:
    return {
        "type": "assistant",
        "session_id": "root-session",
        "parent_tool_use_id": None,
        "message": {
            "id": "synthetic-error",
            "content": [{"type": "text", "text": "API Error: output token maximum"}],
        },
        "error": "max_output_tokens",
        "api_error": "max_output_tokens",
        "is_api_error_message": True,
        **overrides,
    }


def _terminal(**overrides: object) -> dict[str, object]:
    return {
        "type": "result",
        "session_id": "root-session",
        "is_error": True,
        "terminal_reason": "api_error",
        "subtype": "success",
        **overrides,
    }


def _stream(*events: dict[str, object]) -> str:
    return "\n".join(json.dumps(event) for event in events)


@pytest.mark.parametrize("code_field", ["error", "api_error", "both"])
def test_claude_terminal_output_limit_uses_error_fields_not_subtype(code_field: str) -> None:
    error = _error()
    if code_field != "both":
        error.pop("api_error" if code_field == "error" else "error")
    assert ClaudeAdapter().classify_terminal_failure(
        _stream(error, _terminal()), "root-session"
    ) == ("output_limit", "max_output_tokens")


@pytest.mark.parametrize(
    "events",
    [
        # A truncated response can be followed by successful tool use and completion.
        (
            {
                "type": "assistant",
                "session_id": "root-session",
                "message": {"stop_reason": "max_tokens", "usage": {"output_tokens": 32000}},
            },
            _terminal(is_error=False, terminal_reason="completed"),
        ),
        # Text quotations are not structured provider errors, even on a failing run.
        (
            {
                "type": "assistant",
                "session_id": "root-session",
                "message": {"content": [{"type": "text", "text": json.dumps(_error())}]},
            },
            _terminal(),
        ),
        (_error(parent_tool_use_id="subagent-tool"), _terminal()),
        (_error(session_id="child-session"), _terminal()),
        (_error(), _terminal(session_id="different-session")),
        (_error(), _terminal(parent_tool_use_id="subagent-tool")),
        (_error(),),  # An incomplete stream alone cannot establish a terminal failure.
        (_terminal(),),
        (_error(is_api_error_message=False), _terminal()),
        (_error(), _terminal(is_error=False)),
        (_error(), _terminal(terminal_reason="max_turns")),
        (_error(api_error="rate_limit"), _terminal()),
        (_error(), _terminal(), _terminal(is_error=False, terminal_reason="completed")),
        (
            _error(),
            _terminal(is_error=False, terminal_reason="completed"),
            _terminal(),
        ),
        (
            _error(),
            {"type": "assistant", "session_id": "root-session", "message": {}},
            _terminal(),
        ),
    ],
)
def test_claude_output_limit_ignores_nonterminal_or_unrelated_errors(
    events: tuple[dict[str, object], ...],
) -> None:
    assert ClaudeAdapter().classify_terminal_failure(
        _stream(*events), "root-session"
    ) == (None, None)


def test_other_backends_do_not_apply_claude_error_contract() -> None:
    for adapter in (CodexAdapter(), PiAdapter(), QoderAdapter()):
        assert adapter.classify_terminal_failure(
            _stream(_error(), _terminal()), "root-session"
        ) == (None, None)


@pytest.mark.parametrize("process_exit", [0, 1, 7])
@pytest.mark.parametrize("scope_complete", [True, False])
def test_claude_runtime_normalizes_terminal_error_even_with_zero_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, process_exit: int, scope_complete: bool
) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))

    def runner(
        command: list[str],
        cwd: Path,
        timeout: int | None,
        env: dict[str, str] | None = None,
        observer: ProcessObserver | None = None,
    ) -> ProcessResult:
        return ProcessResult(
            _stream(_error(), _terminal()), "", process_exit, False, False, (), scope_complete
        )

    result = ClaudeRuntime(process_runner=runner).run(
        AgentRunRequest(tmp_path, "test", 30, session_id="root-session")
    )
    assert result.failure_kind == "output_limit"
    assert result.provider_error_code == "max_output_tokens"
    assert result.exit_status == (process_exit or 1)
    assert not result.timed_out
    assert not result.budget_exhausted
    assert bool(result.policy_diagnostics) is (not scope_complete)
