"""Supervisor-private V0 measurement facts.

V0 is source identity plus evaluator evidence, not Agent-authored memory.  Keep
its measurement outside the candidate repository so recording a baseline never
creates a metadata-only Git commit or exposes full per-shape evidence to the
Agent.  Legacy campaigns remain readable and are copied into private state on
first access.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Mapping

from aka.legacy.task.persistence import durable_write_json
from aka.light.runtime.paths import supervisor_campaign_root


def baseline_root(workspace: Path) -> Path:
    return supervisor_campaign_root(workspace) / "baseline"


def baseline_record_path(workspace: Path) -> Path:
    return baseline_root(workspace) / "v0.json"


def baseline_failure_path(workspace: Path) -> Path:
    return baseline_root(workspace) / "v0.failed.json"


def _legacy_paths(workspace: Path) -> tuple[Path, ...]:
    return (
        workspace / ".atrex_versions" / "v0.json",
        workspace / "memory" / "v0.json",
    )


def _read_object(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def read_baseline_record(workspace: Path, *, migrate_legacy: bool = True) -> dict | None:
    """Read private V0 facts, importing a legacy workspace record when needed."""
    private = baseline_record_path(workspace)
    record = _read_object(private)
    if record is not None:
        return record
    for legacy in _legacy_paths(workspace):
        record = _read_object(legacy)
        if record is None:
            continue
        if migrate_legacy:
            write_baseline_record(workspace, record)
        return record
    return None


def write_baseline_record(workspace: Path, record: Mapping[str, object]) -> Path:
    value = dict(record)
    if value.get("version") != "v0":
        raise ValueError("V0 baseline record identity does not match")
    path = baseline_record_path(workspace)
    durable_write_json(path, value)
    baseline_failure_path(workspace).unlink(missing_ok=True)
    return path


def write_baseline_failure(workspace: Path, record: Mapping[str, object]) -> Path:
    value = dict(record)
    if value.get("version") != "v0":
        raise ValueError("V0 baseline failure identity does not match")
    path = baseline_failure_path(workspace)
    durable_write_json(path, value)
    baseline_record_path(workspace).unlink(missing_ok=True)
    return path


def _baseline_result(record: Mapping[str, object]) -> dict[str, object]:
    """Reconstruct the bounded evaluator receipt sealed into the private V0 Record."""
    performance = record.get("performance")
    performance = performance if isinstance(performance, Mapping) else {}
    correctness = record.get("correctness")
    correctness = correctness if isinstance(correctness, Mapping) else {}
    passed = str(correctness.get("status", "")).upper() == "PASS"
    result: dict[str, object] = {
        "all_pass": passed,
        "correct": passed,
        "mode": "full",
        "latency_us_geomean": performance.get("latency_us_geomean"),
        "latency_us_arith_mean": performance.get("latency_us_arith_mean"),
        "latency_us_by_shape": performance.get("latency_us_by_shape"),
        "performance_score": performance.get("performance_score"),
        "performance_objective": performance.get("performance_objective"),
        "speedup_vs_ref_mean": performance.get("speedup_vs_ref_mean"),
        "speedup_vs_ref_geomean": performance.get("speedup_vs_ref_geomean"),
        "max_abs_err": correctness.get("max_abs_err"),
        "max_rel_err": correctness.get("max_rel_err"),
    }
    return {key: value for key, value in result.items() if value is not None}


def ensure_baseline_gateway_record(workspace: Path) -> str | None:
    """Return a real private ``gateway-`` identity for the measured V0.

    V0 runs before the Agent HTTP Runtime exists, so its Agate jobs are not
    naturally wrapped by ``MeasurementStore``.  Seal the already-completed V0
    receipt once the source commit is known.  The Record remains Supervisor
    private: ``PROGRESS.md`` exposes only its stable identity and bounded
    summary, never backend ``ev_`` IDs or the full per-Shape payload.
    """
    record = read_baseline_record(workspace)
    if not isinstance(record, dict):
        return None
    commit = record.get("git_commit_hash")
    if not isinstance(commit, str) or not commit:
        return None

    from aka.light.experiment.store import DuplicateTask, MeasurementStore, RECORD_ID
    from aka.light.workspace.sources import Artifact, from_git

    store = MeasurementStore(supervisor_campaign_root(workspace) / "measurements")
    existing = record.get("gateway_record_id")
    if isinstance(existing, str) and RECORD_ID.fullmatch(existing):
        try:
            saved = store.read(existing)
        except (FileNotFoundError, OSError, ValueError, KeyError, TypeError):
            pass
        else:
            if saved.get("request", {}).get("visibility") == "supervisor_private_v0":
                return existing

    source = from_git(workspace, commit)
    artifact = Artifact(source)
    identity = store.kernel(source, commit)
    options: dict[str, object] = {
        "command": ["python", "test_kernel.py", "--version", "v0", "--no-memory"],
        "evaluation_mode": "full",
        "evaluation_policy": {"mode": "full"},
    }
    if artifact.layout.archived:
        options["source_snapshots"] = {"candidate": artifact.digest}
        inputs = {
            name: hashlib.sha256(contents).hexdigest()
            for name, contents in artifact.files.items()
        }
    else:
        inputs = {artifact.layout.python_entrypoint: artifact.digest}
    request = {
        "operation": "evaluate",
        "options": options,
        "inputs": inputs,
        "kernel_ids": {"candidate": commit},
        "visibility": "supervisor_private_v0",
    }
    result = _baseline_result(record)
    response = {
        "exit_code": 0 if result.get("all_pass") is True else 1,
        "stdout": "[test_kernel] RESULT_JSON=" + json.dumps(
            result, ensure_ascii=False, allow_nan=False
        ) + "\n",
        "stderr": (
            "[supervisor] backend_job_ids="
            + ",".join(
                item
                for item in record.get("backend_job_ids", [])
                if isinstance(item, str)
            )
            + "\n"
            if isinstance(record.get("backend_job_ids"), list)
            and record.get("backend_job_ids")
            else ""
        ),
    }
    try:
        with store.reserve(request, {"candidate": identity}) as task:
            gateway_record_id = task.finish(response, cacheable=True)[
                "gateway_record_id"
            ]
    except DuplicateTask as duplicate:
        gateway_record_id = duplicate.record_id
    if not isinstance(gateway_record_id, str):
        raise RuntimeError("V0 Gateway Record did not produce a stable identity")
    updated = dict(record)
    updated["gateway_record_id"] = gateway_record_id
    write_baseline_record(workspace, updated)
    return gateway_record_id
