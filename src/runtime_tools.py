#!/usr/bin/env python3
"""Framework-neutral CLI bindings from a Core Agent to trusted Runtime services."""

from __future__ import annotations

import argparse
import base64
import errno
import hashlib
import json
import os
import stat
import tempfile
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from contexts.attempt import RuntimeAttemptContext
from contexts.lineage_bootstrap import RuntimeLineageBootstrapContext

_CANDIDATE_OPERATIONS = {
    "evaluate",
    "submit",
    "profile",
    "dev",
    "check",
    "sol",
    "disassemble",
}
_RESERVED_REQUEST_FIELDS = {"schema_version", "attempt_id", "candidate"}
_EXPERIMENT_FIELDS = {
    "name",
    "hypothesis",
    "change",
    "evidence",
    "result",
    "decision",
}
_REPORT_FIELDS = {
    "status",
    "hypothesis",
    "bottleneck",
    "plan",
    "change_summary",
    "profile_evidence",
    "evaluation_evidence",
    "result_interpretation",
    "decision",
    "research_sources",
    "lessons",
    "next_directions",
}
_BOOTSTRAP_REPORT_FIELDS = {
    "status",
    "approach",
    "change_summary",
    "correctness_evidence",
    "latency_us",
    "candidate_artifact_digest",
    "gateway_result_digest",
    "research_sources",
    "lessons",
    "next_directions",
    "blocker",
}
RuntimeToolContext = RuntimeAttemptContext | RuntimeLineageBootstrapContext

