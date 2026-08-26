"""Agent-facing request schemas and repair hints for local Core tools."""

from __future__ import annotations

import re
from typing import Any


def _text() -> dict[str, Any]:
    return {"type": "string", "minLength": 1}


def _identifier(prefix: str) -> dict[str, Any]:
    return {"type": "string", "pattern": rf"^{prefix}[0-9a-f]{{32}}$"}


def _digest() -> dict[str, Any]:
    return {"type": "string", "pattern": r"^sha256:[0-9a-f]{64}$"}


def _object(
    properties: dict[str, Any],
    *,
    required: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties if required is None else required),
        "additionalProperties": False,
    }


def _subject() -> dict[str, Any]:
    return _object(
        {
            "kernel_artifact_digest": _digest(),
            "kernel_trial_id": _identifier("gtrial_"),
            "gateway_result_digests": {
                "type": "array",
                "minItems": 1,
                "maxItems": 32,
                "uniqueItems": True,
                "items": _digest(),
            },
        }
    )


def _direction_schema() -> dict[str, Any]:
    proposal = _object(
        {
            "action": {"const": "propose"},
            "name": _text(),
            "hypothesis": _text(),
            "rationale": _text(),
            "plan": {"type": "array", "minItems": 1, "items": _text()},
            "success_criteria": _text(),
            "stop_conditions": _text(),
        }
    )
    update = _object(
        {
            "action": {
                "enum": ["start", "complete", "abandon", "block", "defer"]
            },
            "direction_id": _identifier("direction_"),
            "analysis": _text(),
        }
    )
    return {"oneOf": [proposal, update]}


def _experiment_schema(*, allow_baseline: bool) -> dict[str, Any]:
    nullable_subject = {"oneOf": [_subject(), {"type": "null"}]}
    actions = ["keep_after", "restore_before", "abandon_direction"]
    if allow_baseline:
        actions.append("baseline")
    return _object(
        {
            "direction_id": _identifier("direction_"),
            "name": _text(),
            "hypothesis": _text(),
            "change": _text(),
            "before": nullable_subject,
            "after": nullable_subject,
            "evidence": _text(),
            "analysis": _text(),
            "action": {"enum": actions},
        }
    )


def _attempt_report_schema(*, allow_baseline: bool) -> dict[str, Any]:
    result_binding = _object(
        {
            "operation": {"const": "profile"},
            "kernel_artifact_digest": _digest(),
            "kernel_trial_id": _identifier("gtrial_"),
            "gateway_result_digest": _digest(),
        }
    )
    profile = _object(
        {
            "tool_used": _text(),
            "profiler": _text(),
            "profile_level": _text(),
            "bottleneck_type": _text(),
            "evidence_summary": _text(),
            "evidence_chain": _text(),
            "supporting_results": {
                "type": "array",
                "minItems": 1,
                "maxItems": 32,
                "items": result_binding,
            },
        }
    )
    return _object(
        {
            "status": {
                "enum": (
                    ["candidate_ready", "blocked"]
                    if allow_baseline
                    else ["candidate_ready", "pivot", "blocked"]
                )
            },
            "hypothesis": _text(),
            "diagnosis": _object({"bottleneck": _text(), "evidence": _text()}),
            "approach": _object(
                {
                    "summary": _text(),
                    "steps": {"type": "array", "minItems": 1, "items": _text()},
                    "expected_impact": _text(),
                    "risks": {"type": "array", "items": _text()},
                }
            ),
            "final_candidate": {
                "oneOf": [_object({"change_summary": _text()}), {"type": "null"}]
            },
            "evidence_summary": _object(
                {"correctness": _text(), "performance": _text()}
            ),
            "profile_evidence": {"oneOf": [profile, {"type": "null"}]},
            "analysis": _text(),
            "knowledge_used": {
                "type": "array",
                "items": _object(
                    {"record_id": _text(), "finding": _text(), "application": _text()}
                ),
            },
            "findings": {
                "type": "array",
                "minItems": 1,
                "items": _object(
                    {
                        "category": _text(),
                        "observation": _text(),
                        "root_cause": _text(),
                        "resolution": _text(),
                        "lesson": _text(),
                        "supporting_experiment_ids": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 32,
                            "uniqueItems": True,
                            "items": _identifier("experiment_"),
                        },
                    }
                ),
            },
            "blocker": {"oneOf": [_text(), {"type": "null"}]},
        }
    )


_SCRATCH_FILE = _object({"file": {"type": "string", "pattern": r"^scratch/.+"}})
_SCHEMAS: dict[str, dict[str, Any]] = {
    "kernel-trial-show": _object({"kernel_trial_id": _identifier("gtrial_")}),
    "kernel-artifact-read": _object(
        {
            "kernel_artifact_digest": _digest(),
            "artifact_file": _text(),
            "file": {"type": "string", "pattern": r"^scratch/.+"},
        },
        required=("kernel_artifact_digest", "file"),
    ),
    "gateway-result-read": _object({"gateway_result_digest": _digest()}),
    "wiki-query": _object({"query": _text()}),
    "update-direction": _direction_schema(),
    "list-directions": _SCRATCH_FILE,
    "load-direction": _object({"direction_id": _identifier("direction_")}),
    "list-experiments": _SCRATCH_FILE,
    "load-experiment": _object({"experiment_id": _identifier("experiment_")}),
}


