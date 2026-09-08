from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import pytest

import backends
from agent_config import AgentConfig
from backends.model import (
    AgentRunRequest,
    AgentRunResult,
    AgentRuntimeCapabilities,
    NormalizedAgentEvent,
    RawSessionFile,
    TokenUsage,
)
from sessions.common import execute_agent_session


@dataclass(frozen=True)
class _Context:
    workspace: Path
    token_usage_path: Path
    session_trace_path: Path | None
    manifest: dict[str, Any] = field(default_factory=dict)
    usage_unit: str = "provider_tokens"
    usage_budget: float = 1_000
    timeout_seconds: float = 60


@dataclass
class _Clock:
    now: float = 100.0

    def monotonic(self) -> float:
        return self.now


@dataclass(frozen=True)
class _Outcome:
    usage: TokenUsage = field(default_factory=lambda: TokenUsage(10, 3, 2, 1, 16, "exact"))
    exit_status: int = 0
    timed_out: bool = False
    budget_exhausted: bool = False
    raw_provider_capture_complete: bool = True
    response_usage_complete: bool | None = True
    request_count: int = 1
    elapsed: float = 0
    error: Exception | None = None


class _Runtime:
    id = "codex"

    def __init__(self, context: _Context, clock: _Clock, outcomes: list[_Outcome]) -> None:
        self.context = context
        self.clock = clock
        self.outcomes = outcomes
        self.requests: list[AgentRunRequest] = []
        self.results: list[AgentRunResult] = []
        self.usage_before_launch: list[dict[str, Any] | None] = []

    def run(self, request: AgentRunRequest) -> AgentRunResult:
        index = len(self.requests)
        self.requests.append(request)
        self.usage_before_launch.append(
            _read_json(self.context.token_usage_path)
            if self.context.token_usage_path.exists()
            else None
        )
        assert index < len(self.outcomes), "unexpected extra backend invocation"
        outcome = self.outcomes[index]
        self.clock.now += outcome.elapsed
        if outcome.error is not None:
            raise outcome.error
        stdout = (
            json.dumps(
                {
                    "type": "response_item",
                    "payload": {"type": "message", "role": "assistant", "text": f"segment {index}"},
                }
            )
            + "\n"
        )
        result = AgentRunResult(
            runtime_id=self.id,
            exit_status=outcome.exit_status,
            timed_out=outcome.timed_out,
            terminal_usage=outcome.usage,
            events=tuple(
                NormalizedAgentEvent(
                    sequence=sequence,
                    kind="usage_delta",
                    usage=outcome.usage,
                    message_id=f"segment-{index}-request-{sequence}",
                )
                for sequence in range(outcome.request_count)
            ),
            capabilities=AgentRuntimeCapabilities(terminal_usage=True, usage_delta=True),
            observation_errors=(),
            stdout=stdout,
            stderr=f"diagnostic for segment {index}\n",
            raw_session_files=(
                RawSessionFile("provider/codex-rollout.raw-jsonl", stdout.encode()),
            ),
            raw_provider_capture_complete=outcome.raw_provider_capture_complete,
            policy_diagnostics=(),
            session_id=request.session_id or "",
            budget_exhausted=outcome.budget_exhausted,
            response_usage_complete=outcome.response_usage_complete,
        )
        self.results.append(result)
        return result


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _setup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    outcomes: list[_Outcome],
    *,
    usage_unit: str = "provider_tokens",
    usage_budget: float = 1_000,
    timeout_seconds: float = 60,
    trace: bool = True,
) -> tuple[_Context, _Runtime, _Clock]:
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    context = _Context(
        workspace=tmp_path,
        token_usage_path=tmp_path / "scratch/token-usage.json",
        session_trace_path=sessions / "core" if trace else None,
        usage_unit=usage_unit,
        usage_budget=usage_budget,
        timeout_seconds=timeout_seconds,
    )
    clock = _Clock()
    runtime = _Runtime(context, clock, outcomes)
    monkeypatch.setattr("sessions.session_segments.time.monotonic", clock.monotonic)
    monkeypatch.setattr(backends, "build_agent_runtime", lambda _backend: runtime)
    return context, runtime, clock