_MAX_REQUEST_BYTES = 256 * 1024
_MAX_JOURNAL_BYTES = 2 * 1024 * 1024
_MAX_CANDIDATE_FILES = 4096
_MAX_CANDIDATE_BYTES = 64 * 1024 * 1024
_MAX_RESPONSE_BYTES = 16 * 1024 * 1024


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
    try:
        with urllib.request.urlopen(request, timeout=600) as response:
            body = response.read(_MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:4000]
        raise RuntimeError(f"Runtime service rejected request ({error.code}): {detail}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"Runtime service is unavailable: {error.reason}") from error
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


def gateway_execute(context: RuntimeToolContext, request: dict[str, Any]) -> dict[str, Any]:
    overlap = _RESERVED_REQUEST_FIELDS.intersection(request)
    if overlap:
        raise ValueError(f"Gateway request sets Runtime-owned fields: {sorted(overlap)}")
    operation = request.get("operation")
    if not isinstance(operation, str) or not operation:
        raise ValueError("Gateway request requires operation")
    value = {"schema_version": 2, "attempt_id": context.attempt_id, **request}
    if operation in _CANDIDATE_OPERATIONS:
        value["candidate"] = _candidate(context.working_kernel)
    value.setdefault("idempotency_key", _idempotency_key("gateway", value))
    return _post(context.gateway_url, context.gateway_capability, "/v1/operations", value)


def wiki_query(context: RuntimeToolContext, request: dict[str, Any]) -> dict[str, Any]:
    if context.wiki_url is None or context.wiki_capability is None:
        raise RuntimeError("GPU Wiki capability is unavailable for this Attempt")
    unknown = set(request) - {"query", "idempotency_key"}
    if unknown:
        raise ValueError(f"unknown Wiki request fields: {sorted(unknown)}")
    query = request.get("query")
    if not isinstance(query, str) or not query.strip():
        raise ValueError("Wiki request requires a non-empty query")
    value = {"schema_version": 1, "attempt_id": context.attempt_id, **request}
    value.setdefault("idempotency_key", _idempotency_key("wiki", value))
    return _agent_knowledge(
        _post(context.wiki_url, context.wiki_capability, "/v1/wiki/query", value)
    )


def wiki_read(context: RuntimeToolContext, request: dict[str, Any]) -> dict[str, Any]:
    if context.wiki_url is None or context.wiki_capability is None:
        raise RuntimeError("GPU Wiki capability is unavailable for this Attempt")
    unknown = set(request) - {"source_ref", "idempotency_key"}
    if unknown:
        raise ValueError(f"unknown Wiki read fields: {sorted(unknown)}")
    source_ref = request.get("source_ref")
    if not isinstance(source_ref, str) or not source_ref.strip():
        raise ValueError("Wiki read requires a non-empty source_ref")
    value = {"schema_version": 1, "attempt_id": context.attempt_id, **request}
    value.setdefault("idempotency_key", _idempotency_key("wiki-read", value))
    return _agent_knowledge(
        _post(context.wiki_url, context.wiki_capability, "/v1/wiki/read", value)
    )


def _agent_knowledge(response: dict[str, Any]) -> dict[str, Any]:
    """Expose knowledge content while retaining audit identities inside Runtime."""
    content = response.get("content")
    if not isinstance(content, dict):
        raise RuntimeError("Runtime Wiki response has no Agent-readable content object")
    return content


def _journal_path(context: RuntimeAttemptContext) -> Path:
    return context.workspace / "scratch/experiments.json"


def record_experiment(context: RuntimeAttemptContext, request: dict[str, Any]) -> dict[str, Any]:
    if set(request) != _EXPERIMENT_FIELDS:
        raise ValueError(f"Experiment fields must be exactly {sorted(_EXPERIMENT_FIELDS)}")
    for key in _EXPERIMENT_FIELDS - {"decision"}:
        if not isinstance(request[key], str) or not request[key].strip():
            raise ValueError(f"Experiment {key} must be non-empty text")
    if request["decision"] not in {"continue", "revert", "pivot"}:
        raise ValueError("Experiment decision is invalid")
    path = _journal_path(context)
    journal = (
        _read_object(path, "Experiment journal", max_bytes=_MAX_JOURNAL_BYTES)
        if path.exists()
        else {
            "schema_version": 1,
            "attempt_id": context.attempt_id,
            "experiments": [],
        }
    )
    if journal.get("schema_version") != 1 or journal.get("attempt_id") != context.attempt_id:
        raise ValueError("Experiment journal belongs to a different protocol or Attempt")
    experiments = journal.get("experiments")
    if not isinstance(experiments, list):
        raise ValueError("Experiment journal is malformed")
    experiment = {
        "sequence": len(experiments) + 1,
        "recorded_at": datetime.now(UTC).isoformat(),
        **request,
    }
    experiments.append(experiment)
    _atomic_json(path, journal)
    return experiment


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


def _validated_experiments(context: RuntimeAttemptContext) -> list[dict[str, Any]]:
    journal = _read_object(
        _journal_path(context), "Experiment journal", max_bytes=_MAX_JOURNAL_BYTES
    )
    experiments = journal.get("experiments")
    if journal.get("schema_version") != 1 or journal.get("attempt_id") != context.attempt_id:
        raise ValueError("Experiment journal belongs to a different protocol or Attempt")
    if not isinstance(experiments, list) or not experiments:
        raise ValueError("Attempt report requires a non-empty matching Experiment journal")
    expected_fields = _EXPERIMENT_FIELDS | {"sequence", "recorded_at"}
    for sequence, experiment in enumerate(experiments, start=1):
        if not isinstance(experiment, dict) or set(experiment) != expected_fields:
            raise ValueError("Experiment journal contains a malformed entry")
        if experiment.get("sequence") != sequence:
            raise ValueError("Experiment journal sequence must be contiguous")
        _text(experiment.get("recorded_at"), "Experiment recorded_at")
        try:
            datetime.fromisoformat(str(experiment["recorded_at"]).replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("Experiment recorded_at must be ISO-8601") from error
        for field in _EXPERIMENT_FIELDS - {"decision"}:
            _text(experiment.get(field), f"Experiment {field}")
        if experiment.get("decision") not in {"continue", "revert", "pivot"}:
            raise ValueError("Experiment decision is invalid")
    return experiments


def attempt_report(context: RuntimeAttemptContext, request: dict[str, Any]) -> dict[str, Any]:
    if set(request) != _REPORT_FIELDS:
        raise ValueError(f"Attempt report fields must be exactly {sorted(_REPORT_FIELDS)}")
    experiments = _validated_experiments(context)
    expected_decision = {
        "candidate_ready": "keep",
        "pivot": "pivot",
        "blocked": "blocked",
    }
    status = request.get("status")
    if status not in expected_decision or request.get("decision") != expected_decision[status]:
        raise ValueError("Attempt report status and decision are inconsistent")
    for field in (
        "hypothesis",
        "bottleneck",
        "change_summary",
        "profile_evidence",
        "evaluation_evidence",
        "result_interpretation",
    ):
        _text(request.get(field), f"Attempt report {field}")
    _text_array(request.get("plan"), "Attempt report plan", required=True)
    _text_array(request.get("research_sources"), "Attempt report research_sources")
    _text_array(request.get("lessons"), "Attempt report lessons", required=True)
    _text_array(request.get("next_directions"), "Attempt report next_directions")
    report = {
        "schema_version": 2,
        "attempt_id": context.attempt_id,
        **request,
        "experiments": experiments,
    }
    _atomic_json(context.report_path, report, exclusive=True)
    return report


def lineage_bootstrap_report(
    context: RuntimeLineageBootstrapContext,
    request: dict[str, Any],
) -> dict[str, Any]:
    if set(request) != _BOOTSTRAP_REPORT_FIELDS:
        raise ValueError(
            f"lineage bootstrap report fields must be exactly {sorted(_BOOTSTRAP_REPORT_FIELDS)}"
        )
    status = request.get("status")
    if status not in {"baseline_ready", "blocked"}:
        raise ValueError("lineage bootstrap status is invalid")
    for field in ("approach", "change_summary", "correctness_evidence", "lessons"):
        if not isinstance(request[field], str) or not request[field].strip():
            raise ValueError(f"lineage bootstrap {field} must be non-empty text")
    for field in ("research_sources", "next_directions"):
        value = request[field]
        if not isinstance(value, list) or any(
            not isinstance(item, str) or not item.strip() for item in value
        ):
            raise ValueError(f"lineage bootstrap {field} must be a text array")
    if len(request["next_directions"]) > 3:
        raise ValueError("lineage bootstrap can publish at most three next directions")
    if status == "baseline_ready":
        latency = request["latency_us"]
        if isinstance(latency, bool) or not isinstance(latency, (int, float)) or latency <= 0:
            raise ValueError("a ready lineage baseline requires positive latency_us")
        for field in ("candidate_artifact_digest", "gateway_result_digest"):
            if not isinstance(request[field], str) or not request[field].strip():
                raise ValueError(f"a ready lineage baseline requires {field}")
        if request["blocker"] is not None:
            raise ValueError("a ready lineage baseline cannot declare a blocker")
    else:
        if not isinstance(request["blocker"], str) or not request["blocker"].strip():
            raise ValueError("a blocked lineage baseline requires a blocker")
        if any(
            request[field] is not None
            for field in ("latency_us", "candidate_artifact_digest", "gateway_result_digest")
        ):
            raise ValueError("a blocked lineage baseline cannot claim a candidate result")
    report = {
        "schema_version": 1,
        "bootstrap_attempt_id": context.attempt_id,
        **request,
    }
    _atomic_json(context.report_path, report, exclusive=True)
    return report


def _context(command: str) -> RuntimeToolContext:
    if command == "lineage-bootstrap-report" or os.environ.get("ATREX_CORE_PHASE") == (
        "framework_baseline"
    ):
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in (
        "gateway-execute",
        "wiki-query",
        "wiki-read",
        "record-experiment",
        "attempt-report",
        "lineage-bootstrap-report",
    ):
        command = commands.add_parser(name)
        command.add_argument("--request", required=True, type=Path)
    args = parser.parse_args(argv)
    context = _context(args.command)
    request = _request_object(context, args.request, args.command)
    if args.command == "gateway-execute":
        result = gateway_execute(context, request)
    elif args.command == "wiki-query":
        result = wiki_query(context, request)
    elif args.command == "wiki-read":
        result = wiki_read(context, request)
    elif args.command == "record-experiment":
        if not isinstance(context, RuntimeAttemptContext):
            raise ValueError("experiment journal is available only to optimization Attempts")
        result = record_experiment(context, request)
    elif args.command == "attempt-report":
        if not isinstance(context, RuntimeAttemptContext):
            raise ValueError("Attempt report is available only to optimization Attempts")
        result = attempt_report(context, request)
    else:
        if not isinstance(context, RuntimeLineageBootstrapContext):
            raise ValueError("lineage bootstrap report requires a framework baseline session")
        result = lineage_bootstrap_report(context, request)
    print(json.dumps(result, ensure_ascii=False, allow_nan=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
