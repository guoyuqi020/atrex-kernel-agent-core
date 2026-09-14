"""Agent-visible schema and repair hints must agree with strict Journal writes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import test_runtime_tools as helpers
from test_runtime_tools import _runtime_owned_journals as _runtime_owned_journals

import runtime_tools
from tool_contracts import tool_request_schema


@pytest.mark.parametrize(
    "action", ["abandon_direction", "keep_after", "restore_before", "adopt", "baseline"]
)
def test_both_null_cli_error_has_actionable_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    action: str,
) -> None:
    context = (
        helpers._bootstrap_context(tmp_path) if action == "baseline" else helpers._context(tmp_path)
    )
    monkeypatch.setattr(runtime_tools, "_context", lambda _command: context)
    request = {
        **helpers._experiment("direction_" + "a" * 32),
        "action": action,
        "before": None,
        "after": None,
    }
    path = context.workspace / "scratch/request.json"
    path.write_text(json.dumps(request), encoding="utf-8")
    assert runtime_tools.main(["record-experiment", "--request", str(path)]) == 2
    error = json.loads(capsys.readouterr().out)
    assert error["status"] == "error"
    assert error["error"] == "invalid_request"
    assert error["issues"][0]["path"] == "before"
    assert "cannot both be null" in error["issues"][0]["message"]
    assert "anyOf" in error["request_schema"]["allOf"][0]
    assert "cannot both be null" in json.dumps(error["recovery"])
    assert helpers._fake_state(context)["experiments"] == []


@pytest.mark.parametrize("side", ["before", "after"])
def test_diagnostic_experiment_can_bind_only_one_side(tmp_path: Path, side: str) -> None:
    context = helpers._context(tmp_path)
    direction = helpers._propose_and_start_direction(context)
    request = {**helpers._experiment(direction), "action": "abandon_direction"}
    request["after" if side == "before" else "before"] = None
    receipt = runtime_tools.record_experiment(context, request)
    stored = runtime_tools.load_experiment(context, {"experiment_id": receipt["experiment_id"]})
    assert stored[side]["result_artifact_digests"]
    assert stored["after" if side == "before" else "before"] is None


def test_closure_schema_requires_selected_support_and_assessment() -> None:
    schema = tool_request_schema("update-direction")["oneOf"][1]
    assert set(schema["allOf"][0]["else"]["required"]) == {
        "supporting_experiment_ids",
        "hypothesis_status",
    }
    assert schema["properties"]["supporting_experiment_ids"]["uniqueItems"]
    assert schema["properties"]["hypothesis_status"]["enum"] == [
        "unresolved",
        "supported",
        "refuted",
    ]


def test_remote_generic_issue_is_mapped_to_direction_support() -> None:
    detail = "Supporting Experiment must belong to the Direction being closed"
    error = runtime_tools._augment_agent_error(
        "update-direction",
        {"issues": [{"path": "$", "message": detail}]},
        detail=detail,
    )
    assert error["issues"][0]["path"] == "supporting_experiment_ids"
    assert "associated_experiment_ids" in json.dumps(error["recovery"])
    assert "request_schema" in error


def test_blocked_bootstrap_can_handoff_real_diagnostics_without_baseline(tmp_path: Path) -> None:
    context = helpers._bootstrap_context(tmp_path)
    direction = helpers._propose_and_start_direction(context)
    receipt = helpers._record_diagnostic_experiment(context, direction)
    helpers._change_direction(
        context,
        {
            "action": "block",
            "direction_id": direction,
            "analysis": "Diagnostic result records the blocker",
        },
    )
    request = helpers._report(receipt["experiment_id"])
    request.update(
        status="blocked", final_candidate=None, profile_evidence=None, blocker="Diagnostic failure"
    )
    assert runtime_tools.attempt_report(context, request)["report_status"] == "blocked"
