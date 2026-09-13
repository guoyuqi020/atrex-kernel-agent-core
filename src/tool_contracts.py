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
    return _object({"result_artifact_digest": _digest()})


def _evaluate_schema() -> dict[str, Any]:
    """Describe Agent inputs, including file helpers resolved before the HTTP request."""
    schema = _object(
        {
            "operation": {"const": "evaluate"},
            "mode": {"type": "string", "enum": ["full", "correctness_only"], "default": "full"},
            "candidate_path": {
                **_text(),
                "description": (
                    "Optional workspace-relative Candidate .py file or Kernel directory; "
                    "Defaults to work/kernel. No absolute/traversal paths, links, or .runtime."
                ),
            },
            "comparison": {
                "anyOf": [
                    _object(
                        {
                            "method": {"const": "abba"},
                            "baseline_path": {
                                **_text(),
                                "description": (
                                    "Workspace-relative baseline A .py file or Kernel directory. "
                                    "A single .py file is uploaded as kernel.py. "
                                    "No absolute/traversal paths, links, or .runtime paths."
                                ),
                            },
                            "repeats": {
                                "type": "integer",
                                "minimum": 2,
                                "maximum": 20,
                                "default": 2,
                                "description": (
                                    "Observations per side; 2 schedules A, B, B, A. "
                                    "The schedule must fit Runtime's allocation budget."
                                ),
                            },
                        },
                        required=("method", "baseline_path"),
                    ),
                    {"type": "null"},
                ],
                "description": "Optional exploratory A/B comparison; requires full mode.",
            },
            "input_py": {
                "anyOf": [
                    {"type": "string", "minLength": 1, "maxLength": 131_072},
                    {"type": "null"},
                ],
                "description": (
                    "Nonblank Agate _make_inputs(**input_kwargs) source, at most 128 KiB in UTF-8. "
                    "Return a dictionary keyed by Model.forward argument names; do not hard-code "
                    "a random seed. Prefer pairing custom source with matching custom shapes."
                ),
            },
            "shapes": {
                "anyOf": [
                    {
                        "type": "object",
                        "minProperties": 1,
                        "additionalProperties": {"type": "object"},
                    },
                    {"type": "null"},
                ],
                "description": (
                    "Shape IDs must be integer-parseable strings. Each Agate Shape record maps "
                    "input_kwargs to _make_inputs keyword arguments, not Tensor definitions, "
                    "and optional init_kwargs to Model constructor arguments (null or {} for "
                    "no arguments). Match the input generator and public Model.forward ABI."
                ),
            },
            "input_path": {
                **_text(),
                "description": (
                    "Workspace-relative regular UTF-8 source file, at most 128 KiB. "
                    "Contents must implement _make_inputs; see input_py for the public ABI. "
                    "No absolute/traversal paths, links, or .runtime control paths."
                ),
            },
            "shapes_path": {
                **_text(),
                "description": (
                    "Workspace-relative regular UTF-8 JSON file, at most 256 KiB; contents "
                    "must match shapes. input_kwargs maps to _make_inputs parameters; "
                    "init_kwargs maps to Model constructor arguments. "
                    "No absolute/traversal paths, links, or .runtime paths."
                ),
            },
        },
        required=("operation",),
    )
    schema["allOf"] = [
        {"not": {"required": ["input_py", "input_path"]}},
        {"not": {"required": ["shapes", "shapes_path"]}},
        {
            "if": {"required": ["comparison"], "properties": {"comparison": {"type": "object"}}},
            "then": {"properties": {"mode": {"const": "full"}}},
        },
    ]
    return schema


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
            "action": {"enum": ["start", "complete", "abandon", "block", "defer"]},
            "direction_id": _identifier("direction_"),
            "analysis": _text(),
        }
    )
    return {"oneOf": [proposal, update]}


