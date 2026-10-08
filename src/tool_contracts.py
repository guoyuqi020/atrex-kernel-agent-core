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


def _claim_results() -> dict[str, Any]:
    return {
        "type": "array",
        "maxItems": 32,
        "items": _object(
            {
                "kernel_artifact_digest": _digest(),
                "result_artifact_digests": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 4096,
                    "uniqueItems": True,
                    "items": _digest(),
                },
            }
        ),
        "description": (
            "Exact visible Kernel/Result bindings; available with any Journal module selection."
        ),
    }


def _claim_kind() -> dict[str, Any]:
    return {
        "enum": ["observation", "implementation_outcome", "causal_hypothesis"],
        "default": "causal_hypothesis",
    }


def _evaluate_schema() -> dict[str, Any]:
    """Describe Agent inputs, including file helpers resolved before the HTTP request."""
    schema = _object(
        {
            "operation": {"const": "evaluate"},
            "mode": {"type": "string", "enum": ["full", "correctness_only"], "default": "full"},
            "latency_prediction": {
                "type": "string",
                "enum": ["improved", "retained", "degraded"],
                "description": (
                    "Before seeing the result, predict geometric-mean latency: improved means "
                    ">1% faster, retained means within +/-1% inclusive, and degraded means "
                    ">1% slower. For ABBA compare B against A; otherwise compare this "
                    "candidate against the Kernel at the start of this Attempt. Required for "
                    "full mode; not returned to the Agent."
                ),
            },
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
            "if": {"required": ["mode"], "properties": {"mode": {"const": "correctness_only"}}},
            "else": {"required": ["latency_prediction"]},
        },
        {
            "if": {"required": ["comparison"], "properties": {"comparison": {"type": "object"}}},
            "then": {"properties": {"mode": {"const": "full"}}},
        },
    ]
    return schema


def _direction_schema(*, experiments_enabled: bool = True) -> dict[str, Any]:
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
            "hypothesis_status": {"enum": ["unresolved", "supported", "refuted"]},
            "claim_kind": _claim_kind(),
            "scope": {"oneOf": [_text(), {"type": "null"}]},
            "supporting_results": _claim_results(),
            "supporting_experiment_ids": {
                "type": "array",
                "maxItems": 32,
                "uniqueItems": True,
                "items": _identifier("experiment_"),
            },
        },
        required=("action", "direction_id", "analysis"),
    )
    if not experiments_enabled:
        update["properties"].pop("supporting_experiment_ids")
    update["allOf"] = [
        {
            "if": {"properties": {"action": {"const": "start"}}},
            "then": {
                "not": {
                    "anyOf": [
                        {"required": ["hypothesis_status"]},
                        {"required": ["supporting_experiment_ids"]},
                        {"required": ["supporting_results"]},
                        {"required": ["scope"]},
                        {"required": ["claim_kind"]},
                    ]
                }
            },
            "else": {
                "required": ["hypothesis_status", "supporting_experiment_ids"]
                if experiments_enabled
                else ["hypothesis_status"]
            },
        }
    ]
    update["description"] = (
        "complete, abandon, block, and defer each require hypothesis_status and analysis. "
        "Use unresolved and empty support when unmeasured or inconclusive; stopping work does "
        "not refute its hypothesis. For supported/refuted, provide scope and completed Result "
        "evidence relevant to the proposal hypothesis, directly through supporting_results "
        "or through selected Experiments. Runtime checks bindings and operation eligibility, "
        "not scientific truth; insufficient support downgrades to unresolved "
        "with assessment_notes. "
        + (
            "supporting_experiment_ids may be empty; selected IDs must belong to this Direction. "
            if experiments_enabled
            else "The Experiment module is disabled; omit supporting_experiment_ids. "
        )
        + "Propose and start do not require outcome evidence."
    )
    proposal["properties"].update(
        {
            "relationship": {
                "enum": [
                    "retry",
                    "refinement",
                    "reimplementation",
                    "correction",
                    "port",
                    "combination",
                    "adoption",
                ]
            },
            "derived_from_direction_ids": {
                "type": "array",
                "maxItems": 32,
                "uniqueItems": True,
                "items": _identifier("direction_"),
            },
            "derived_from_experiment_ids": {
                "type": "array",
                "maxItems": 32,
                "uniqueItems": True,
                "items": _identifier("experiment_"),
            },
            "supersedes_direction_id": {"anyOf": [_identifier("direction_"), {"type": "null"}]},
        }
    )
    proposal["description"] = (
        "Optional genealogy is declared once at proposal. Parents must be visible; combination "
        "requires two distinct parent Directions, directly or via Experiments. Only correction "
        "may supersede a parent. Explain the relationship in rationale. To correct ancestry, "
        "propose a new derived Direction rather than rewriting the old one."
    )
    return {"oneOf": [proposal, update]}


