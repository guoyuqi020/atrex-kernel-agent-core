"""Bounded report completion across fresh invocations of one logical Agent session."""

from __future__ import annotations

import contextlib
import json
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import backends
from agent_config import AgentConfig
from session_transcript import encode_records

from . import common


@dataclass(frozen=True)
class _SegmentContext:
    workspace: Path
    token_usage_path: Path
    session_trace_path: Path | None
    usage_unit: str
    usage_budget: float
    timeout_seconds: float
    manifest: Mapping[str, Any]


@dataclass
class _Segment:
    context: _SegmentContext
    path: str
    session_id: str
    result: backends.AgentRunResult | None = None
    started: bool = False
    sealed: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    conversation: str = ""

    def refresh_trace(self) -> None:
        root = self.context.session_trace_path
        if root is not None:
            self.metadata = json.loads((root / "session.json").read_text(encoding="utf-8"))
            self.conversation = (root / "conversation.jsonl").read_text(encoding="utf-8")


def _aggregate_usage(context: common.SessionContext, segments: list[_Segment]) -> dict[str, Any]:
    results = [segment.result for segment in segments if segment.result is not None]
    unknown = any(segment.started and segment.result is None for segment in segments)
    if not results:
        report = common.usage_report(context, None)
    else:
        usages = [result.terminal_usage for result in results]
        if unknown:
            usages.append(backends.TokenUsage.unavailable())
        total = backends.sum_token_usages(usages)
        if total.credits is None:
            # A partial later result must not erase earlier known component charges.
            # These are lower bounds; usage_complete below remains false for partial usage.
            known_components = {}
            for name in (
                "input_tokens",
                "output_tokens",
                "cache_read_tokens",
                "cache_write_tokens",
            ):
                observed = [
                    getattr(usage, name) for usage in usages if getattr(usage, name) is not None
                ]
                if observed:
                    known_components[name] = sum(observed)
            total = replace(total, **known_components)
        combined = replace(results[-1], terminal_usage=total, budget_exhausted=False)
        report = common.usage_report(context, combined)
        report["model_request_count"] = sum(
            common.usage_report(context, result)["model_request_count"] for result in results
        )
        report["usage_complete"] = not unknown and all(
            common.usage_report(context, result)["usage_complete"] for result in results
        )
    report["session_count"] = sum(segment.started for segment in segments)
    # Runtime validates this equality against the original logical-session budget.
    report["budget_exhausted"] = report["consumed"] >= context.usage_budget
    return report


def _segment_context(context: common.SessionContext, ordinal: int) -> _SegmentContext:
    trace = context.session_trace_path
    if trace is not None and ordinal:
        parent = trace / "continuations"
        if trace.is_symlink() or not trace.is_dir():
            raise ValueError("Session trace root changed before continuation")
        if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
            raise ValueError("Session continuation parent must be a real directory")
        parent.mkdir(mode=0o700, exist_ok=True)
        trace = parent / f"{ordinal:03d}"
    return _SegmentContext(
        workspace=context.workspace,
        token_usage_path=context.token_usage_path,
        session_trace_path=trace,
        usage_unit=context.usage_unit,
        usage_budget=context.usage_budget,
        timeout_seconds=context.timeout_seconds,
        manifest=context.manifest,
    )


def _publish_trace(
    context: common.SessionContext,
    segments: list[_Segment],
    completion: dict[str, Any],
    exit_status: int | None,
) -> None:
    root = context.session_trace_path
    if root is None or not segments or not segments[0].metadata:
        return
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Session trace root changed during report completion")
    records: list[dict[str, Any]] = []
    index: list[dict[str, Any]] = []
    for ordinal, segment in enumerate(segments):
        index.append(
            {
                "index": ordinal,
                "path": segment.path,
                "session_id": segment.metadata.get("session_id", segment.session_id),
                "state": segment.metadata.get("state", "pending"),
                "exit_status": segment.metadata.get("exit_status"),
                "raw_provider_capture_complete": segment.metadata.get(
                    "raw_provider_capture_complete", False
                ),
            }
        )
        if len(segments) > 1:
            records.append(
                {
                    "schema_version": 1,
                    "sequence": len(records),
                    "type": "segment_start",
                    "source": "runtime",
                    "segment_index": ordinal,
                    "segment_path": segment.path,
                    "session_id": index[-1]["session_id"],
                }
            )
            for line in segment.conversation.splitlines():
                record = json.loads(line)
                record["segment_sequence"] = record.get("sequence")
                record["sequence"] = len(records)
                record["segment_index"] = ordinal
                record["segment_path"] = segment.path
                records.append(record)
            records.append(
                {
                    "schema_version": 1,
                    "sequence": len(records),
                    "type": "segment_end",
                    "source": "runtime",
                    "segment_index": ordinal,
                    "state": index[-1]["state"],
                }
            )
    if len(segments) > 1:
        common.atomic_text(root / "conversation.jsonl", encode_records(records))
    finished = completion["state"] in {"complete", "exhausted", "failed", "interrupted"}
    capture_complete = all(
        segment.result is not None and segment.result.raw_provider_capture_complete
        for segment in segments
    )
    results = [segment.result for segment in segments if segment.result is not None]
    response_flags = [result.response_usage_complete for result in results]
    response_complete = (
        False
        if not capture_complete or False in response_flags
        else True
        if response_flags and all(flag is True for flag in response_flags)
        else None
    )
    metadata = {
        **segments[0].metadata,
        "state": "interrupted"
        if completion["state"] == "interrupted"
        else ("finished" if finished else "running"),
        "exit_status": exit_status,
        "timed_out": exit_status == 124 or any(result.timed_out for result in results),
        "raw_provider_capture_complete": capture_complete,
        "response_usage_complete": response_complete,
        "conversation_capture_complete": capture_complete,
        "observation_errors": [error for result in results for error in result.observation_errors],
        "policy_diagnostics": [item for result in results for item in result.policy_diagnostics],
        "segments": index,
        "report_completion": dict(completion),
        "normalizations": {
            "conversation": "fresh segments concatenated; segment_sequence retains local order",
            "provider_paths": "relative to each record's segment_path",
            "events": "each segment retains its own events.jsonl; no terminal/delta double count",
            "usage": "sum of fresh invocation terminal usage; no native history replay",
        },
    }
    common.atomic_json(root / "session.json", metadata)
    marker = root / common._LIVE_TRACE_MARKER
    if finished and completion["state"] != "interrupted":
        marker.unlink(missing_ok=True)
    else:
        common.atomic_text(marker, "unsealed\n")