def _config(retries: int = 2) -> AgentConfig:
    return AgentConfig(
        "codex",
        "max",
        '{"permissions":"fixed"}',
        {},
        model="test-model",
        report_completion_retries=retries,
    )


def test_missing_report_gets_fresh_report_only_session_in_same_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, runtime, _clock = _setup(tmp_path, monkeypatch, [_Outcome(), _Outcome()])
    prompts = ["original phase prompt", "Full phase context. Submit only the missing report."]
    check_count = 0
    success_count = 0

    def completion_check(_remaining_timeout_s: float) -> str | None:
        nonlocal check_count
        check_count += 1
        assert _read_json(context.token_usage_path)["session_count"] == check_count
        return prompts[1] if check_count == 1 else None

    def on_success() -> None:
        nonlocal success_count
        success_count += 1

    assert (
        execute_agent_session(
            context,
            _config(),
            prompts[0],
            system_prompt="fixed system instructions",
            completion_check=completion_check,
            on_success=on_success,
        )
        == 0
    )
    assert check_count == 2
    assert success_count == 1
    assert [request.prompt for request in runtime.requests] == prompts
    assert all(request.workspace == context.workspace for request in runtime.requests)
    assert all(request.system_prompt == "fixed system instructions" for request in runtime.requests)
    assert all(request.model == "test-model" for request in runtime.requests)
    assert all(
        request.session_settings == _config().session_settings for request in runtime.requests
    )
    assert len({request.session_id for request in runtime.requests}) == 2
    assert all(request.session_id for request in runtime.requests)
    assert context.session_trace_path is not None
    assert runtime.requests[0].live_trace_path == context.session_trace_path
    assert runtime.requests[1].live_trace_path == context.session_trace_path / "continuations/001"


def test_already_present_report_does_not_start_another_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, runtime, _clock = _setup(tmp_path, monkeypatch, [_Outcome()])
    checks: list[bool] = []

    def completion_check(_remaining_timeout_s: float) -> None:
        checks.append(True)

    assert (
        execute_agent_session(context, _config(), "prompt", completion_check=completion_check) == 0
    )
    assert len(runtime.requests) == 1
    assert checks == [True]
    assert context.session_trace_path is not None
    assert not (context.session_trace_path / "continuations").exists()
    metadata = _read_json(context.session_trace_path / "session.json")
    assert isinstance(metadata["report_completion"], dict)
    assert len(metadata["segments"]) == 1


@pytest.mark.parametrize("retries", [0, 1, 2])
def test_missing_report_exhausts_only_configured_completion_retries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    retries: int,
) -> None:
    context, runtime, _clock = _setup(
        tmp_path, monkeypatch, [_Outcome() for _ in range(retries + 1)]
    )
    checks: list[bool] = []
    successes: list[bool] = []

    def completion_check(_remaining_timeout_s: float) -> str:
        checks.append(True)
        return f"Full phase context. Report-only completion attempt {len(checks)}."

    assert (
        execute_agent_session(
            context,
            _config(retries),
            "prompt",
            completion_check=completion_check,
            on_success=lambda: successes.append(True),
        )
        == 127
    )
    assert len(runtime.requests) == retries + 1
    assert len(checks) == retries + 1
    assert not successes
    assert len({request.session_id for request in runtime.requests}) == retries + 1
    assert _read_json(context.token_usage_path)["session_count"] == retries + 1


