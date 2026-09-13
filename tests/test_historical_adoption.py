"""Offline coverage for historical adoption and evidence-free terminal reports."""

from __future__ import annotations

import io
import json
import urllib.request
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
import test_runtime_tools as helpers
from test_runtime_tools import _runtime_owned_journals as _runtime_owned_journals

import runtime_tools
from tool_contracts import tool_request_schema

_REAL_RUNTIME_JOURNAL = runtime_tools.runtime_journal
_REAL_REGISTER_REPORT = runtime_tools._register_attempt_report
_MISSING = object()


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Unexpected network request in historical-adoption tests")

    monkeypatch.setattr(urllib.request, "urlopen", forbidden)


def _terminal_report(status: str) -> dict[str, Any]:
    report = helpers._report("experiment_" + "1" * 32)
    report.update(
        {
            "status": status,
            "final_candidate": None,
            "profile_evidence": None,
            "findings": [],
            "contributing_result_artifact_digests": [],
            "blocker": "No usable evaluation could be obtained" if status == "blocked" else None,
        }
    )
    return report


@pytest.mark.parametrize("allow_baseline", [False, True])
def test_adopt_schema_exposes_action_and_requires_trial_reference_fields(
    allow_baseline: bool,
) -> None:
    schema = tool_request_schema("record-experiment", allow_baseline=allow_baseline)

    assert schema is not None
    assert "adopt" in schema["properties"]["action"]["enum"]
    assert {"before", "after"} <= set(schema["required"])
    for side in ("before", "after"):
        subject = schema["properties"][side]["oneOf"][0]
        assert subject["required"] == ["result_artifact_digest"]
        assert subject["properties"]["result_artifact_digest"] == {
            "type": "string",
            "pattern": r"^sha256:[0-9a-f]{64}$",
        }
        assert subject["additionalProperties"] is False
        assert schema["allOf"][0]["if"]["properties"]["action"] == {"const": "adopt"}
        assert schema["allOf"][0]["then"]["properties"][side] == subject


@pytest.mark.parametrize("allow_baseline", [False, True])
def test_report_schema_requires_findings_only_for_candidate_ready(allow_baseline: bool) -> None:
    schema = tool_request_schema("attempt-report", allow_baseline=allow_baseline)

    assert schema is not None
    assert "minItems" not in schema["properties"]["findings"]
    assert schema["allOf"] == [
        {
            "if": {
                "properties": {"status": {"const": "candidate_ready"}},
                "required": ["status"],
            },
            "then": {"properties": {"findings": {"minItems": 1}}},
        }
    ]