def _experiment_schema(*, allow_baseline: bool, directions_enabled: bool = True) -> dict[str, Any]:
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
    if not directions_enabled:
        schema["properties"].pop("direction_id")
        schema["required"].remove("direction_id")
    schema["allOf"] = [
        {
            "anyOf": [
                {"properties": {"before": _subject()}},
                {"properties": {"after": _subject()}},
            ]
        },
        {
            "if": {
                "properties": {"action": {"enum": ["adopt", "keep_after", "restore_before"]}},
                "required": ["action"],
            },
            "then": {"properties": {"before": _subject(), "after": _subject()}},
        },
        {
            "if": {"properties": {"action": {"const": "baseline"}}, "required": ["action"]},
            "then": {"properties": {"before": {"type": "null"}, "after": _subject()}},
        },
    ]
    return schema


def _attempt_report_schema(
    *, allow_baseline: bool, experiments_enabled: bool = True
) -> dict[str, Any]:
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
                        "root_cause": {"oneOf": [_text(), {"type": "null"}], "default": None},
                        "claim": {"oneOf": [_text(), {"type": "null"}]},
                        "claim_kind": _claim_kind(),
                        "assessment": {
                            "enum": ["unresolved", "supported", "refuted"],
                            "default": "unresolved",
                        },
                        "scope": {"oneOf": [_text(), {"type": "null"}]},
                        "supporting_results": _claim_results(),
                        "resolution": _text(),
                        "lesson": _text(),
                        "supporting_experiment_ids": {
                            "type": "array",
                            "maxItems": 32,
                            "uniqueItems": True,
                            "items": _identifier("experiment_"),
                        },
                    },
                    required=("category", "observation", "resolution", "lesson"),
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
    if not experiments_enabled:
        finding = schema["properties"]["findings"]["items"]
        finding["properties"].pop("supporting_experiment_ids")
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
    "kernel-pareto-frontier": _object({}),
    "update-direction": _direction_schema(),
    "list-directions": {
        **_SCRATCH_FILE,
        "description": (
            "Find visible hypotheses by ID, name, status, and ancestry. Writes the index to file; "
            "returns only status/file/count. Read that file, then load selected Directions."
        ),
    },
    "load-direction": {
        **_object({"direction_id": _identifier("direction_")}),
        "description": (
            "Read one visible Direction's hypothesis, rationale, plan, criteria, latest analysis, "
            "and evidence links. Use an ID from a list/find result or a known visible record. "
            "Does not start the Direction or return its raw conversation."
        ),
    },
    "list-experiments": {
        **_SCRATCH_FILE,
        "description": (
            "Find visible experiments and their recorded hypothesis, change, evidence, analysis, "
            "and action. Writes the index to file; returns only status/file/count. Load selected "
            "records only when more detail or exact Artifact bindings are needed."
        ),
    },
    "load-experiment": {
        **_object({"experiment_id": _identifier("experiment_")}),
        "description": (
            "Read one complete visible Experiment, including before/after Kernel and Result "
            "Artifact bindings. Use result-artifact-read or kernel-artifact-read for the actual "
            "observation or source. Does not return the full Session transcript."
        ),
    },
    "find-kernel-experiments": _object({"kernel_artifact_digest": _digest()}),
    "find-kernel-directions": _object({"kernel_artifact_digest": _digest()}),
}