@pytest.mark.parametrize(
    ("outcome", "expected_status"),
    [
        pytest.param(_Outcome(exit_status=7), 7, id="nonzero-exit"),
        pytest.param(_Outcome(timed_out=True), 124, id="timeout"),
        pytest.param(_Outcome(budget_exhausted=True), 125, id="backend-budget-exhausted"),
        pytest.param(
            _Outcome(usage=TokenUsage(1_000, 0, 0, 0, 1_000, "exact")),
            125,
            id="measured-budget-exhausted",
        ),
        pytest.param(
            _Outcome(raw_provider_capture_complete=False), 126, id="incomplete-raw-capture"
        ),
        pytest.param(_Outcome(response_usage_complete=False), 126, id="incomplete-response-usage"),
        pytest.param(_Outcome(usage=TokenUsage.unavailable()), 126, id="unavailable-usage"),
        pytest.param(
            _Outcome(usage=TokenUsage(10, 3, 2, 1, 16, "partial")),
            126,
            id="partial-usage",
        ),
    ],
)
def test_failed_or_incomplete_sessions_do_not_check_or_retry_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    outcome: _Outcome,
    expected_status: int,
) -> None:
    context, runtime, _clock = _setup(tmp_path, monkeypatch, [outcome])

    def unexpected_callback(_remaining_timeout_s: float) -> str:
        pytest.fail("completion callback must not run after a failed or incomplete session")

    assert (
        execute_agent_session(context, _config(), "prompt", completion_check=unexpected_callback)
        == expected_status
    )
    assert len(runtime.requests) == 1
    assert context.token_usage_path.is_file()


@pytest.mark.parametrize("usage_unit", ["provider_tokens", "credits"])
def test_completion_sessions_incrementally_accumulate_usage_and_request_counts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    usage_unit: str,
) -> None:
    usages = (
        [TokenUsage.credit(1.25), TokenUsage.credit(2.5)]
        if usage_unit == "credits"
        else [TokenUsage(10, 3, 2, 1, 16, "exact"), TokenUsage(20, 7, 5, 2, 34, "exact")]
    )
    context, runtime, _clock = _setup(
        tmp_path,
        monkeypatch,
        [_Outcome(usage=usages[0], request_count=2), _Outcome(usage=usages[1], request_count=3)],
        usage_unit=usage_unit,
        usage_budget=100,
    )

    def completion_check(_remaining_timeout_s: float) -> str | None:
        return "Full context. Submit the report." if len(runtime.requests) == 1 else None

    assert (
        execute_agent_session(context, _config(), "prompt", completion_check=completion_check) == 0
    )
    initial_checkpoint = runtime.usage_before_launch[0]
    assert initial_checkpoint is not None
    assert initial_checkpoint["session_count"] == 1
    assert initial_checkpoint["usage_complete"] is False
    assert initial_checkpoint["consumed"] == 0
    previous = runtime.usage_before_launch[1]
    assert previous is not None
    assert previous["session_count"] == 2
    assert previous["usage_complete"] is False
    assert previous["model_request_count"] == 2
    report = _read_json(context.token_usage_path)
    assert report["session_count"] == 2
    assert report["model_request_count"] == 5
    assert report["usage_complete"] is True
    assert report["budget"] == 100
    assert report["budget_exhausted"] is False
    assert runtime.requests[0].usage_budget == 100
    if usage_unit == "credits":
        assert previous["credits"] == 1.25
        assert report["credits"] == 3.75
        assert report["consumed"] == 3.75
        assert runtime.requests[1].usage_budget == 98.75
    else:
        assert previous["consumed"] == 16
        assert report["credits"] is None
        assert report["consumed"] == 50
        assert report["token_usage"] == {
            "uncached_input_tokens": 30,
            "output_tokens": 10,
            "cache_read_tokens": 7,
            "cache_write_tokens": 3,
        }
        assert runtime.requests[1].usage_budget == 84


def test_cumulative_budget_exhaustion_stops_before_another_completion_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, runtime, _clock = _setup(
        tmp_path,
        monkeypatch,
        [_Outcome(), _Outcome(usage=TokenUsage(84, 0, 0, 0, 84, "exact"))],
        usage_budget=100,
    )
    checks: list[bool] = []

    def completion_check(_remaining_timeout_s: float) -> str:
        checks.append(True)
        return "Full context. Submit the report."

    assert (
        execute_agent_session(context, _config(), "prompt", completion_check=completion_check)
        == 125
    )
    assert len(runtime.requests) == 2
    assert runtime.requests[1].usage_budget == 84
    assert checks == [True]
    report = _read_json(context.token_usage_path)
    assert report["consumed"] == 100
    assert report["budget_exhausted"] is True
    assert report["session_count"] == 2


