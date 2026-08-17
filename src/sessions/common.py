"""Shared execution and evidence handling for Agent sessions."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Protocol

import backends
from agent_config import AgentConfig


class SessionContext(Protocol):
    """Context fields required by the backend session runner."""

    @property
    def workspace(self) -> Path: ...

    @property
    def token_usage_path(self) -> Path: ...

    @property
    def session_trace_path(self) -> Path | None: ...

    @property
    def token_budget(self) -> int: ...

    @property
    def timeout_seconds(self) -> float: ...

    @property
    def manifest(self) -> Mapping[str, Any]: ...


def _usage_value(value: int | None) -> int:
    return value if value is not None and value >= 0 else 0


def usage_report(
    context: SessionContext,
    result: backends.AgentRunResult | None,
) -> dict[str, Any]:
    usage = result.terminal_usage if result is not None else backends.TokenUsage.unavailable()
    components = (
        usage.input_tokens,
        usage.output_tokens,
        usage.cache_read_tokens,
        usage.cache_write_tokens,
    )
    complete = all(value is not None for value in components) and (
        usage.measurement == "exact" or (result is not None and result.budget_exhausted)
    )
    buckets = {
        "uncached_input_tokens": _usage_value(usage.input_tokens),
        "output_tokens": _usage_value(usage.output_tokens),
        "cache_read_tokens": _usage_value(usage.cache_read_tokens),
        "cache_write_tokens": _usage_value(usage.cache_write_tokens),
    }
    total = sum(buckets.values())
    request_count = (
        sum(1 for event in result.events if event.kind == "usage_delta")
        if result is not None
        else 0
    )
    if result is not None and result.terminal_usage.total_tokens is not None:
        request_count = max(request_count, 1)
    return {
        "schema_version": 1,
        "budget_tokens": context.token_budget,
        "usage": buckets,
        "total_tokens": total,
        "budget_exhausted": (
            total >= context.token_budget or (result is not None and result.budget_exhausted)
        ),
        "session_count": 1 if result is not None else 0,
        "model_request_count": request_count,
        "usage_complete": complete,
    }


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True).encode()
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as output:
            temporary = Path(output.name)
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        temporary.chmod(0o600)
        temporary.replace(path)
        temporary = None
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def write_trace(context: SessionContext, result: backends.AgentRunResult) -> None:
    if context.session_trace_path is None:
        return
    context.session_trace_path.mkdir(parents=True, exist_ok=True, mode=0o700)
    events = context.session_trace_path / "events.jsonl"
    with events.open("w", encoding="utf-8") as output:
        for event in result.events:
            value: dict[str, Any] = {
                "schema_version": 1,
                "sequence": event.sequence,
                "kind": event.kind,
            }
            if event.usage is not None:
                value["usage"] = {
                    "uncached_input_tokens": event.usage.input_tokens,
                    "output_tokens": event.usage.output_tokens,
                    "cache_read_tokens": event.usage.cache_read_tokens,
                    "cache_write_tokens": event.usage.cache_write_tokens,
                    "total_tokens": event.usage.total_tokens,
                    "measurement": event.usage.measurement,
                }
            output.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")
    atomic_json(
        context.session_trace_path / "session.json",
        {
            "schema_version": 1,
            "runtime_id": result.runtime_id,
            "session_id": result.session_id,
            "exit_status": result.exit_status,
            "timed_out": result.timed_out,
            "observation_errors": list(result.observation_errors),
        },
    )


def execute_agent_session(
    context: SessionContext,
    config: AgentConfig,
    prompt: str,
    *,
    on_success: Callable[[], None] | None = None,
) -> int:
    """Run one backend session and always persist its token report."""
    runtime = backends.build_agent_runtime(config.agent_backend)
    result: backends.AgentRunResult | None = None
    try:
        result = runtime.run(
            backends.AgentRunRequest(
                workspace=context.workspace,
                prompt=prompt,
                timeout_s=max(1, int(context.timeout_seconds) - 5),
                reasoning_effort=config.reasoning_effort,
                session_id=str(uuid.uuid4()),
                session_settings=config.session_settings,
                token_budget=context.token_budget,
            )
        )
        write_trace(context, result)
        if result.stdout_tail:
            print(result.stdout_tail, flush=True)
        if result.stderr_tail:
            print(result.stderr_tail, file=sys.stderr, flush=True)
        if result.budget_exhausted:
            return 125
        if result.timed_out:
            return 124
        if result.exit_status == 0 and on_success is not None:
            on_success()
        return result.exit_status
    finally:
        atomic_json(context.token_usage_path, usage_report(context, result))


def guarded_main(run: Callable[[], int]) -> int:
    """Map an entrypoint failure to a stable process exit status."""
    try:
        return run()
    except Exception as error:
        print(f"[atrex-core] {type(error).__name__}: {error}", file=sys.stderr, flush=True)
        return 1
