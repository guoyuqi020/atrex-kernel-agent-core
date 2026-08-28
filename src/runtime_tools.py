#!/usr/bin/env python3
"""Framework-neutral CLI bindings from a Core Agent to trusted Runtime services."""

from __future__ import annotations

import argparse
import base64
import errno
import hashlib
import json
import math
import os
import stat
import tempfile
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

from contexts.attempt import RuntimeAttemptContext
from contexts.lineage_bootstrap import RuntimeLineageBootstrapContext
from tool_contracts import local_validation_issue, tool_recovery, tool_request_schema

_CANDIDATE_OPERATIONS = {
    "evaluate",
    "profile",
    "dev",
    "check",
    "disassemble",
}
_GATEWAY_EXECUTE_OPERATIONS = _CANDIDATE_OPERATIONS | {
    "poll",
    "jobs",
    "cancel",
    "env",
    "health",
    "config",
}
_PUBLIC_RUNTIME_QUERY_COMMANDS = {
    "kernel-trial-show": "kernel_trial_show",
    "kernel-artifact-read": "kernel_artifact_read",
    "gateway-result-read": "gateway_result_read",
}
_RUNTIME_QUERY_COMMANDS = dict(_PUBLIC_RUNTIME_QUERY_COMMANDS)
_RUNTIME_QUERY_OPERATIONS = frozenset(_RUNTIME_QUERY_COMMANDS.values())
_RUNTIME_JOURNAL_COMMANDS = {
    "update-direction": "direction_update",
    "list-directions": "directions_list",
    "load-direction": "direction_load",
    "record-experiment": "experiment_record",
    "list-experiments": "experiments_list",
    "load-experiment": "experiment_load",
    "_journal-snapshot": "journal_snapshot",
}
_ATTEMPT_COMMANDS = (
    "gateway-execute",
    *_PUBLIC_RUNTIME_QUERY_COMMANDS,
    "wiki-query",
    "update-direction",
    "list-directions",
    "load-direction",
    "record-experiment",
    "list-experiments",
    "load-experiment",
    "attempt-report",
)
_RESERVED_REQUEST_FIELDS = {"schema_version", "attempt_id", "candidate", "idempotency_key"}
_EXPERIMENT_FIELDS = {
    "direction_id",
    "name",
    "hypothesis",
    "change",
    "before",
    "after",
    "evidence",
    "analysis",
    "action",
}
_DIRECTION_PROPOSAL_FIELDS = {
    "action",
    "name",
    "hypothesis",
    "rationale",
    "plan",
    "success_criteria",
    "stop_conditions",
}
_DIRECTION_UPDATE_FIELDS = {
    "action",
    "direction_id",
    "analysis",
}
_EXPERIMENT_SUBJECT_FIELDS = {
    "kernel_artifact_digest",
    "kernel_trial_id",
    "gateway_result_digests",
}
_REPORT_FIELDS = {
    "status",
    "hypothesis",
    "diagnosis",
    "approach",
    "final_candidate",
    "evidence_summary",
    "profile_evidence",
    "analysis",
    "knowledge_used",
    "findings",
    "blocker",
}
RuntimeToolContext = RuntimeAttemptContext | RuntimeLineageBootstrapContext

_MAX_REQUEST_BYTES = 256 * 1024
_MAX_CANDIDATE_FILES = 4096
_MAX_CANDIDATE_BYTES = 64 * 1024 * 1024
_MAX_RESPONSE_BYTES = 16 * 1024 * 1024
_MAX_ERROR_RESPONSE_BYTES = 64 * 1024
_HTTP_TIMEOUT_SECONDS = 600
# Seven ten-minute reads outlast Runtime's own Agate wait, so a slow operation is
# collected on a later reconnect instead of being abandoned half-finished.
_MAX_TIMEOUT_RECONNECTS = 6


class RuntimeServiceError(RuntimeError):
    """Structured non-success response returned by a trusted Runtime service."""

    def __init__(self, status_code: int, payload: dict[str, Any]) -> None:
        self.status_code = status_code
        self.payload = payload
        super().__init__(str(payload.get("detail", payload.get("error", "Runtime service error"))))