def test_completion_trace_keeps_initial_provider_files_and_combines_segment_boundaries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, runtime, _clock = _setup(tmp_path, monkeypatch, [_Outcome(), _Outcome()])
    prompts = ["original prompt", "Full phase context and report-only instructions."]

    def completion_check(_remaining_timeout_s: float) -> str | None:
        return prompts[1] if len(runtime.requests) == 1 else None

    assert (
        execute_agent_session(context, _config(), prompts[0], completion_check=completion_check)
        == 0
    )
    assert context.session_trace_path is not None
    root = context.session_trace_path
    continuation = root / "continuations/001"
    for index, trace in enumerate((root, continuation)):
        assert (trace / "input/prompt.md").read_text() == prompts[index]
        assert (trace / "provider/stdout.stream-json").read_text() == runtime.results[index].stdout
        assert (trace / "provider/stderr.log").read_text() == runtime.results[index].stderr
        assert (trace / "provider/codex-rollout.raw-jsonl").read_bytes() == (
            runtime.results[index].raw_session_files[0].payload
        )
        assert (trace / "events.jsonl").is_file()
        assert not (trace / ".runtime-live-session").exists()
    metadata = _read_json(root / "session.json")
    assert metadata["session_id"] == runtime.requests[0].session_id
    assert isinstance(metadata["report_completion"], dict)
    assert len(metadata["segments"]) == 2
    assert {segment["session_id"] for segment in metadata["segments"]} == {
        request.session_id for request in runtime.requests
    }
    conversation = [
        json.loads(line) for line in (root / "conversation.jsonl").read_text().splitlines()
    ]
    assert [row["content"][0]["text"] for row in conversation if row.get("role") == "user"] == (
        prompts
    )
    assert sum(row["type"] == "segment_start" for row in conversation) == 2
    assert sum(row["type"] == "segment_end" for row in conversation) == 2
    assert "segment 0" in (root / "conversation.jsonl").read_text()
    assert "segment 1" in (root / "conversation.jsonl").read_text()