def tool_request_schema(
    command: str,
    *,
    allow_baseline: bool = False,
    operation: str | None = None,
    directions_enabled: bool = True,
    experiments_enabled: bool = True,
) -> dict[str, Any] | None:
    """Return the exact local Agent request contract when Core owns validation."""
    if command == "gateway-execute" and operation == "evaluate":
        return _evaluate_schema()
    if command == "record-experiment":
        return _experiment_schema(
            allow_baseline=allow_baseline, directions_enabled=directions_enabled
        )
    if command == "attempt-report":
        return _attempt_report_schema(
            allow_baseline=allow_baseline, experiments_enabled=experiments_enabled
        )
    if command == "update-direction":
        return _direction_schema(experiments_enabled=experiments_enabled)
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
                "Before complete, abandon, block, or defer, declare hypothesis_status. "
                "Use unresolved with empty support for untested ideas or blockers. Select IDs "
                "from load-direction associated_experiment_ids only when relevant, or cite exact "
                "Kernel/Result bindings directly with supporting_results. Judged conclusions need "
                "scope and completed evidence; lifecycle closure is not hypothesis refutation"
            )
        },
        {
            "instruction": (
                "When direction_concurrency_conflict is returned, continue the existing "
                "in_progress(self) Direction or close it with complete, abandon, defer, or block. "
                "Retry start only after this Attempt has no in_progress(self) Direction; "
                "do not close other Attempts' work"
            )
        },
        {
            "instruction": (
                "When direction_trajectory_conflict is returned, another Pool Trajectory "
                "already advanced that Direction ID in this Epoch. Propose a new Direction "
                "with relationship=reimplementation and the inherited ID as its parent"
            )
        },
        {
            "instruction": (
                "When direction_advancement_limit_exceeded is returned, the requested Direction "
                "was not started. Leave its status unchanged for a future Attempt; do not retry "
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
                "before and after cannot both be null, even for abandon_direction. "
                "Set each non-null before/after subject to one real Kernel-bound "
                "result_artifact_digest; "
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
    "find-kernel-experiments": [
        {
            "instruction": (
                "Use the exact kernel_artifact_digest from a Gateway result or a visible "
                "Experiment; only visible Journal associations are returned"
            )
        }
    ],
    "find-kernel-directions": [
        {
            "instruction": (
                "Use the exact kernel_artifact_digest from a Gateway result; Direction IDs "
                "are derived only from visible recorded Experiments citing that Artifact"
            )
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
                "Read both indexes and close every in_progress(self) Direction "
                "with update-direction before retrying attempt-report; "
                "do not close other Attempts' Directions. complete, abandon, block, and defer "
                "all require hypothesis_status; unresolved permits empty support. "
                "blocked/pivot may have zero Experiments even after an unmeasured closure; "
                "never fabricate evidence to end a session"
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
    command: str,
    *,
    operation: str | None = None,
    detail: str = "",
    directions_enabled: bool = True,
    experiments_enabled: bool = True,
) -> list[dict[str, Any]] | None:
    """Return bounded, visibility-safe next actions for repairing one local request."""
    if command == "gateway-execute" and operation == "evaluate":
        return _evaluate_recovery(detail)
    if command == "record-experiment" and not directions_enabled:
        return [
            {
                "instruction": (
                    "Omit direction_id. Cite real visible Result Artifacts in before/after."
                )
            }
        ]
    if command == "update-direction" and not experiments_enabled:
        return [
            {
                "instruction": (
                    "Close the Direction with analysis and hypothesis_status; "
                    "omit supporting_experiment_ids. Use unresolved if unmeasured; for a judged "
                    "conclusion provide scope and supporting_results with exact "
                    "Kernel/Result bindings."
                )
            }
        ]
    if command == "attempt-report" and (not directions_enabled or not experiments_enabled):
        return [
            {
                "instruction": (
                    "Use only enabled Journal modules and the live attempt-report schema; "
                    "never invent missing evidence."
                )
            }
        ]
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
    elif field == "latency_prediction":
        instruction = (
            "For full Evaluate, set latency_prediction to improved, retained, or degraded "
            "before measuring. Compare B against A for ABBA, or the candidate against this "
            "Attempt's starting Kernel otherwise. correctness_only does not require it."
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
    (re.compile(r"before and after cannot both", re.I), "before"),
    (
        re.compile(r"Supporting Experiment|Direction supporting_experiment_ids", re.I),
        "supporting_experiment_ids",
    ),
    (re.compile(r"hypothesis_status", re.I), "hypothesis_status"),
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
        r"input_path|shapes_path|input_py|shapes|mode|latency_prediction|"
        r"baseline_path|candidate_path|repeats)\b",
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
