"""Local Agent tool request-contract tests."""

from __future__ import annotations

from runtime_tools import _EXPERIMENT_FIELDS, _REPORT_FIELDS
from tool_contracts import local_validation_issue, tool_recovery, tool_request_schema


def test_complex_local_schemas_track_validator_top_level_fields() -> None:
    experiment = tool_request_schema("record-experiment")
    bootstrap_experiment = tool_request_schema(
        "record-experiment",
        allow_baseline=True,
    )
    report = tool_request_schema("attempt-report")
    bootstrap_report = tool_request_schema(
        "attempt-report",
        allow_baseline=True,
    )

    assert experiment is not None
    assert bootstrap_experiment is not None
    assert report is not None
    assert bootstrap_report is not None
    assert set(experiment["required"]) == _EXPERIMENT_FIELDS
    assert set(experiment["properties"]) == _EXPERIMENT_FIELDS
    assert set(report["required"]) == _REPORT_FIELDS
    assert set(report["properties"]) == _REPORT_FIELDS
    assert experiment["additionalProperties"] is False
    assert report["additionalProperties"] is False
    assert experiment["properties"]["action"]["enum"] == [
        "keep_after",
        "restore_before",
        "abandon_direction",
    ]
    assert bootstrap_experiment["properties"]["action"]["enum"][-1] == "baseline"
    assert report["properties"]["status"]["enum"] == [
        "candidate_ready",
        "pivot",
        "blocked",
    ]
    assert bootstrap_report["properties"]["status"]["enum"] == [
        "candidate_ready",
        "blocked",
    ]
    profile_result = bootstrap_report["properties"]["profile_evidence"]["oneOf"][0][
        "properties"
    ]["supporting_results"]["items"]
    assert profile_result["properties"]["operation"] == {"const": "profile"}


def test_simple_read_contracts_are_exact_and_bounded() -> None:
    listing = tool_request_schema("list-experiments")
    direction_listing = tool_request_schema("list-directions")
    artifact = tool_request_schema("kernel-artifact-read")

    assert listing == {
        "type": "object",
        "properties": {"file": {"type": "string", "pattern": r"^scratch/.+"}},
        "required": ["file"],
        "additionalProperties": False,
    }
    assert direction_listing == listing
    assert artifact is not None
    assert artifact["required"] == ["kernel_artifact_digest", "file"]
    assert artifact["additionalProperties"] is False


def test_validator_message_becomes_compact_repair_issue() -> None:
    assert local_validation_issue("Experiment Direction must be in progress") == {
        "path": "direction_id",
        "code": "invalid_state",
        "message": "Experiment Direction must be in progress",
    }


def test_direction_limit_and_terminal_state_errors_name_the_repair_target() -> None:
    detail = (
        "Attempt Direction advancement limit exceeded: maximum=3; "
        "requested_direction_id=direction_44444444444444444444444444444444; "
        "already_advanced_direction_ids=['direction_11111111111111111111111111111111', "
        "'direction_22222222222222222222222222222222', "
        "'direction_33333333333333333333333333333333']. "
        "The requested Direction was not started; keep it proposed or deferred for a future Attempt"
    )
    assert local_validation_issue(detail) == {
        "path": "direction_id",
        "code": "direction_advancement_limit_exceeded",
        "message": detail,
    }
    detail = (
        "Attempt report cannot leave any Direction in progress: "
        "['direction_11111111111111111111111111111111']"
    )
    assert local_validation_issue(detail) == {
        "path": "direction_events",
        "code": "invalid_state",
        "message": detail,
    }


def test_attempt_report_recovery_explains_how_to_close_open_directions() -> None:
    recovery = tool_recovery("attempt-report")

    assert recovery is not None
    assert recovery[0] == {
        "tool": "list-directions",
        "request": {"file": "scratch/directions-index.json"},
    }
    assert recovery[1] == {
        "tool": "list-experiments",
        "request": {"file": "scratch/experiments-index.json"},
    }
    assert "close every in_progress Direction" in recovery[2]["instruction"]
    assert "Use defer or block when no Experiment exists" in recovery[2]["instruction"]
    assert "failed attempt-report publishes nothing" in recovery[3]["instruction"]
    assert "then retry" in recovery[3]["instruction"]
    assert "never retry after a successful response" in recovery[3]["instruction"]


def test_direction_limit_recovery_says_not_to_retry_start() -> None:
    recovery = tool_recovery("update-direction")

    assert recovery is not None
    instruction = recovery[-1]["instruction"]
    assert "direction_advancement_limit_exceeded" in instruction
    assert "requested Direction was not started" in instruction
    assert "do not retry start in the current Attempt" in instruction
