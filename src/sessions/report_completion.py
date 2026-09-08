"""Require Runtime acceptance before an optimization or bootstrap session completes."""

from __future__ import annotations

import json
import math
import os
import queue
import re
import stat
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from uuid import uuid4

import runtime_tools
from contexts.attempt import RuntimeAttemptContext
from contexts.lineage_bootstrap import RuntimeLineageBootstrapContext

ReportContext = RuntimeAttemptContext | RuntimeLineageBootstrapContext
_RUNTIME_TOOL = "agent/optimizer/src/runtime_tools.py"
_MAX_LOCAL_REPORT_BYTES = 4 * 1024 * 1024


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Report acceptance check exceeded the remaining session deadline")
    return remaining


def _post_once(context: ReportContext, timeout: float) -> dict[str, Any]:
    """Use one HTTP request, with the shared Runtime error type and response limits."""
    payload = {
        "schema_version": 2,
        "attempt_id": context.attempt_id,
        "operation": "attempt_report_status",
        "idempotency_key": f"core-report-status-{uuid4().hex}",
    }
    request = urllib.request.Request(
        context.gateway_url.rstrip("/") + "/v1/runtime/queries",
        data=json.dumps(payload, allow_nan=False).encode(),
        method="POST",
        headers={
            "authorization": f"Bearer {context.gateway_capability}",
            "content-type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(runtime_tools._MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as error:
        body = error.read(runtime_tools._MAX_ERROR_RESPONSE_BYTES + 1)
        error_payload: dict[str, Any] = {
            "error": "runtime_service_error",
            "detail": f"Runtime report status query returned HTTP {error.code}",
        }
        if len(body) <= runtime_tools._MAX_ERROR_RESPONSE_BYTES:
            try:
                decoded = json.loads(body)
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass
            else:
                if isinstance(decoded, dict):
                    error_payload = decoded
        raise runtime_tools.RuntimeServiceError(error.code, error_payload) from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"Runtime service is unavailable: {error.reason}") from error
    if len(body) > runtime_tools._MAX_RESPONSE_BYTES:
        raise RuntimeError("Runtime service response exceeds the Core byte limit")
    value = json.loads(body)
    if not isinstance(value, dict):
        raise ValueError("Runtime report status response must be an object")
    return value


def _status_response(context: ReportContext, deadline: float) -> dict[str, Any]:
    # urllib's socket timeout is not a total DNS/connect/read deadline. Keep the
    # caller bounded even for a stalled resolver or a slowly streaming response.
    # The daemon performs this read-only query only: it never touches report files.
    outcomes: queue.Queue[dict[str, Any] | BaseException] = queue.Queue(maxsize=1)

    def query() -> None:
        try:
            result = _post_once(context, _remaining(deadline))
        except BaseException as error:
            outcomes.put(error)
        else:
            outcomes.put(result)

    threading.Thread(target=query, name="report-acceptance-status", daemon=True).start()
    try:
        outcome = outcomes.get(timeout=_remaining(deadline))
    except queue.Empty as error:
        raise TimeoutError(
            "Report acceptance check exceeded the remaining session deadline"
        ) from error
    _remaining(deadline)
    if isinstance(outcome, BaseException):
        raise outcome
    return outcome


def _report_path(context: ReportContext) -> Path:
    """Validate the lexical path before any local report read, write, or move."""
    workspace_info = context.workspace.lstat()
    if stat.S_ISLNK(workspace_info.st_mode) or not stat.S_ISDIR(workspace_info.st_mode):
        raise ValueError("Report workspace must be a real directory")
    try:
        relative = context.report_path.relative_to(context.workspace)
    except ValueError as error:
        raise ValueError("Terminal report must be under workspace scratch/") from error
    path, _ = runtime_tools._scratch_output(context, relative.as_posix(), label="Terminal report")
    return path


def _accepted_report(context: ReportContext, result: Mapping[str, Any]) -> dict[str, Any]:
    report = result.get("report")
    if not isinstance(report, dict):
        raise ValueError("Runtime accepted report must be an object")
    if report.get("attempt_id") != context.attempt_id:
        raise ValueError("Runtime accepted report belongs to a different session")
    allowed = {"candidate_ready", "blocked"}
    if isinstance(context, RuntimeAttemptContext):
        allowed.add("pivot")
    status = report.get("status")
    if not isinstance(status, str) or status not in allowed:
        raise ValueError("Runtime accepted report has an invalid terminal status")
    digest = result.get("report_artifact_digest")
    if not isinstance(digest, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None:
        raise ValueError("Runtime accepted report is missing a valid artifact digest")
    return report


def _restore_report(path: Path, report: dict[str, Any], deadline: float) -> None:
    if path.exists():
        try:
            existing = runtime_tools._read_object(
                path, "Local terminal report", max_bytes=_MAX_LOCAL_REPORT_BYTES
            )
        except ValueError:
            existing = None
        if existing == report:
            return
    _remaining(deadline)
    runtime_tools._atomic_json(path, report)


def _backup_report(context: ReportContext, path: Path, deadline: float) -> str | None:
    if not path.exists():
        return None
    _remaining(deadline)
    # Reserve a unique destination before moving, preserving the original report bytes.
    descriptor, name = tempfile.mkstemp(
        prefix="report-completion-backup-", suffix=".json", dir=context.workspace / "scratch"
    )
    os.close(descriptor)
    backup = Path(name)
    try:
        _remaining(deadline)
        os.replace(path, backup)
    except BaseException:
        backup.unlink()
        raise
    return backup.relative_to(context.workspace).as_posix()


def _public_context(context: ReportContext) -> str:
    task = context.manifest.get("context", context.manifest)
    if not isinstance(task, Mapping):
        task = {}
    public = {
        "dsl": context.manifest.get("dsl"),
        "operator": task.get("operator"),
        "hardware_target": task.get("hardware_target"),
    }
    return json.dumps(public, ensure_ascii=False, sort_keys=True)


def _trace_hint(context: ReportContext) -> str:
    if context.session_trace_path is not None:
        try:
            relative = context.session_trace_path.relative_to(context.workspace)
        except ValueError:
            relative = None
        if (
            relative is not None
            and relative.parts
            and relative.parts[0] in {"sessions", "scratch"}
            and ".." not in relative.parts
        ):
            name = (relative / "conversation.jsonl").as_posix()
            if context.attempt_id not in name:
                return f"Read the prior session trace at `{name}` (including its continuations)."
    return "Read the prior session trace available under `sessions/`, including continuations."


def _missing_prompt(context: ReportContext, backup: str | None) -> str:
    empty_status = (
        "blocked" if isinstance(context, RuntimeLineageBootstrapContext) else "blocked or pivot"
    )
    fallback = (
        "If existing evidence cannot justify candidate_ready, report blocked honestly."
        if isinstance(context, RuntimeLineageBootstrapContext)
        else (
            "If existing evidence cannot justify candidate_ready, report pivot or blocked honestly."
        )
    )
    preserved = (
        f"An unaccepted local terminal report was preserved at `{backup}`. "
        "Read it as a draft only; its existence is not a successful Runtime receipt.\n"
        if backup is not None
        else ""
    )
    return (
        "## Complete the terminal report submission\n\n"
        "Runtime has not accepted a terminal report for this session. "
        "This continuation is report-only. Do not start new optimization, change the Candidate, "
        "or launch evaluate, profile, dev, probes, or other new measurement/model work.\n"
        f"Public task context: {_public_context(context)}\n"
        f"{_trace_hint(context)}\n"
        f"{preserved}"
        "Read the existing Runtime Journal using list-directions, load-direction, "
        "list-experiments, and load-experiment as needed. Reuse already measured results "
        "and their actual identifiers; "
        "do not invent trials, measurements, successful checks, or supporting evidence. "
        "Complete only the Journal bookkeeping needed to describe the work already done.\n"
        "If the exact final Kernel reuses matching successful historical full-Evaluate evidence, "
        "record an adopt decision with the real before/after Trials and let Runtime validate it. "
        "Do not wait for authoritative ABBA: it runs after Report handoff and creates no Agent "
        "Trial. Agent ABBA cannot replace ordinary full-Evaluate evidence.\n"
        f"{fallback} Explain any remaining uncertainty or blocker.\n"
        f"A {empty_status} report may have zero Experiments and empty findings; "
        "do not invent either. Close any in_progress Direction with block or defer first.\n"
        f"Submit the terminal report through the real `python {_RUNTIME_TOOL} attempt-report "
        "--request scratch/report-completion-request.json` tool. "
        "Follow the existing attempt-report request schema; the tool supplies protocol metadata. "
        "Do not merely write a local "
        "terminal report or describe a report in your final response. Inspect the tool response "
        "and finish only after a successful Runtime receipt (status: published). "
        "If submission fails, repair the report or its existing evidence references and resubmit "
        "without starting new optimization.\n"
    )


def report_completion_prompt(
    context: ReportContext, remaining_timeout_seconds: float
) -> str | None:
    """Check Runtime acceptance within the remaining logical-session deadline."""
    if not math.isfinite(remaining_timeout_seconds) or remaining_timeout_seconds <= 0:
        raise ValueError("Report completion requires a positive finite remaining timeout")
    deadline = time.monotonic() + remaining_timeout_seconds
    response = _status_response(context, deadline)
    result = response.get("result")
    if not isinstance(result, dict):
        raise ValueError("Runtime report status result must be an object")
    status = result.get("status")
    if status == "accepted":
        report = _accepted_report(context, result)
        _remaining(deadline)
        _restore_report(_report_path(context), report, deadline)
        _remaining(deadline)
        return None
    if status == "missing":
        _remaining(deadline)
        backup = _backup_report(context, _report_path(context), deadline)
        prompt = _missing_prompt(context, backup)
        _remaining(deadline)
        return prompt
    raise ValueError("Runtime returned an invalid report acceptance status")