def test_historical_adopt_survives_http_and_journal_snapshot_into_candidate_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = helpers._context(tmp_path)
    # Freeze the existing Trial evidence as visible history, leaving no current Experiment.
    helpers._completed_test_experiment(context)
    state = helpers._fake_state(context)
    helpers._FAKE_HISTORY[str(context.workspace)] = deepcopy(state)
    for records in state.values():
        records.clear()
    assert not state["experiments"]

    fake_journal = runtime_tools.runtime_journal
    commands = {
        operation: command for command, operation in runtime_tools._RUNTIME_JOURNAL_COMMANDS.items()
    }
    calls: list[dict[str, Any]] = []

    def urlopen(request: urllib.request.Request, timeout: float) -> io.BytesIO:
        assert timeout > 0
        assert request.get_method() == "POST"
        assert request.get_header("Authorization") == "Bearer capability"
        assert isinstance(request.data, bytes)
        value = json.loads(request.data)
        calls.append(deepcopy(value))
        if request.full_url == context.gateway_url + "/v1/runtime/journals":
            result = fake_journal(
                context,
                commands[value["operation"]],
                value.get("request", {}),
            )
        else:
            assert request.full_url == context.gateway_url + "/v1/runtime/queries"
            assert value["operation"] == "attempt_report"
            assert not context.report_path.exists()
            helpers._REGISTERED_REPORTS.append(deepcopy(value["report"]))
            result = {"status": "registered"}
        return io.BytesIO(json.dumps({"result": result}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(runtime_tools, "runtime_journal", _REAL_RUNTIME_JOURNAL)
    monkeypatch.setattr(runtime_tools, "_register_attempt_report", _REAL_REGISTER_REPORT)
    context.working_kernel = tmp_path / "working-kernel"
    context.working_kernel.mkdir()
    (context.working_kernel / "kernel.py").write_text("# recovered historical candidate\n")

    direction_id = helpers._propose_and_start_direction(context)
    experiment = helpers._experiment(direction_id)
    experiment["action"] = "adopt"
    receipt = runtime_tools.record_experiment(context, experiment)
    helpers._complete_direction(context, direction_id)

    published = runtime_tools.attempt_report(
        context,
        helpers._report(str(receipt["experiment_id"])),
    )

    experiment_calls = [call for call in calls if call["operation"] == "experiment_record"]
    assert len(experiment_calls) == 1
    assert experiment_calls[0]["request"] == experiment
    assert experiment_calls[0]["request"]["after"] == {"result_artifact_digest": "sha256:" + "f" * 64}
    snapshot_operation = runtime_tools._RUNTIME_JOURNAL_COMMANDS["_journal-snapshot"]
    assert any(call["operation"] == snapshot_operation for call in calls)
    assert published["report_status"] == "candidate_ready"
    report = json.loads(context.report_path.read_text())
    assert report["experiments"][0]["action"] == "adopt"
    assert report["experiments"][0]["after"] == helpers._materialized_subject(experiment["after"])
    assert report["experiments"][0]["experiment_id"] == receipt["experiment_id"]
    assert report["findings"][0]["supporting_experiment_ids"] == [receipt["experiment_id"]]
    assert [report] == helpers._REGISTERED_REPORTS


@pytest.mark.parametrize("side", ["before", "after"])
@pytest.mark.parametrize(
    "value",
    [_MISSING, None, {}, {"result_artifact_digest": ""}],
    ids=["missing", "null", "empty-object", "empty-trial"],
)
def test_adopt_rejects_missing_or_invalid_trial_references(
    tmp_path: Path,
    side: str,
    value: object,
) -> None:
    context = helpers._context(tmp_path)
    direction_id = helpers._propose_and_start_direction(context)
    experiment = helpers._experiment(direction_id)
    experiment["action"] = "adopt"
    if value is _MISSING:
        del experiment[side]
    else:
        experiment[side] = value

    with pytest.raises(ValueError, match=r"before|after|fields"):
        runtime_tools.record_experiment(context, experiment)

    assert not helpers._fake_state(context)["experiments"]
    assert not helpers._REGISTERED_REPORTS


def test_adopt_rejects_both_trial_references_null(tmp_path: Path) -> None:
    context = helpers._context(tmp_path)
    direction_id = helpers._propose_and_start_direction(context)
    experiment = helpers._experiment(direction_id)
    experiment.update({"action": "adopt", "before": None, "after": None})

    with pytest.raises(ValueError, match=r"adopt requires.*before"):
        runtime_tools.record_experiment(context, experiment)

    assert not helpers._fake_state(context)["experiments"]


@pytest.mark.parametrize("status", ["blocked", "pivot"])
def test_terminal_report_can_publish_without_experiments_findings_or_direction_events(
    tmp_path: Path,
    status: str,
) -> None:
    context = helpers._context(tmp_path)

    published = runtime_tools.attempt_report(context, _terminal_report(status))

    assert published["report_status"] == status
    assert published["experiment_count"] == published["finding_count"] == 0
    report = json.loads(context.report_path.read_text())
    assert report["experiments"] == report["findings"] == report["direction_events"] == []
    assert [report] == helpers._REGISTERED_REPORTS


def test_blocked_bootstrap_can_publish_without_experiments(tmp_path: Path) -> None:
    context = helpers._bootstrap_context(tmp_path)

    published = runtime_tools.attempt_report(context, _terminal_report("blocked"))

    assert published["report_status"] == "blocked"
    assert published["experiment_count"] == published["finding_count"] == 0


@pytest.mark.parametrize(
    ("missing", "message"),
    [
        ("experiments", "Experiment"),
        ("direction_events", "Direction event"),
        ("findings", "findings"),
    ],
)
def test_candidate_ready_still_requires_each_kind_of_evidence(
    tmp_path: Path,
    missing: str,
    message: str,
) -> None:
    context = helpers._context(tmp_path)
    _, receipt = helpers._completed_test_experiment(context)
    report = helpers._report(str(receipt["experiment_id"]))
    if missing == "findings":
        report["findings"] = []
    else:
        helpers._fake_state(context)[missing].clear()

    with pytest.raises(ValueError, match=message):
        runtime_tools.attempt_report(context, report)

    assert not context.report_path.exists()
    assert not helpers._REGISTERED_REPORTS


def test_adopt_does_not_replace_bootstrap_candidate_baseline(tmp_path: Path) -> None:
    context = helpers._bootstrap_context(tmp_path)
    direction_id = helpers._propose_and_start_direction(context)
    experiment = helpers._experiment(direction_id)
    experiment["action"] = "adopt"
    receipt = runtime_tools.record_experiment(context, experiment)
    helpers._complete_direction(context, direction_id)

    with pytest.raises(ValueError, match="exactly one baseline Experiment"):
        runtime_tools.attempt_report(context, helpers._report(str(receipt["experiment_id"])))

    assert not context.report_path.exists()
    assert not helpers._REGISTERED_REPORTS


@pytest.mark.parametrize(("status", "close_action"), [("blocked", "block"), ("pivot", "defer")])
def test_zero_experiments_cannot_bypass_an_active_direction(
    tmp_path: Path,
    status: str,
    close_action: str,
) -> None:
    context = helpers._context(tmp_path)
    direction_id = helpers._propose_and_start_direction(context)
    # An active historical Direction must be checked even with an empty current journal.
    state = helpers._fake_state(context)
    helpers._FAKE_HISTORY[str(context.workspace)] = deepcopy(state)
    state["direction_events"].clear()
    report = _terminal_report(status)

    with pytest.raises(ValueError, match="cannot leave any Direction in progress"):
        runtime_tools.attempt_report(context, report)

    assert not context.report_path.exists()
    assert not helpers._REGISTERED_REPORTS
    runtime_tools.update_direction(
        context,
        {
            "action": close_action,
            "direction_id": direction_id,
            "analysis": "No Experiment was run, so close this Direction explicitly",
        },
    )
    published = runtime_tools.attempt_report(context, report)
    assert published["report_status"] == status
    assert published["experiment_count"] == 0
    assert helpers._fake_state(context)["direction_events"][-1]["action"] == close_action
