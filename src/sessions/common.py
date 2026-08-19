"""Shared execution and evidence handling for Agent sessions."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

import backends
from agent_config import AgentConfig

_LIVE_TRACE_MARKER = ".runtime-live-session"


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


def atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
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


def atomic_json(path: Path, value: object) -> None:
    atomic_bytes(
        path,
        json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True).encode(),
    )


def atomic_text(path: Path, value: str) -> None:
    atomic_bytes(path, value.encode("utf-8"))


def start_live_trace(
    context: SessionContext,
    prompt: str,
    *,
    runtime_id: str,
    session_id: str,
    config: AgentConfig,
) -> bool:
    """Create a stable, explicitly non-authoritative trace projection before launch."""
    if context.session_trace_path is None:
        return False
    trace_root = context.session_trace_path
    parent = trace_root.parent
    workspace = context.workspace.resolve()
    if parent.is_symlink() or not parent.is_dir() or not parent.resolve().is_relative_to(workspace):
        raise ValueError("Session trace parent changed before launch")
    if trace_root.exists() or trace_root.is_symlink():
        raise ValueError("Session trace path must not exist before launch")
    trace_root.mkdir(mode=0o700)
    atomic_text(trace_root / _LIVE_TRACE_MARKER, "unsealed\n")
    atomic_text(trace_root / "input/prompt.md", prompt)
    atomic_text(trace_root / "provider/stdout.stream-json", "")
    atomic_text(trace_root / "provider/stderr.log", "")
    if runtime_id == "codex":
        atomic_bytes(trace_root / "provider/codex-rollout.raw-jsonl", b"")
    atomic_json(
        trace_root / "session.json",
        {
            "schema_version": 1,
            "runtime_id": runtime_id,
            "reasoning_effort": config.reasoning_effort,
            "runtime_bound": config.runtime_bound,
            "session_settings_sha256": hashlib.sha256(
                config.session_settings.encode("utf-8")
            ).hexdigest(),
            "session_id": session_id,
            "state": "running",
        },
    )
    return True


def mark_live_trace_interrupted(context: SessionContext, error: BaseException) -> None:
    """Leave a useful Workspace-only terminal marker when no final result exists."""
    trace_root = context.session_trace_path
    if trace_root is None or trace_root.is_symlink() or not trace_root.is_dir():
        return
    marker = trace_root / _LIVE_TRACE_MARKER
    if marker.is_symlink() or not marker.is_file():
        return
    try:
        session_path = trace_root / "session.json"
        if session_path.is_symlink() or not session_path.is_file():
            return
        session = json.loads(session_path.read_text(encoding="utf-8"))
        if not isinstance(session, dict):
            session = {}
        session["state"] = "interrupted"
        session["error_type"] = type(error).__name__
        atomic_json(trace_root / "session.json", session)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return


def write_trace(
    context: SessionContext,
    result: backends.AgentRunResult,
    prompt: str,
    config: AgentConfig,
    *,
    replace_live: bool = False,
) -> None:
    """Persist unredacted Session input and Provider files plus a normalized usage index."""
    if context.session_trace_path is None:
        return
    trace_root = context.session_trace_path
    parent = trace_root.parent
    workspace = context.workspace.resolve()
    if parent.is_symlink() or not parent.is_dir() or not parent.resolve().is_relative_to(workspace):
        raise ValueError("Session trace parent changed after launch validation")
    if trace_root.exists() or trace_root.is_symlink():
        marker = trace_root / _LIVE_TRACE_MARKER
        if (
            not replace_live
            or trace_root.is_symlink()
            or not trace_root.is_dir()
            or marker.is_symlink()
            or not marker.is_file()
        ):
            raise ValueError("Session trace path must not be created by the Coding Agent")
    raw_files: list[tuple[PurePosixPath, bytes]] = []
    raw_paths: set[str] = set()
    reserved_paths = {
        "provider/stdout.stream-json",
        "provider/stderr.log",
    }
    for raw_file in result.raw_session_files:
        relative = PurePosixPath(raw_file.relative_path)
        normalized = relative.as_posix()
        if (
            relative.is_absolute()
            or normalized == "."
            or ".." in relative.parts
            or not relative.parts
            or relative.parts[0] != "provider"
            or normalized in reserved_paths
            or normalized in raw_paths
        ):
            raise ValueError("Raw Provider Session file has an unsafe path")
        raw_paths.add(normalized)
        raw_files.append((relative, raw_file.payload))
    if trace_root.exists():
        shutil.rmtree(trace_root)
    trace_root.mkdir(mode=0o700)
    atomic_text(trace_root / "input/prompt.md", prompt)
    atomic_text(trace_root / "provider/stdout.stream-json", result.stdout)
    atomic_text(trace_root / "provider/stderr.log", result.stderr)
    for relative, payload in raw_files:
        atomic_bytes(trace_root.joinpath(*relative.parts), payload)

    normalized_events: list[dict[str, Any]] = [
        {"type": "session", "version": 0, "id": result.session_id}
    ]
    for event in result.events:
        data: dict[str, Any] = {
            "schema_version": 1,
            "sequence": event.sequence,
            "kind": event.kind,
        }
        if event.usage is not None:
            data["usage"] = {
                "uncached_input_tokens": event.usage.input_tokens,
                "output_tokens": event.usage.output_tokens,
                "cache_read_tokens": event.usage.cache_read_tokens,
                "cache_write_tokens": event.usage.cache_write_tokens,
                "total_tokens": event.usage.total_tokens,
                "measurement": event.usage.measurement,
            }
        normalized_events.append(
            {
                "type": "provider/usage",
                "seq": event.sequence,
                "time": event.sequence,
                "data": data,
                "ignorable": True,
            }
        )
    atomic_text(
        trace_root / "events.jsonl",
        "".join(
            json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n"
            for event in normalized_events
        ),
    )
    atomic_json(
        trace_root / "session.json",
        {
            "schema_version": 1,
            "runtime_id": result.runtime_id,
            "reasoning_effort": config.reasoning_effort,
            "runtime_bound": config.runtime_bound,
            "session_settings_sha256": hashlib.sha256(
                config.session_settings.encode("utf-8")
            ).hexdigest(),
            "session_id": result.session_id,
            "state": "finished",
            "exit_status": result.exit_status,
            "timed_out": result.timed_out,
            "raw_provider_capture_complete": result.raw_provider_capture_complete,
            "observation_errors": list(result.observation_errors),
            "policy_diagnostics": list(result.policy_diagnostics),
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
    session_id = str(uuid.uuid4())
    live_trace = start_live_trace(
        context,
        prompt,
        runtime_id=runtime.id,
        session_id=session_id,
        config=config,
    )
    try:
        result = runtime.run(
            backends.AgentRunRequest(
                workspace=context.workspace,
                prompt=prompt,
                timeout_s=max(1, int(context.timeout_seconds) - 5),
                reasoning_effort=config.reasoning_effort,
                session_id=session_id,
                session_settings=config.session_settings,
                token_budget=context.token_budget,
                live_trace_path=context.session_trace_path if live_trace else None,
            )
        )
        write_trace(context, result, prompt, config, replace_live=live_trace)
        if not result.raw_provider_capture_complete:
            return 126
        if result.budget_exhausted:
            return 125
        if result.timed_out:
            return 124
        if result.exit_status == 0 and on_success is not None:
            on_success()
        return result.exit_status
    except BaseException as error:
        mark_live_trace_interrupted(context, error)
        raise
    finally:
        atomic_json(context.token_usage_path, usage_report(context, result))


def guarded_main(run: Callable[[], int]) -> int:
    """Map an entrypoint failure to a stable process exit status."""
    try:
        return run()
    except Exception as error:
        print(f"[atrex-core] {type(error).__name__}: {error}", file=sys.stderr, flush=True)
        return 1