def _read_object(
    path: Path,
    label: str,
    *,
    max_bytes: int,
) -> dict[str, Any]:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ValueError(f"{label} must be a regular file")
    if info.st_size > max_bytes:
        raise ValueError(f"{label} exceeds its byte limit")
    try:
        value = json.loads(path.read_bytes())
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError(f"{label} is not valid JSON") from error
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _atomic_json(path: Path, value: object, *, exclusive: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode()
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as output:
            temporary = Path(output.name)
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, 0o600)
        if exclusive:
            try:
                os.link(temporary, path)
            except OSError as error:
                if error.errno == errno.EEXIST:
                    raise FileExistsError(f"terminal report already exists: {path}") from error
                raise
        else:
            os.replace(temporary, path)
            temporary = None
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _scratch_output(
    context: RuntimeToolContext,
    value: object,
    *,
    label: str = "Kernel Artifact output",
) -> tuple[Path, str]:
    if not isinstance(value, str):
        raise ValueError(f"{label} file must be a string")
    relative = PurePosixPath(value)
    if (
        relative.is_absolute()
        or len(relative.parts) < 2
        or relative.parts[0] != "scratch"
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise ValueError(f"{label} file must be a safe path under scratch/")
    current = context.workspace
    for part in relative.parent.parts:
        current /= part
        if current.exists():
            metadata = current.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                raise ValueError(f"{label} parent must be a real directory")
        else:
            current.mkdir(mode=0o700)
    destination = context.workspace.joinpath(*relative.parts)
    if destination.exists() or destination.is_symlink():
        metadata = destination.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise ValueError(f"{label} must be a regular file")
    return destination, relative.as_posix()


def _atomic_bytes(path: Path, payload: bytes) -> None:
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as output:
            temporary = Path(output.name)
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        temporary = None
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _post(url: str, capability: str, path: str, value: object) -> dict[str, Any]:
    payload = json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
    request = urllib.request.Request(
        url.rstrip("/") + path,
        data=payload,
        method="POST",
        headers={
            "authorization": f"Bearer {capability}",
            "content-type": "application/json",
        },
    )
    for _ in range(_MAX_TIMEOUT_RECONNECTS + 1):
        try:
            return _exchange(request)
        except TimeoutError:
            continue
        except urllib.error.HTTPError:
            raise
        except urllib.error.URLError as error:
            if not isinstance(error.reason, TimeoutError):
                raise RuntimeError(f"Runtime service is unavailable: {error.reason}") from error
    raise RuntimeError(
        "Runtime service did not answer within "
        f"{(_MAX_TIMEOUT_RECONNECTS + 1) * _HTTP_TIMEOUT_SECONDS}s; "
        "repeat the identical request to collect the result"
    )


def _exchange(request: urllib.request.Request) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(request, timeout=_HTTP_TIMEOUT_SECONDS) as response:
            body = response.read(_MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as error:
        body = error.read(_MAX_ERROR_RESPONSE_BYTES + 1)
        if len(body) > _MAX_ERROR_RESPONSE_BYTES:
            error_payload: dict[str, Any] = {
                "error": "runtime_service_error",
                "detail": "Runtime service error response exceeds the Core byte limit",
            }
        else:
            try:
                decoded = json.loads(body)
            except (json.JSONDecodeError, UnicodeDecodeError):
                detail = body.decode("utf-8", errors="replace").strip()
                error_payload = {
                    "error": "runtime_service_error",
                    "detail": detail or f"Runtime service returned HTTP {error.code}",
                }
            else:
                error_payload = (
                    decoded
                    if isinstance(decoded, dict)
                    else {
                        "error": "runtime_service_error",
                        "detail": "Runtime service returned a non-object error response",
                    }
                )
        raise RuntimeServiceError(error.code, error_payload) from error
    if len(body) > _MAX_RESPONSE_BYTES:
        raise RuntimeError("Runtime service response exceeds the Core byte limit")
    result = json.loads(body)
    if not isinstance(result, dict):
        raise RuntimeError("Runtime service returned a non-object response")
    return result


def _candidate(root: Path) -> dict[str, object]:
    files: list[dict[str, str]] = []
    total_bytes = 0
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if path.is_symlink():
            raise ValueError(f"Candidate contains a symbolic link: {relative}")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError(f"Candidate contains a special file: {relative}")
        pure = PurePosixPath(relative.as_posix())
        if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
            raise ValueError(f"Candidate contains an unsafe path: {relative}")
        content = path.read_bytes()
        total_bytes += len(content)
        if len(files) + 1 > _MAX_CANDIDATE_FILES:
            raise ValueError("Candidate exceeds the Core file-count limit")
        if total_bytes > _MAX_CANDIDATE_BYTES:
            raise ValueError("Candidate exceeds the Core byte limit")
        files.append(
            {
                "path": pure.as_posix(),
                "content_base64": base64.b64encode(content).decode("ascii"),
            }
        )
    if not files:
        raise ValueError("Candidate Kernel is empty")
    return {"files": files}


def _idempotency_key(prefix: str, value: object) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return f"core-{prefix}-{hashlib.sha256(payload).hexdigest()[:32]}"


def _finite_number(value: object, *, positive: bool = False) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or (positive and number <= 0):
        return None
    return number


def _duration_us(kernel: dict[str, Any]) -> float | None:
    duration = _finite_number(kernel.get("duration"), positive=True)
    unit = kernel.get("duration_unit")
    if duration is None or not isinstance(unit, str):
        return _finite_number(kernel.get("duration_us"), positive=True)
    scale = {"ns": 0.001, "us": 1.0, "ms": 1_000.0, "s": 1_000_000.0}.get(unit)
    return None if scale is None else duration * scale


def _profile_kernel(raw: dict[str, Any]) -> dict[str, Any]:
    """Preserve safe profiler evidence while adding stable Agent-facing aliases."""
    kernel = dict(raw)
    name = raw.get("name", raw.get("kernel_name"))
    if isinstance(name, str) and name:
        kernel["name"] = name
    kernel.pop("kernel_name", None)

    duration_us = _duration_us(raw)
    if duration_us is not None:
        kernel["duration_us"] = duration_us
    kernel.pop("duration", None)
    kernel.pop("duration_unit", None)

    memory_sol = _finite_number(raw.get("memory_sol_pct"))
    if memory_sol is None:
        memory_sol = _finite_number(raw.get("mem_sol_pct"))
    if memory_sol is not None:
        kernel["memory_sol_pct"] = memory_sol
    kernel.pop("mem_sol_pct", None)

    aliases = {
        "registers": "registers_per_thread",
        "smem_bytes": "shared_memory_bytes",
    }
    for source, target in aliases.items():
        value = _finite_number(raw.get(target))
        if value is None:
            value = _finite_number(raw.get(source))
        if value is not None:
            kernel[target] = value
        kernel.pop(source, None)

    compute_sol = _finite_number(raw.get("compute_sol_pct"))
    bound = raw.get("bound")
    if (
        (not isinstance(bound, str) or not bound)
        and compute_sol is not None
        and memory_sol is not None
    ):
        kernel["bound"] = "compute" if compute_sol > memory_sol else "memory"
    return kernel


def _profile_result(
    raw: dict[str, Any],
    request: dict[str, Any],
) -> dict[str, Any]:
    kernels_value = raw.get("kernels")
    if not isinstance(kernels_value, list):
        return raw
    kernels = [_profile_kernel(item) for item in kernels_value if isinstance(item, dict)]
    durations = [
        duration
        for kernel in kernels
        if (duration := _finite_number(kernel.get("duration_us"), positive=True)) is not None
    ]
    total_duration = sum(durations)
    if total_duration > 0:
        for kernel in kernels:
            duration = _finite_number(kernel.get("duration_us"), positive=True)
            if duration is not None:
                kernel["duration_share_pct"] = duration * 100.0 / total_duration

    weighted_sol = 0.0
    weighted_sol_duration = 0.0
    weighted_compute = 0.0
    weighted_memory = 0.0
    bound_duration = 0.0
    for kernel in kernels:
        duration = _finite_number(kernel.get("duration_us"), positive=True)
        compute = _finite_number(kernel.get("compute_sol_pct"))
        memory = _finite_number(kernel.get("memory_sol_pct"))
        if duration is None or compute is None or memory is None:
            continue
        weighted_sol += max(compute, memory) * duration
        weighted_sol_duration += duration
        weighted_compute += compute * duration
        weighted_memory += memory * duration
        bound_duration += duration

    result: dict[str, Any] = {
        key: value for key, value in raw.items() if key not in {"kernels", "shape_id"}
    }
    shape_id = request.get("shape_id", raw.get("shape_id"))
    if isinstance(shape_id, int) and not isinstance(shape_id, bool) and shape_id >= 0:
        result["shape_id"] = str(shape_id)
    elif isinstance(shape_id, str) and shape_id.isdecimal():
        result["shape_id"] = shape_id
    level = request.get("level")
    if isinstance(level, str) and level:
        result["profile_level"] = level
    result["kernel_count"] = len(kernels)
    if total_duration > 0:
        result["total_duration_us"] = total_duration
        dominant = max(
            kernels,
            key=lambda item: _finite_number(item.get("duration_us"), positive=True) or 0.0,
        )
        name = dominant.get("name")
        if isinstance(name, str) and name:
            result["dominant_kernel"] = name
    if weighted_sol_duration > 0:
        result["weighted_sol_pct"] = weighted_sol / weighted_sol_duration
    if bound_duration > 0:
        result["dominant_bound"] = "compute" if weighted_compute > weighted_memory else "memory"
    result["kernels"] = kernels
    return result


def _profile_agent_response(
    response: dict[str, Any],
    request: dict[str, Any],
) -> dict[str, Any]:
    worker = response.get("result")
    if not isinstance(worker, dict):
        raise ValueError("Gateway profile response has no object result")
    visible = {
        "kernel_artifact_digest": response.get("kernel_artifact_digest"),
        "kernel_trial_id": response.get("kernel_trial_id"),
        "gateway_result_digest": response.get("gateway_result_digest"),
        **worker,
    }
    raw_result = worker.get("result")
    if isinstance(raw_result, dict):
        visible["result"] = _profile_result(raw_result, request)
    return visible


def _agent_gateway_response(
    response: dict[str, Any],
    request: dict[str, Any],
) -> dict[str, Any]:
    operation = response.get("operation")
    if operation == "profile":
        return _profile_agent_response(response, request)
    if operation in {
        "dev",
        "disassemble",
        "jobs",
        "poll",
        "cancel",
        "env",
        "health",
        "config",
    }:
        result = response.get("result")
        if not isinstance(result, dict):
            raise ValueError(f"Gateway {operation} response has no object result")
        return result
    visible = {
        key: item for key, item in response.items() if key not in {"schema_version", "evaluation"}
    }
    evaluation = response.get("evaluation")
    if evaluation is None:
        return visible
    if not isinstance(evaluation, dict):
        raise ValueError("Gateway returned an invalid evaluation")
    result = visible.get("result")
    if not isinstance(result, dict):
        raise ValueError("Gateway returned an evaluation without an object result")
    merged = dict(result)
    merged.pop("all_pass", None)
    merged.pop("latency_us_geomean", None)
    merged["correct"] = evaluation.get("correct")
    merged["latency_us"] = evaluation.get("latency_us")
    visible["result"] = merged
    return visible


def gateway_execute(context: RuntimeToolContext, request: dict[str, Any]) -> dict[str, Any]:
    overlap = _RESERVED_REQUEST_FIELDS.intersection(request)
    if overlap:
        raise ValueError(f"Gateway request sets Runtime-owned fields: {sorted(overlap)}")
    operation = request.get("operation")
    if not isinstance(operation, str) or not operation:
        raise ValueError("Gateway request requires operation")
    if operation in _RUNTIME_QUERY_OPERATIONS:
        command = next(
            name
            for name, query_operation in _RUNTIME_QUERY_COMMANDS.items()
            if query_operation == operation
        )
        raise ValueError(
            f"Runtime-local operation {operation!r} must use the {command!r} subcommand"
        )
    if operation not in _GATEWAY_EXECUTE_OPERATIONS:
        raise ValueError(f"unsupported gateway-execute operation: {operation}")
    value = {"schema_version": 2, "attempt_id": context.attempt_id, **request}
    if operation == "dev":
        value["files"] = _dev_files(context, value)
    if operation in _CANDIDATE_OPERATIONS:
        value["candidate"] = _candidate(context.working_kernel)
    value["idempotency_key"] = _idempotency_key("gateway", value)
    response = _post(context.gateway_url, context.gateway_capability, "/v1/operations", value)
    return _agent_gateway_response(response, request)


def _dev_files(context: RuntimeToolContext, value: dict[str, Any]) -> list[dict[str, str]]:
    """Resolve `file_paths` into the wire `files` field, keeping inline entries.

    Naming a workspace path costs far less than inlining Base64, and it is why a
    multi-line probe no longer has to be smuggled through `command`.
    """
    files: list[dict[str, str]] = []
    seen: set[str] = set()
    inline = value.get("files") or []
    if not isinstance(inline, list):
        raise ValueError("dev files must be a list")
    for entry in inline:
        if not isinstance(entry, dict) or "path" not in entry:
            raise ValueError("dev file entry requires path")
        files.append(entry)
        seen.add(str(entry["path"]))
    paths = value.pop("file_paths", None) or []
    if not isinstance(paths, list):
        raise ValueError("dev file_paths must be a list")
    workspace = context.workspace.resolve()
    for raw in paths:
        if not isinstance(raw, str) or not raw:
            raise ValueError("dev file_paths entries must be non-empty strings")
        pure = PurePosixPath(raw)
        if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
            raise ValueError(f"dev file path is unsafe: {raw}")
        source = (workspace / pure).resolve()
        if not source.is_relative_to(workspace):
            raise ValueError(f"dev file path escapes the workspace: {raw}")
        if source.is_symlink() or not source.is_file():
            raise ValueError(f"dev file is missing: {raw}")
        if pure.name in seen:
            raise ValueError(f"dev files repeat {pure.name}")
        seen.add(pure.name)
        files.append(
            {
                "path": pure.name,
                "content_base64": base64.b64encode(source.read_bytes()).decode("ascii"),
            }
        )
    return files


def runtime_query(
    context: RuntimeToolContext,
    command: str,
    request: dict[str, Any],
) -> dict[str, Any]:
    """Execute one Runtime-local, visibility-scoped history query without Agate."""
    try:
        operation = _RUNTIME_QUERY_COMMANDS[command]
    except KeyError as error:
        raise ValueError(f"unsupported Runtime query command: {command}") from error
    overlap = (_RESERVED_REQUEST_FIELDS | {"operation"}).intersection(request)
    if overlap:
        raise ValueError(f"Runtime query sets Runtime-owned fields: {sorted(overlap)}")
    destination: Path | None = None
    destination_name: str | None = None
    query_request = request
    if command == "kernel-artifact-read":
        unknown = set(request) - {"kernel_artifact_digest", "artifact_file", "file"}
        if unknown:
            raise ValueError(f"unknown Kernel Artifact read fields: {sorted(unknown)}")
        destination, destination_name = _scratch_output(context, request.get("file"))
        artifact_file = request.get("artifact_file")
        if artifact_file is None:
            artifact_file = PurePosixPath(destination_name).name
        if not isinstance(artifact_file, str):
            raise ValueError("artifact_file must be a string")
        query_request = {
            "kernel_artifact_digest": request.get("kernel_artifact_digest"),
            "file": artifact_file,
        }
    value = {
        "schema_version": 2,
        "attempt_id": context.attempt_id,
        "operation": operation,
        **query_request,
    }
    value["idempotency_key"] = _idempotency_key("runtime-query", value)
    response = _post(
        context.gateway_url,
        context.gateway_capability,
        "/v1/runtime/queries",
        value,
    )
    result = response.get("result")
    if not isinstance(result, dict):
        raise ValueError(f"{command} returned an invalid response")
    if command == "gateway-result-read":
        return result
    if command == "kernel-artifact-read":
        if not isinstance(result, dict) or destination is None or destination_name is None:
            raise ValueError("Kernel Artifact read returned an invalid response")
        content = result.get("content")
        encoding = result.get("encoding")
        if not isinstance(content, str):
            raise ValueError("Kernel Artifact read returned no file content")
        if encoding == "utf-8":
            payload = content.encode("utf-8")
        elif encoding == "base64":
            try:
                payload = base64.b64decode(content, validate=True)
            except ValueError as error:
                raise ValueError("Kernel Artifact read returned invalid Base64") from error
        else:
            raise ValueError("Kernel Artifact read returned an unknown encoding")
        _atomic_bytes(destination, payload)
        return {
            "status": "completed",
            "file": destination_name,
            "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
    return result


def runtime_journal(
    context: RuntimeToolContext,
    command: str,
    request: dict[str, Any],
) -> dict[str, Any]:
    """Execute one authoritative Runtime Journal mutation or read."""
    try:
        operation = _RUNTIME_JOURNAL_COMMANDS[command]
    except KeyError as error:
        raise ValueError(f"unsupported Runtime Journal command: {command}") from error
    value: dict[str, Any] = {
        "schema_version": 2,
        "attempt_id": context.attempt_id,
        "operation": operation,
    }
    if command in {"update-direction", "record-experiment"}:
        value["request"] = request
        value["idempotency_key"] = _idempotency_key("runtime-journal", value)
    elif command == "load-direction":
        value["direction_id"] = request.get("direction_id")
        value["idempotency_key"] = f"core-journal-read-{uuid4().hex}"
    elif command == "load-experiment":
        value["experiment_id"] = request.get("experiment_id")
        value["idempotency_key"] = f"core-journal-read-{uuid4().hex}"
    else:
        value["idempotency_key"] = f"core-journal-read-{uuid4().hex}"
    response = _post(
        context.gateway_url,
        context.gateway_capability,
        "/v1/runtime/journals",
        value,
    )
    result = response.get("result")
    if not isinstance(result, dict):
        raise ValueError(f"{command} returned an invalid Runtime Journal response")
    return result


def wiki_query(context: RuntimeToolContext, request: dict[str, Any]) -> dict[str, Any]:
    if context.wiki_url is None or context.wiki_capability is None:
        raise RuntimeError("GPU Wiki capability is unavailable for this Attempt")
    unknown = set(request) - {"query"}
    if unknown:
        raise ValueError(f"unknown Wiki request fields: {sorted(unknown)}")
    query = request.get("query")
    if not isinstance(query, str) or not query.strip():
        raise ValueError("Wiki request requires a non-empty query")
    value = {"schema_version": 1, "attempt_id": context.attempt_id, "query": query}
    value["idempotency_key"] = _idempotency_key("wiki", value)
    return _agent_knowledge(
        _post(context.wiki_url, context.wiki_capability, "/v1/wiki/query", value)
    )


def _agent_knowledge(response: dict[str, Any]) -> dict[str, Any]:
    """Expose knowledge content while retaining audit identities inside Runtime."""
    content = response.get("content")
    if not isinstance(content, dict):
        raise RuntimeError("Runtime Wiki response has no Agent-readable content object")
    return content


def _direction_event_fields() -> set[str]:
    return {
        "direction_event_id",
        "direction_id",
        "recorded_at",
        "action",
        "name",
        "hypothesis",
        "rationale",
        "plan",
        "success_criteria",
        "stop_conditions",
        "analysis",
        "supporting_experiment_ids",
    }


def _validate_direction_id(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value.startswith("direction_")
        or len(value) != len("direction_") + 32
        or any(character not in "0123456789abcdef" for character in value[10:])
    ):
        raise ValueError("Direction ID is invalid")
    return value


def _validate_experiment_id_array(value: object, label: str) -> list[str]:
    values = _text_array(value, label)
    if len(values) > 32 or len(set(values)) != len(values):
        raise ValueError(f"{label} must contain at most 32 unique IDs")
    for experiment_id in values:
        if (
            not experiment_id.startswith("experiment_")
            or len(experiment_id) != len("experiment_") + 32
            or any(character not in "0123456789abcdef" for character in experiment_id[11:])
        ):
            raise ValueError(f"{label} contains an invalid Experiment ID")
    return values


def _validate_direction_events(events: list[Any], label: str) -> list[dict[str, Any]]:
    validated: list[dict[str, Any]] = []
    for event in events:
        if not isinstance(event, dict) or set(event) != _direction_event_fields():
            raise ValueError(f"{label} contains a malformed event")
        _validate_direction_id(event.get("direction_id"))
        event_id = event.get("direction_event_id")
        if (
            not isinstance(event_id, str)
            or not event_id.startswith("directionevent_")
            or len(event_id) != len("directionevent_") + 32
            or any(character not in "0123456789abcdef" for character in event_id[15:])
        ):
            raise ValueError("Direction Event ID is invalid")
        recorded_at = _text(event.get("recorded_at"), "Direction recorded_at")
        try:
            datetime.fromisoformat(recorded_at.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("Direction recorded_at must be ISO-8601") from error
        action = event.get("action")
        if action not in {"propose", "start", "complete", "abandon", "block", "defer"}:
            raise ValueError("Direction action is invalid")
        supporting = _validate_experiment_id_array(
            event.get("supporting_experiment_ids"),
            "Direction supporting_experiment_ids",
        )
        if action == "propose":
            for field in (
                "name",
                "hypothesis",
                "rationale",
                "success_criteria",
                "stop_conditions",
            ):
                _text(event.get(field), f"Direction {field}")
            _text_array(event.get("plan"), "Direction plan", required=True)
            if event.get("analysis") is not None or supporting:
                raise ValueError("Direction proposal cannot contain outcome evidence")
        else:
            if (
                any(
                    event.get(field) is not None
                    for field in (
                        "name",
                        "hypothesis",
                        "rationale",
                        "success_criteria",
                        "stop_conditions",
                    )
                )
                or event.get("plan") != []
            ):
                raise ValueError("Direction update cannot redefine its proposal")
            _text(event.get("analysis"), "Direction analysis")
            if action in {"complete", "abandon"} and not supporting:
                raise ValueError(f"Direction {action} requires supporting Experiments")
        validated.append(event)
    return validated


def update_direction(context: RuntimeToolContext, request: dict[str, Any]) -> dict[str, Any]:
    return runtime_journal(context, "update-direction", request)


def list_directions(context: RuntimeToolContext, request: dict[str, Any]) -> dict[str, Any]:
    if set(request) != {"file"}:
        raise ValueError("list-directions request requires exactly file")
    destination, destination_name = _scratch_output(
        context,
        request.get("file"),
        label="Direction index output",
    )
    value = runtime_journal(context, "list-directions", {})
    directions = value.get("directions")
    if set(value) != {"directions"} or not isinstance(directions, list):
        raise ValueError("Runtime returned an invalid Direction index")
    _atomic_json(destination, value)
    return {
        "status": "completed",
        "file": destination_name,
        "count": len(directions),
    }


def load_direction(context: RuntimeToolContext, request: dict[str, Any]) -> dict[str, Any]:
    if set(request) != {"direction_id"}:
        raise ValueError("load-direction request requires exactly direction_id")
    return runtime_journal(context, "load-direction", request)


def _experiment_subject(value: object, label: str) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != _EXPERIMENT_SUBJECT_FIELDS:
        raise ValueError(
            f"Experiment {label} fields must be exactly {sorted(_EXPERIMENT_SUBJECT_FIELDS)}"
        )
    digest = value.get("kernel_artifact_digest")
    if (
        not isinstance(digest, str)
        or not digest.startswith("sha256:")
        or len(digest) != len("sha256:") + 64
        or any(character not in "0123456789abcdef" for character in digest[7:])
    ):
        raise ValueError(f"Experiment {label} kernel_artifact_digest is invalid")
    trial_id = value.get("kernel_trial_id")
    if (
        not isinstance(trial_id, str)
        or not trial_id.startswith("gtrial_")
        or len(trial_id) != len("gtrial_") + 32
        or any(character not in "0123456789abcdef" for character in trial_id[7:])
    ):
        raise ValueError(f"Experiment {label} kernel_trial_id is invalid")
    results = value.get("gateway_result_digests")
    if (
        not isinstance(results, list)
        or not results
        or len(results) > 32
        or len(set(item for item in results if isinstance(item, str))) != len(results)
    ):
        raise ValueError(
            f"Experiment {label} gateway_result_digests must be a non-empty unique list"
        )
    for result in results:
        if (
            not isinstance(result, str)
            or not result.startswith("sha256:")
            or len(result) != len("sha256:") + 64
            or any(character not in "0123456789abcdef" for character in result[7:])
        ):
            raise ValueError(f"Experiment {label} gateway_result_digests is invalid")
    return value


def _validate_experiment_comparison(
    request: dict[str, Any],
    *,
    allow_baseline: bool,
) -> None:
    before = _experiment_subject(request.get("before"), "before")
    after = _experiment_subject(request.get("after"), "after")
    if request.get("action") == "baseline":
        if not allow_baseline:
            raise ValueError("Experiment action baseline is only valid during Bootstrap")
        if before is not None or after is None:
            raise ValueError("Experiment baseline requires before=null and complete after evidence")
        return
    if (before is None) != (after is None):
        raise ValueError("Experiment before and after must both be present or both be null")
    if request.get("action") in {"keep_after", "restore_before"} and before is None:
        raise ValueError("Experiment keep_after/restore_before requires before and after evidence")


def record_experiment(context: RuntimeToolContext, request: dict[str, Any]) -> dict[str, Any]:
    return runtime_journal(context, "record-experiment", request)


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be non-empty text")
    return value


def _text_array(value: object, label: str, *, required: bool = False) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ValueError(f"{label} must be an array of non-empty text")
    if required and not value:
        raise ValueError(f"{label} must not be empty")
    return value


def _exact_object(value: object, label: str, fields: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError(f"{label} fields must be exactly {sorted(fields)}")
    return value


def _object_array(
    value: object,
    label: str,
    fields: set[str],
    *,
    required: bool = False,
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be an array")
    if required and not value:
        raise ValueError(f"{label} must not be empty")
    objects: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        entry = _exact_object(item, f"{label}[{index}]", fields)
        for field in fields:
            _text(entry.get(field), f"{label}[{index}].{field}")
        objects.append(entry)
    return objects


def _validate_experiment_entries(
    experiments: list[Any],
    label: str,
    *,
    allow_baseline: bool,
) -> list[dict[str, Any]]:
    expected_fields = _EXPERIMENT_FIELDS | {"experiment_id", "sequence", "recorded_at"}
    validated: list[dict[str, Any]] = []
    for sequence, experiment in enumerate(experiments, start=1):
        if not isinstance(experiment, dict) or set(experiment) != expected_fields:
            raise ValueError(f"{label} contains a malformed entry")
        if experiment.get("sequence") != sequence:
            raise ValueError(f"{label} sequence must be contiguous")
        experiment_id = experiment.get("experiment_id")
        if (
            not isinstance(experiment_id, str)
            or not experiment_id.startswith("experiment_")
            or len(experiment_id) != len("experiment_") + 32
            or any(character not in "0123456789abcdef" for character in experiment_id[11:])
        ):
            raise ValueError("Experiment experiment_id is invalid")
        _text(experiment.get("recorded_at"), "Experiment recorded_at")
        try:
            datetime.fromisoformat(str(experiment["recorded_at"]).replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("Experiment recorded_at must be ISO-8601") from error
        for field in _EXPERIMENT_FIELDS - {"action", "before", "after"}:
            _text(experiment.get(field), f"Experiment {field}")
        _validate_direction_id(experiment.get("direction_id"))
        allowed_actions = {
            "keep_after",
            "restore_before",
            "abandon_direction",
        }
        if allow_baseline:
            allowed_actions.add("baseline")
        if experiment.get("action") not in allowed_actions:
            raise ValueError("Experiment action is invalid")
        _validate_experiment_comparison(experiment, allow_baseline=allow_baseline)
        validated.append(experiment)
    return validated


def list_experiments(
    context: RuntimeToolContext,
    request: dict[str, Any],
) -> dict[str, Any]:
    """Write a compact index of visible historical and current Experiments."""
    if set(request) != {"file"}:
        raise ValueError("list-experiments request requires exactly file")
    destination, destination_name = _scratch_output(
        context,
        request.get("file"),
        label="Experiment index output",
    )
    value = runtime_journal(context, "list-experiments", {})
    experiments = value.get("experiments")
    if set(value) != {"experiments"} or not isinstance(experiments, list):
        raise ValueError("Runtime returned an invalid Experiment index")
    _atomic_json(destination, value)
    return {
        "status": "completed",
        "file": destination_name,
        "count": len(value["experiments"]),
    }


def load_experiment(
    context: RuntimeToolContext,
    request: dict[str, Any],
) -> dict[str, Any]:
    """Return one exact visible historical or current Experiment record."""
    if set(request) != {"experiment_id"}:
        raise ValueError("load-experiment request requires exactly experiment_id")
    return runtime_journal(context, "load-experiment", request)


def attempt_report(context: RuntimeToolContext, request: dict[str, Any]) -> dict[str, Any]:
    if set(request) != _REPORT_FIELDS:
        raise ValueError(f"Attempt report fields must be exactly {sorted(_REPORT_FIELDS)}")
    snapshot = runtime_journal(context, "_journal-snapshot", {})
    if set(snapshot) != {"direction_events", "experiments", "directions"}:
        raise ValueError("Runtime returned an invalid Journal snapshot")
    experiment_values = snapshot.get("experiments")
    direction_event_values = snapshot.get("direction_events")
    direction_values = snapshot.get("directions")
    if not isinstance(experiment_values, list) or not experiment_values:
        raise ValueError("Attempt report requires at least one Runtime Experiment")
    if not isinstance(direction_event_values, list) or not direction_event_values:
        raise ValueError("Attempt report requires at least one Runtime Direction event")
    if not isinstance(direction_values, list):
        raise ValueError("Runtime returned invalid normalized Directions")
    experiments = _validate_experiment_entries(
        experiment_values,
        "Runtime Experiment Journal",
        allow_baseline=isinstance(context, RuntimeLineageBootstrapContext),
    )
    direction_events = _validate_direction_events(
        direction_event_values,
        "Runtime Direction Journal",
    )
    directions: dict[str, dict[str, Any]] = {}
    for direction in direction_values:
        if not isinstance(direction, dict):
            raise ValueError("Runtime returned a malformed normalized Direction")
        direction_id = _validate_direction_id(direction.get("direction_id"))
        directions[direction_id] = direction
    experiment_direction_ids = {str(experiment["direction_id"]) for experiment in experiments}
    unknown_direction_ids = sorted(experiment_direction_ids - directions.keys())
    if unknown_direction_ids:
        raise ValueError(
            f"Attempt report Experiment references unknown Directions: {unknown_direction_ids}"
        )
    in_progress_direction_ids = sorted(
        direction_id
        for direction_id, direction in directions.items()
        if direction["status"] == "in_progress"
    )
    if in_progress_direction_ids:
        raise ValueError(
            f"Attempt report cannot leave any Direction in progress: {in_progress_direction_ids}"
        )
    status = request.get("status")
    if status not in {"candidate_ready", "pivot", "blocked"}:
        raise ValueError("Attempt report status is invalid")
    if isinstance(context, RuntimeLineageBootstrapContext):
        if status == "pivot":
            raise ValueError("Bootstrap Attempt report status must be candidate_ready or blocked")
        baseline_count = sum(experiment["action"] == "baseline" for experiment in experiments)
        if baseline_count > 1:
            raise ValueError("Bootstrap Attempt report may contain only one baseline Experiment")
        if status == "candidate_ready" and baseline_count != 1:
            raise ValueError(
                "Bootstrap candidate_ready report requires exactly one baseline Experiment"
            )
        has_identity_bearing_experiment = any(
            experiment[side] is not None
            for experiment in experiments
            for side in ("before", "after")
        )
        if status == "blocked" and baseline_count == 0 and has_identity_bearing_experiment:
            raise ValueError(
                "Bootstrap blocked report may omit baseline only when no Experiment has "
                "identity-bearing Gateway evidence"
            )
    _text(request.get("hypothesis"), "Attempt report hypothesis")
    _text(request.get("analysis"), "Attempt report analysis")
    diagnosis = _exact_object(
        request.get("diagnosis"),
        "Attempt report diagnosis",
        {"bottleneck", "evidence"},
    )
    for field in diagnosis:
        _text(diagnosis[field], f"Attempt report diagnosis.{field}")
    approach = _exact_object(
        request.get("approach"),
        "Attempt report approach",
        {"summary", "steps", "expected_impact", "risks"},
    )
    _text(approach.get("summary"), "Attempt report approach.summary")
    _text(approach.get("expected_impact"), "Attempt report approach.expected_impact")
    _text_array(approach.get("steps"), "Attempt report approach.steps", required=True)
    _text_array(approach.get("risks"), "Attempt report approach.risks")
    evidence_summary = _exact_object(
        request.get("evidence_summary"),
        "Attempt report evidence_summary",
        {"correctness", "performance"},
    )
    for field in evidence_summary:
        _text(evidence_summary[field], f"Attempt report evidence_summary.{field}")
    profile_evidence = request.get("profile_evidence")
    if profile_evidence is not None:
        profile_value = _exact_object(
            profile_evidence,
            "Attempt report profile_evidence",
            {
                "tool_used",
                "profiler",
                "profile_level",
                "bottleneck_type",
                "evidence_summary",
                "evidence_chain",
                "supporting_results",
            },
        )
        for field in (
            "tool_used",
            "profiler",
            "profile_level",
            "bottleneck_type",
            "evidence_summary",
            "evidence_chain",
        ):
            _text(profile_value[field], f"Attempt report profile_evidence.{field}")
        supporting_results = profile_value["supporting_results"]
        if not isinstance(supporting_results, list) or not supporting_results:
            raise ValueError("Attempt report profile_evidence.supporting_results must be non-empty")
        if len(supporting_results) > 32:
            raise ValueError("Attempt report profile_evidence supports at most 32 results")
        journal_bindings: set[tuple[str, str, str]] = set()
        for experiment in experiments:
            for side_name in ("before", "after"):
                side = experiment[side_name]
                if side is None:
                    continue
                for result_digest in side["gateway_result_digests"]:
                    journal_bindings.add(
                        (
                            side["kernel_artifact_digest"],
                            side["kernel_trial_id"],
                            result_digest,
                        )
                    )
        seen_results: set[str] = set()
        has_profile = False
        for index, item in enumerate(supporting_results):
            reference = _exact_object(
                item,
                f"Attempt report profile_evidence.supporting_results[{index}]",
                {
                    "operation",
                    "kernel_artifact_digest",
                    "kernel_trial_id",
                    "gateway_result_digest",
                },
            )
            operation = reference["operation"]
            if operation != "profile":
                raise ValueError("Profile supporting result operation must be profile")
            has_profile = True
            subject = _experiment_subject(
                {
                    "kernel_artifact_digest": reference["kernel_artifact_digest"],
                    "kernel_trial_id": reference["kernel_trial_id"],
                    "gateway_result_digests": [reference["gateway_result_digest"]],
                },
                f"Attempt report profile_evidence.supporting_results[{index}]",
            )
            assert subject is not None
            binding = (
                subject["kernel_artifact_digest"],
                subject["kernel_trial_id"],
                subject["gateway_result_digests"][0],
            )
            if binding not in journal_bindings:
                raise ValueError(
                    "Profile supporting result is not referenced by the Experiment journal: "
                    f"{binding[2]}"
                )
            if binding[2] in seen_results:
                raise ValueError("Profile supporting Gateway results must be unique")
            seen_results.add(binding[2])
        if not has_profile:
            raise ValueError("Profile evidence requires at least one profile result")
    candidate = request.get("final_candidate")
    blocker = request.get("blocker")
    if status == "candidate_ready":
        candidate_value = _exact_object(
            candidate,
            "Attempt report final_candidate",
            {"change_summary"},
        )
        _text(candidate_value.get("change_summary"), "Attempt report final_candidate summary")
        if blocker is not None:
            raise ValueError("candidate_ready cannot declare a blocker")
    elif status == "pivot":
        if candidate is not None:
            raise ValueError("pivot cannot nominate final_candidate")
        if blocker is not None:
            raise ValueError("pivot cannot declare a blocker")
    else:
        if candidate is not None:
            raise ValueError("blocked cannot nominate final_candidate")
        _text(blocker, "Attempt report blocker")
    _object_array(
        request.get("knowledge_used"),
        "Attempt report knowledge_used",
        {"record_id", "finding", "application"},
    )
    findings = request.get("findings")
    if not isinstance(findings, list) or not findings:
        raise ValueError("Attempt report findings must be a non-empty array")
    journal_experiment_ids = {experiment["experiment_id"] for experiment in experiments}
    for index, item in enumerate(findings):
        finding = _exact_object(
            item,
            f"Attempt report findings[{index}]",
            {
                "category",
                "observation",
                "root_cause",
                "resolution",
                "lesson",
                "supporting_experiment_ids",
            },
        )
        for field in ("category", "observation", "root_cause", "resolution", "lesson"):
            _text(finding[field], f"Attempt report findings[{index}].{field}")
        supporting_ids = _text_array(
            finding["supporting_experiment_ids"],
            f"Attempt report findings[{index}].supporting_experiment_ids",
            required=True,
        )
        if len(supporting_ids) > 32:
            raise ValueError("Attempt finding supports at most 32 Experiments")
        if len(set(supporting_ids)) != len(supporting_ids):
            raise ValueError("Attempt finding supporting Experiment IDs must be unique")
        unknown_experiment_ids = sorted(set(supporting_ids) - journal_experiment_ids)
        if unknown_experiment_ids:
            raise ValueError(
                "Attempt finding references Experiments outside this journal: "
                f"{unknown_experiment_ids}"
            )
    report = {
        "schema_version": 12,
        "attempt_id": context.attempt_id,
        **request,
        "experiments": experiments,
        "direction_events": direction_events,
    }
    _register_attempt_report(context, report)
    _atomic_json(context.report_path, report, exclusive=True)
    return report


def _register_attempt_report(context: RuntimeToolContext, report: dict[str, Any]) -> None:
    """Let Runtime seal the exact candidate and accept or refuse this nomination."""
    value: dict[str, Any] = {
        "schema_version": 2,
        "attempt_id": context.attempt_id,
        "operation": "attempt_report",
        "report": report,
        "candidate": _candidate(context.working_kernel),
    }
    value["idempotency_key"] = _idempotency_key("runtime-report", value)
    response = _post(
        context.gateway_url,
        context.gateway_capability,
        "/v1/runtime/queries",
        value,
    )
    result = response.get("result")
    if not isinstance(result, dict) or result.get("status") != "registered":
        raise ValueError("Attempt report registration returned an invalid response")


def _context(command: str) -> RuntimeToolContext:
    if os.environ.get("ATREX_CORE_PHASE") == "framework_baseline":
        return RuntimeLineageBootstrapContext.from_environment()
    return RuntimeAttemptContext.from_environment()


def _request_object(
    context: RuntimeToolContext,
    path: Path,
    command: str,
) -> dict[str, Any]:
    candidate = path if path.is_absolute() else context.workspace / path
    if candidate.is_symlink():
        raise ValueError(f"{command} request must not be a symbolic link")
    request_path = candidate.resolve()
    scratch = (context.workspace / "scratch").resolve()
    if not request_path.is_relative_to(scratch):
        raise ValueError(f"{command} request must be under scratch")
    return _read_object(
        request_path,
        f"{command} request",
        max_bytes=_MAX_REQUEST_BYTES,
    )


def _augment_agent_error(
    command: str,
    response: dict[str, Any],
    *,
    detail: str,
    context: RuntimeToolContext | None = None,
) -> dict[str, Any]:
    """Add local repair contracts without replacing more authoritative service guidance."""
    if command == "kernel-artifact-read" and isinstance(response.get("issues"), list):
        normalized_issues: list[Any] = []
        for issue in response["issues"]:
            if isinstance(issue, dict) and issue.get("path") == "file":
                normalized_issues.append({**issue, "path": "artifact_file"})
            else:
                normalized_issues.append(issue)
        response["issues"] = normalized_issues
    if "issues" not in response and detail:
        response["issues"] = [local_validation_issue(detail)]
    schema = tool_request_schema(
        command,
        allow_baseline=isinstance(context, RuntimeLineageBootstrapContext),
    )
    if schema is not None:
        response["request_schema"] = schema
    if "recovery" not in response:
        recovery = tool_recovery(command)
        if recovery is not None:
            response["recovery"] = recovery
    return response


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in _ATTEMPT_COMMANDS:
        command = commands.add_parser(name)
        command.add_argument("--request", required=True, type=Path)
    args = parser.parse_args(argv)
    context: RuntimeToolContext | None = None
    try:
        context = _context(args.command)
        request = _request_object(context, args.request, args.command)
        if args.command == "gateway-execute":
            result = gateway_execute(context, request)
        elif args.command in _RUNTIME_QUERY_COMMANDS:
            result = runtime_query(context, args.command, request)
        elif args.command == "wiki-query":
            result = wiki_query(context, request)
        elif args.command == "update-direction":
            result = update_direction(context, request)
        elif args.command == "list-directions":
            result = list_directions(context, request)
        elif args.command == "load-direction":
            result = load_direction(context, request)
        elif args.command == "record-experiment":
            result = record_experiment(context, request)
        elif args.command == "list-experiments":
            result = list_experiments(context, request)
        elif args.command == "load-experiment":
            result = load_experiment(context, request)
        elif args.command == "attempt-report":
            result = attempt_report(context, request)
    except RuntimeServiceError as error:
        response = dict(error.payload)
        response.update(
            {
                "status": "error",
                "command": args.command,
                "http_status": error.status_code,
            }
        )
        if error.status_code == 400:
            response = _augment_agent_error(
                args.command,
                response,
                detail=str(response.get("detail", "invalid request")),
                context=context,
            )
        print(json.dumps(response, ensure_ascii=False, allow_nan=False, sort_keys=True))
        return 2
    except (OSError, RuntimeError, ValueError) as error:
        invalid_request = isinstance(error, (FileExistsError, FileNotFoundError, ValueError))
        response = {
            "status": "error",
            "error": "invalid_request" if invalid_request else "tool_failed",
            "command": args.command,
            "detail": str(error),
        }
        if invalid_request:
            response = _augment_agent_error(
                args.command,
                response,
                detail=str(error),
                context=context,
            )
        print(json.dumps(response, ensure_ascii=False, allow_nan=False, sort_keys=True))
        return 2
    print(json.dumps(result, ensure_ascii=False, allow_nan=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