def _experiment_schema(*, allow_baseline: bool) -> dict[str, Any]:
    nullable_subject = {"oneOf": [_subject(), {"type": "null"}]}
    actions = ["keep_after", "restore_before", "abandon_direction", "adopt"]
    if allow_baseline:
        actions.append("baseline")
    schema = _object(
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
    schema["allOf"] = [
        {
            "if": {"properties": {"action": {"const": "adopt"}}, "required": ["action"]},
            "then": {"properties": {"before": _subject(), "after": _subject()}},
        }
    ]
    return schema


def _attempt_report_schema(*, allow_baseline: bool) -> dict[str, Any]:
    result_binding = _object(
        {
            "operation": {"const": "profile"},
            "kernel_artifact_digest": _digest(),
            "result_artifact_digest": _digest(),
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
    schema = _object(
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
            "final_candidate": {"oneOf": [_object({"change_summary": _text()}), {"type": "null"}]},
            "evidence_summary": _object({"correctness": _text(), "performance": _text()}),
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
            "contributing_result_artifact_digests": {
                "type": "array",
                "maxItems": 64,
                "items": _digest(),
            },
            "blocker": {"oneOf": [_text(), {"type": "null"}]},
        }
    )
    schema["allOf"] = [
        {
            "if": {
                "properties": {"status": {"const": "candidate_ready"}},
                "required": ["status"],
            },
            "then": {"properties": {"findings": {"minItems": 1}}},
        }
    ]
    return schema


_SCRATCH_FILE = _object({"file": {"type": "string", "pattern": r"^scratch/.+"}})
_SCHEMAS: dict[str, dict[str, Any]] = {
    "kernel-artifact-read": _object(
        {
            "kernel_artifact_digest": _digest(),
            "artifact_file": _text(),
            "file": {"type": "string", "pattern": r"^scratch/.+"},
        },
        required=("kernel_artifact_digest", "file"),
    ),
    "result-artifact-read": _object({"result_artifact_digest": _digest()}),
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
    operation: str | None = None,
) -> dict[str, Any] | None:
    """Return the exact local Agent request contract when Core owns validation."""
    if command == "gateway-execute" and operation == "evaluate":
        return _evaluate_schema()
    if command == "record-experiment":
        return _experiment_schema(allow_baseline=allow_baseline)
    if command == "attempt-report":
        return _attempt_report_schema(allow_baseline=allow_baseline)
    return _SCHEMAS.get(command)


_RECOVERY: dict[str, list[dict[str, Any]]] = {
    "kernel-artifact-read": [
        {
            "instruction": (
                "Use a kernel_artifact_digest returned by a Gateway result or load-experiment"
            )
        }
    ],
    "result-artifact-read": [
        {
            "instruction": (
                "Use a result_artifact_digest returned by a visible Gateway operation, "
                "kernel-artifact-read, or Experiment"
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
        {
            "instruction": (
                "Bind the Experiment to a visible in_progress, completed, abandoned, blocked, "
                "or deferred Direction. Late evidence can be recorded after closure without "
                "reopening or changing its status. A proposed Direction must be started first"
            )
        },
        {
            "instruction": (
                "Set each non-null before/after subject to one visible result_artifact_digest; "
                "Runtime resolves the Kernel and Result Artifacts. For exact historical source "
                "reuse, use action=adopt with both real Results; historical after is permitted "
                "only when Runtime validates its matching successful full Evaluate"
            )
        },
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
                "complete or abandon requires a supporting Experiment. blocked/pivot may have "
                "zero Experiments and empty findings; never fabricate evidence to end a session"
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


def tool_recovery(
    command: str, *, operation: str | None = None, detail: str = ""
) -> list[dict[str, Any]] | None:
    """Return bounded, visibility-safe next actions for repairing one local request."""
    if command == "gateway-execute" and operation == "evaluate":
        return _evaluate_recovery(detail)
    return _RECOVERY.get(command)


def _evaluate_recovery(detail: str) -> list[dict[str, Any]]:
    issue = local_validation_issue(detail)
    field, code = issue["path"], issue["code"]
    if code == "mutually_exclusive":
        other = "input_py" if field == "input_path" else "shapes"
        instruction = f"Keep only one of {field} and {other}; remove the other field."
    elif field == "shapes_path" and code in {"invalid_json", "invalid_shape_file"}:
        instruction = (
            "Fix the shapes_path file as a non-empty JSON object keyed by numeric Shape IDs, "
            "not an array or quoted JSON string. Each record's input_kwargs supplies "
            "_make_inputs keyword arguments, not Tensor definitions; optional init_kwargs "
            "supplies Model constructor arguments (null or {} for no arguments). Match the "
            "paired custom input generator and public Model.forward ABI."
        )
    elif code == "invalid_encoding":
        instruction = f"Save the file named by {field} as UTF-8 text, not binary data."
    elif code == "limit_exceeded" and field in {"input_path", "shapes_path"}:
        limit = "128 KiB" if field == "input_path" else "256 KiB"
        instruction = f"Reduce the file named by {field} to at most {limit}; count UTF-8 bytes."
    elif field in {"input_path", "shapes_path"}:
        instruction = (
            f"Set {field} to an existing regular UTF-8 file under real workspace directories, "
            "for example scratch/custom-input.py or scratch/custom-shapes.json. "
            "Do not use absolute paths, dot/traversal components, symlinks, or .runtime files."
        )
    elif field == "input_py":
        instruction = (
            "Define _make_inputs with parameters matching the Shape records' input_kwargs; "
            "return a dictionary keyed by public Model.forward argument names. Do not "
            "hard-code a random seed. Adapt the paired custom input/Shape examples in the "
            "tool instructions to your task ABI."
        )
    elif field == "shapes":
        instruction = (
            "Use numeric Shape ID keys and records with input_kwargs for _make_inputs "
            "keyword arguments, not Tensor definitions, and optional init_kwargs for Model "
            "constructor arguments (null or {} for no arguments). Adapt the paired custom "
            "input/Shape examples in the tool instructions to your public task ABI."
        )
    elif field in {"comparison.baseline_path", "candidate_path"}:
        instruction = (
            f"Set {field} to a real workspace-relative .py file or Kernel directory. "
            "A single .py file is uploaded as kernel.py. Do not use absolute/traversal paths, "
            "symlinks, or .runtime files. comparison.baseline_path is required for ABBA; "
            "omit candidate_path to use work/kernel."
        )
    elif field.startswith("comparison") or field in {"baseline_path", "repeats"}:
        instruction = (
            "Set comparison.method to abba and name a workspace .py file or Kernel "
            "directory in comparison.baseline_path. Set comparison.repeats from 2 to 20 "
            "(default 2). ABBA requires full mode (normally omitted); "
            "use 2 when a larger schedule exceeds Runtime's allocation budget."
        )
    elif field == "mode" and "comparison" in detail:
        instruction = (
            "Use mode full for comparison.method abba. To check correctness only, "
            "remove comparison and set mode to correctness_only."
        )
    else:
        instruction = (
            "Repair the fields identified in issues using request_schema. "
            "Evaluate accepts full or correctness_only, not correctness; file helpers and "
            "inline forms of the same input are mutually exclusive."
        )
    return [
        {"instruction": instruction},
        {
            "instruction": (
                "Save the corrected Evaluate request "
                "under scratch/ and invoke gateway-execute again. "
                "The tool rereads input files and derives the retry identity from their contents; "
                "do not supply idempotency_key or blindly retry an unchanged invalid request."
            )
        },
    ]


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
    (
        re.compile(r"contributing[_ ]result[_ ]artifact[_ ]digests", re.I),
        "contributing_result_artifact_digests",
    ),
    (re.compile(r"kernel[_ ]artifact", re.I), "kernel_artifact_digest"),
    (re.compile(r"(?:result[_ ]artifact|gateway[_ ]result)", re.I), "result_artifact_digest"),
    (re.compile(r"\bbefore\b", re.I), "before"),
    (re.compile(r"\bafter\b", re.I), "after"),
    (re.compile(r"\bquery\b", re.I), "query"),
    (re.compile(r"\bblocker\b", re.I), "blocker"),
)


def local_validation_issue(detail: str) -> dict[str, str]:
    """Normalize an existing precise validator message into a compact issue object."""
    evaluate_field = re.match(
        r"^evaluate "
        r"(comparison(?:\.(?:baseline_path|method|repeats))?|"
        r"input_path|shapes_path|input_py|shapes|mode|baseline_path|candidate_path|repeats)\b",
        detail,
    )
    if evaluate_field is not None:
        if "mutually exclusive" in detail:
            code = "mutually_exclusive"
        elif "valid JSON" in detail:
            code = "invalid_json"
        elif "JSON object" in detail:
            code = "invalid_shape_file"
        elif "contain UTF-8" in detail:
            code = "invalid_encoding"
        elif "byte limit" in detail:
            code = "limit_exceeded"
        elif (
            "workspace-relative path" in detail
            or "control files" in detail
            or "symbolic links" in detail
        ):
            code = "invalid_path"
        elif "regular file" in detail or "Kernel directory" in detail:
            code = "invalid_file"
        else:
            code = "invalid_value"
        return {"path": evaluate_field.group(1), "code": code, "message": detail}
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
    if "fields must be exactly" in lowered or ("unknown" in lowered and "field" in lowered):
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