def test_completion_sessions_share_deadline_including_completion_check_time(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, runtime, clock = _setup(
        tmp_path,
        monkeypatch,
        [_Outcome(elapsed=12), _Outcome()],
        timeout_seconds=60,
    )

    def completion_check(_remaining_timeout_s: float) -> str | None:
        clock.now += 7
        return "Full context. Submit the report." if len(runtime.requests) == 1 else None

    assert (
        execute_agent_session(context, _config(), "prompt", completion_check=completion_check) == 0
    )
    assert len(runtime.requests) == 2
    assert 0 < runtime.requests[1].timeout_s <= runtime.requests[0].timeout_s - 19
    assert runtime.requests[0].timeout_s <= 60


def test_expired_shared_deadline_does_not_launch_another_backend(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, runtime, clock = _setup(tmp_path, monkeypatch, [_Outcome()])

    def completion_check(_remaining_timeout_s: float) -> str:
        clock.now += context.timeout_seconds
        return "Full context. Submit the report."

    assert (
        execute_agent_session(context, _config(), "prompt", completion_check=completion_check)
        == 124
    )
    assert len(runtime.requests) == 1
    assert _read_json(context.token_usage_path)["consumed"] == 16
    assert context.session_trace_path is not None
    assert _read_json(context.session_trace_path / "session.json")["timed_out"] is True


@pytest.mark.parametrize("raises_timeout", [False, True])
def test_completion_check_cannot_succeed_after_its_remaining_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    raises_timeout: bool,
) -> None:
    context, runtime, clock = _setup(tmp_path, monkeypatch, [_Outcome(elapsed=10)])

    def completion_check(remaining_timeout_s: float) -> None:
        assert 0 < remaining_timeout_s < context.timeout_seconds
        clock.now += remaining_timeout_s + 1
        if raises_timeout:
            raise TimeoutError("report query exceeded the deadline")

    assert (
        execute_agent_session(context, _config(), "prompt", completion_check=completion_check)
        == 124
    )
    assert len(runtime.requests) == 1
    assert _read_json(context.token_usage_path)["consumed"] == 16


def test_continuation_launch_checkpoints_unknown_usage_and_unsealed_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, runtime, _clock = _setup(tmp_path, monkeypatch, [_Outcome(), _Outcome()])
    original_run = runtime.run

    def inspected_run(request: AgentRunRequest) -> AgentRunResult:
        assert context.session_trace_path is not None
        root = context.session_trace_path
        assert (root / ".runtime-live-session").is_file()
        checkpoint = _read_json(context.token_usage_path)
        assert checkpoint["usage_complete"] is False
        assert checkpoint["session_count"] == len(runtime.requests) + 1
        if runtime.requests:
            assert checkpoint["consumed"] == 16
            assert checkpoint["model_request_count"] == 1
            assert (root / "provider/stdout.stream-json").read_text() == runtime.results[0].stdout
        return original_run(request)

    monkeypatch.setattr(runtime, "run", inspected_run)
    assert (
        execute_agent_session(
            context,
            _config(),
            "prompt",
            completion_check=lambda _remaining_timeout_s: (
                "Submit the report." if len(runtime.requests) == 1 else None
            ),
        )
        == 0
    )
    assert context.session_trace_path is not None
    assert not (context.session_trace_path / ".runtime-live-session").exists()


def test_root_trace_reflects_later_incomplete_response_usage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, runtime, _clock = _setup(
        tmp_path, monkeypatch, [_Outcome(), _Outcome(response_usage_complete=False)]
    )
    assert (
        execute_agent_session(
            context,
            _config(),
            "prompt",
            completion_check=lambda _remaining_timeout_s: "Submit the report.",
        )
        == 126
    )
    assert len(runtime.requests) == 2
    assert context.session_trace_path is not None
    metadata = _read_json(context.session_trace_path / "session.json")
    assert metadata["response_usage_complete"] is False
    assert metadata["exit_status"] == 126
    assert metadata["report_completion"]["state"] == "failed"


@pytest.mark.parametrize("usage_unit", ["provider_tokens", "credits"])
def test_later_backend_exception_preserves_previously_observed_usage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    usage_unit: str,
) -> None:
    usage = TokenUsage.credit(1.25) if usage_unit == "credits" else _Outcome().usage
    context, runtime, _clock = _setup(
        tmp_path,
        monkeypatch,
        [
            _Outcome(usage=usage, request_count=2),
            _Outcome(error=RuntimeError("later launch failed")),
        ],
        usage_unit=usage_unit,
    )

    with pytest.raises(RuntimeError, match="later launch failed"):
        execute_agent_session(
            context,
            _config(),
            "prompt",
            completion_check=lambda _remaining_timeout_s: "Full context. Submit the report.",
        )
    assert len(runtime.requests) == 2
    report = _read_json(context.token_usage_path)
    assert report["consumed"] == (1.25 if usage_unit == "credits" else 16)
    assert report["model_request_count"] == 2
    assert report["usage_complete"] is False
    if usage_unit == "credits":
        assert report["credits"] == 1.25
    else:
        assert report["token_usage"]["uncached_input_tokens"] == 10
    assert context.session_trace_path is not None
    root = context.session_trace_path
    assert (root / "provider/stdout.stream-json").read_text() == runtime.results[0].stdout
    assert "later launch" not in (root / "provider/stdout.stream-json").read_text()
    interrupted = _read_json(root / "continuations/001/session.json")
    assert interrupted["state"] == "interrupted"
    assert (root / ".runtime-live-session").is_file()
    assert _read_json(root / "session.json")["state"] == "interrupted"