def execute_report_completion(
    context: common.SessionContext,
    config: AgentConfig,
    prompt: str,
    *,
    system_prompt: str,
    on_success: Callable[[], None] | None,
    completion_check: Callable[[float], str | None],
) -> int:
    """Preserve all completed evidence while spending only the remaining session allowance."""
    runtime = backends.build_agent_runtime(config.agent_backend)
    deadline = time.monotonic() + max(1, int(context.timeout_seconds) - 5)
    segments: list[_Segment] = []
    completion: dict[str, Any] = {
        "state": "checking",
        "retries_used": 0,
        "max_retries": config.report_completion_retries,
    }
    exit_status: int | None = None
    current: _Segment | None = None
    checking_completion = False
    try:
        for ordinal in range(config.report_completion_retries + 1):
            checking_completion = False
            usage = _aggregate_usage(context, segments)
            remaining_budget = context.usage_budget - usage["consumed"]
            remaining_timeout = int(deadline - time.monotonic())
            if remaining_budget <= 0 or remaining_timeout < 1:
                exit_status = 125 if remaining_budget <= 0 else 124
                completion["state"] = "failed"
                return exit_status
            current = _Segment(
                _segment_context(context, ordinal),
                "." if ordinal == 0 else f"continuations/{ordinal:03d}",
                str(uuid.uuid4()),
            )
            live = common.start_live_trace(
                current.context,
                prompt,
                runtime_id=runtime.id,
                session_id=current.session_id,
                config=config,
                system_prompt=system_prompt,
            )
            current.refresh_trace()
            segments.append(current)
            completion.update(state="continuing" if ordinal else "checking", retries_used=ordinal)
            _publish_trace(context, segments, completion, None)
            current.started = True
            common.atomic_json(context.token_usage_path, _aggregate_usage(context, segments))
            current.result = runtime.run(
                backends.AgentRunRequest(
                    workspace=context.workspace,
                    prompt=prompt,
                    timeout_s=remaining_timeout,
                    reasoning_effort=config.reasoning_effort,
                    session_id=current.session_id,
                    session_settings=config.session_settings,
                    model=config.model,
                    usage_budget=remaining_budget,
                    live_trace_path=current.context.session_trace_path if live else None,
                    system_prompt=system_prompt,
                )
            )
            result = current.result
            common.write_trace(
                current.context,
                result,
                prompt,
                config,
                replace_live=live,
                keep_live=ordinal == 0,
                system_prompt=system_prompt,
            )
            current.refresh_trace()
            current.sealed = True
            usage = _aggregate_usage(context, segments)
            common.atomic_json(context.token_usage_path, usage)
            if result.budget_exhausted or usage["budget_exhausted"]:
                exit_status = 125
            elif result.timed_out:
                exit_status = 124
            elif result.exit_status != 0:
                exit_status = result.exit_status
            elif (
                not result.raw_provider_capture_complete
                or not usage["usage_complete"]
                or result.response_usage_complete is False
                or result.policy_diagnostics
            ):
                exit_status = 126
            if exit_status is not None:
                completion["state"] = "failed"
                return exit_status
            completion["state"] = "checking"
            _publish_trace(context, segments, completion, None)
            checking_completion = True
            remaining_check = deadline - time.monotonic()
            if remaining_check <= 0:
                completion["state"] = "failed"
                exit_status = 124
                return 124
            try:
                repair_prompt = completion_check(remaining_check)
            except TimeoutError:
                completion["state"] = "failed"
                exit_status = 124
                return 124
            if time.monotonic() >= deadline:
                completion["state"] = "failed"
                exit_status = 124
                return 124
            if repair_prompt is None:
                if on_success is not None:
                    on_success()
                completion["state"] = "complete"
                exit_status = 0
                return 0
            if not isinstance(repair_prompt, str) or not repair_prompt.strip():
                raise ValueError("Report completion check must return a non-empty prompt or None")
            if ordinal == config.report_completion_retries:
                completion["state"] = "exhausted"
                exit_status = 127
                return 127
            prompt = repair_prompt
        raise AssertionError("Report completion loop ended without a terminal result")
    except BaseException as error:
        if checking_completion and current is not None and current.sealed:
            # Report inspection failure does not invalidate completed model capture or charges.
            completion["state"] = "failed"
            exit_status = 1
        else:
            completion["state"] = "interrupted"
        if current is not None and not current.sealed:
            common.mark_live_trace_interrupted(current.context, error)
            trace = current.context.session_trace_path
            if trace is not None and (trace / common._LIVE_TRACE_MARKER).is_file():
                with contextlib.suppress(OSError, ValueError):
                    current.refresh_trace()
        raise
    finally:
        # A later invocation without a result must not erase earlier charges or claim completeness.
        common.atomic_json(context.token_usage_path, _aggregate_usage(context, segments))
        _publish_trace(context, segments, completion, exit_status)