def tool_request_schema(
    command: str,
    *,
    allow_baseline: bool = False,
) -> dict[str, Any] | None:
    """Return the exact local Agent request contract when Core owns validation."""
    if command == "record-experiment":
        return _experiment_schema(allow_baseline=allow_baseline)
    if command == "attempt-report":
        return _attempt_report_schema(allow_baseline=allow_baseline)
    return _SCHEMAS.get(command)


_RECOVERY: dict[str, list[dict[str, Any]]] = {
    "kernel-trial-show": [
        {
            "instruction": (
                "Use a kernel_trial_id returned by a visible Gateway operation or Experiment"
            )
        }
    ],
    "kernel-artifact-read": [
        {
            "instruction": (
                "Use a kernel_artifact_digest returned by kernel-trial-show or load-experiment"
            )
        }
    ],
    "gateway-result-read": [
        {
            "instruction": (
                "Use a gateway_result_digest returned by a visible Gateway operation or Experiment"
            )
        }
    ],
    "update-direction": [
        {
            "tool": "list-directions",
            "request": {"file": "scratch/directions-index.json"},
        },
        {
            "instruction": (
                "Record at least one Experiment before completing or abandoning a Direction"
            )
        },
        {
            "instruction": (
                "When direction_concurrency_conflict is returned, continue the existing "
                "in-progress Direction or close it with complete, abandon, defer, or block. "
                "Retry start only after no other Direction is in progress"
            )
        },
        {
            "instruction": (
                "When direction_advancement_limit_exceeded is returned, the requested Direction "
                "was not started. Keep it proposed or deferred for a future Attempt; do not retry "
                "start in the current Attempt"
            )
        },
    ],
    "load-direction": [
        {
            "tool": "list-directions",
            "request": {"file": "scratch/directions-index.json"},
        }
    ],
    "record-experiment": [
        {
            "tool": "list-directions",
            "request": {"file": "scratch/directions-index.json"},
        },
        {"instruction": "Bind the Experiment to a visible Direction whose status is in_progress"},
    ],
    "load-experiment": [
        {
            "tool": "list-experiments",
            "request": {"file": "scratch/experiments-index.json"},
        }
    ],
    "attempt-report": [
        {
            "tool": "list-directions",
            "request": {"file": "scratch/directions-index.json"},
        },
        {
            "tool": "list-experiments",
            "request": {"file": "scratch/experiments-index.json"},
        },
        {
            "instruction": (
                "Read both indexes and close every in_progress Direction with update-direction "
                "before retrying attempt-report. Use defer or block when no Experiment exists; "
                "complete or abandon requires a supporting Experiment"
            )
        },
        {
            "instruction": (
                "A failed attempt-report publishes nothing. Correct the request using issues and "
                "request_schema, then retry; never retry after a successful response"
            )
        },
    ],
}


def tool_recovery(command: str) -> list[dict[str, Any]] | None:
    """Return bounded, visibility-safe next actions for repairing one local request."""
    return _RECOVERY.get(command)


_PATH_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"profile_evidence\.supporting_results"), "profile_evidence.supporting_results"),
    (re.compile(r"supporting_experiment_ids", re.I), "findings.supporting_experiment_ids"),
    (re.compile(r"final_candidate", re.I), "final_candidate"),
    (re.compile(r"cannot leave any Direction in progress", re.I), "direction_events"),
    (re.compile(r"Only one Direction may be in progress", re.I), "direction_id"),
    (re.compile(r"Direction advancement limit exceeded", re.I), "direction_id"),
    (re.compile(r"Experiment Direction", re.I), "direction_id"),
    (re.compile(r"direction[_ ]id", re.I), "direction_id"),
    (re.compile(r"experiment[_ ]id", re.I), "experiment_id"),
    (re.compile(r"kernel[_ ]trial[_ ]id", re.I), "kernel_trial_id"),
    (re.compile(r"kernel[_ ]artifact", re.I), "kernel_artifact_digest"),
    (re.compile(r"gateway[_ ]result", re.I), "gateway_result_digest"),
    (re.compile(r"\bbefore\b", re.I), "before"),
    (re.compile(r"\bafter\b", re.I), "after"),
    (re.compile(r"\bquery\b", re.I), "query"),
    (re.compile(r"\bblocker\b", re.I), "blocker"),
)


def local_validation_issue(detail: str) -> dict[str, str]:
    """Normalize an existing precise validator message into a compact issue object."""
    path = "$"
    structured_path = re.search(
        r"Attempt report ([A-Za-z_][A-Za-z0-9_.\[\]]*)",
        detail,
    )
    if structured_path is not None:
        path = structured_path.group(1)
    for pattern, candidate in _PATH_PATTERNS:
        if pattern.search(detail):
            path = candidate
            break
    lowered = detail.lower()
    if "fields must be exactly" in lowered or (
        "unknown" in lowered and "field" in lowered
    ):
        code = "invalid_fields"
    elif "outside" in lowered or "unknown" in lowered:
        code = "not_visible"
    elif "direction advancement limit exceeded" in lowered:
        code = "direction_advancement_limit_exceeded"
    elif "only one direction may be in progress" in lowered:
        code = "direction_concurrency_conflict"
    elif "in progress" in lowered or "status" in lowered or "transition" in lowered:
        code = "invalid_state"
    elif "exceeds" in lowered or "at most" in lowered or "byte limit" in lowered:
        code = "limit_exceeded"
    elif "already exists" in lowered:
        code = "conflict"
    else:
        code = "invalid_value"
    return {"path": path, "code": code, "message": detail}