@pytest.mark.parametrize("completed_segments", [1, 2])
@pytest.mark.parametrize("usage_unit", ["provider_tokens", "credits"])
def test_completion_check_exception_keeps_finished_backend_usage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    completed_segments: int,
    usage_unit: str,
) -> None:
    usage = TokenUsage.credit(1.25) if usage_unit == "credits" else _Outcome().usage
    context, runtime, _clock = _setup(
        tmp_path,
        monkeypatch,
        [_Outcome(usage=usage)] * completed_segments,
        usage_unit=usage_unit,
    )

    def completion_check(_remaining_timeout_s: float) -> str:
        if len(runtime.requests) < completed_segments:
            return "Full context. Submit the report."
        raise ValueError("report inspection failed")

    with pytest.raises(ValueError, match="report inspection failed"):
        execute_agent_session(context, _config(), "prompt", completion_check=completion_check)
    assert len(runtime.requests) == completed_segments
    report = _read_json(context.token_usage_path)
    assert report["consumed"] == completed_segments * (1.25 if usage_unit == "credits" else 16)
    assert report["session_count"] == completed_segments
    assert report["model_request_count"] == completed_segments
    assert report["usage_complete"] is True
    assert context.session_trace_path is not None
    root = context.session_trace_path
    # Runtime can seal this fully captured trace; report inspection did not interrupt a model.
    assert not (root / ".runtime-live-session").exists()
    metadata = _read_json(root / "session.json")
    assert metadata["state"] == "finished"
    assert metadata["exit_status"] == 1
    assert metadata["report_completion"]["state"] == "failed"
    assert metadata["raw_provider_capture_complete"] is True
    assert metadata["response_usage_complete"] is True
    assert len(metadata["segments"]) == completed_segments
    assert all(segment["state"] == "finished" for segment in metadata["segments"])


@pytest.mark.parametrize(
    "outcome",
    [_Outcome(), _Outcome(usage=TokenUsage.unavailable(), response_usage_complete=False)],
    ids=["complete-usage", "legacy-incomplete-usage"],
)
def test_no_completion_callback_preserves_single_session_behavior(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    outcome: _Outcome,
) -> None:
    context, runtime, _clock = _setup(tmp_path, monkeypatch, [outcome])
    successes: list[bool] = []

    assert (
        execute_agent_session(
            context, _config(), "prompt", on_success=lambda: successes.append(True)
        )
        == 0
    )
    assert successes == [True]
    assert len(runtime.requests) == 1
    assert context.session_trace_path is not None
    assert not (context.session_trace_path / "continuations").exists()
    metadata = _read_json(context.session_trace_path / "session.json")
    assert "report_completion" not in metadata
    assert "segments" not in metadata


def test_completion_loop_works_without_a_trace_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, runtime, _clock = _setup(tmp_path, monkeypatch, [_Outcome(), _Outcome()], trace=False)

    def completion_check(_remaining_timeout_s: float) -> str | None:
        return "Full context. Submit the report." if len(runtime.requests) == 1 else None

    assert (
        execute_agent_session(context, _config(), "prompt", completion_check=completion_check) == 0
    )
    assert len(runtime.requests) == 2
    assert all(request.live_trace_path is None for request in runtime.requests)
    assert _read_json(context.token_usage_path)["session_count"] == 2


@pytest.mark.parametrize("total_tokens", [3, None])
def test_later_partial_token_buckets_preserve_known_charges(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    total_tokens: int | None,
) -> None:
    context, runtime, _clock = _setup(
        tmp_path,
        monkeypatch,
        [_Outcome(), _Outcome(usage=TokenUsage(None, 3, None, None, total_tokens, "partial"))],
    )
    assert (
        execute_agent_session(
            context,
            _config(),
            "prompt",
            completion_check=lambda _remaining_timeout_s: "Submit the report.",
        )
        == 126
    )
    assert len(runtime.requests) == 2
    report = _read_json(context.token_usage_path)
    assert report["consumed"] == 19
    assert report["token_usage"] == {
        "uncached_input_tokens": 10,
        "output_tokens": 6,
        "cache_read_tokens": 2,
        "cache_write_tokens": 1,
    }
    assert report["usage_complete"] is False


def test_later_incomplete_usage_does_not_erase_prior_token_components(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, runtime, _clock = _setup(
        tmp_path,
        monkeypatch,
        [_Outcome(), replace(_Outcome(), usage=TokenUsage.unavailable())],
    )
    checks: list[bool] = []

    def completion_check(_remaining_timeout_s: float) -> str:
        checks.append(True)
        return "Full context. Submit the report."

    assert (
        execute_agent_session(context, _config(), "prompt", completion_check=completion_check)
        == 126
    )
    assert len(runtime.requests) == 2
    assert checks == [True]
    report = _read_json(context.token_usage_path)
    assert report["consumed"] == 16
    assert report["usage_complete"] is False
    assert report["token_usage"]["uncached_input_tokens"] == 10
