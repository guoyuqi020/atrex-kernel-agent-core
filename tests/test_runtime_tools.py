from __future__ import annotations

import io
import json
import urllib.error
from copy import deepcopy
from datetime import UTC, datetime
from email.message import Message
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

import runtime_tools
from contexts.lineage_bootstrap import RuntimeLineageBootstrapContext
from runtime_tools import (
    RuntimeServiceError,
    _agent_knowledge,
    _atomic_json,
    _request_object,
    attempt_report,
    gateway_execute,
    list_directions,
    list_experiments,
    load_direction,
    load_experiment,
    record_experiment,
    runtime_query,
    update_direction,
    wiki_query,
)


def _context(root: Path) -> Any:
    (root / "scratch").mkdir(parents=True)
    return SimpleNamespace(
        workspace=root,
        attempt_id="attempt_0123456789abcdef0123456789abcdef",
        report_path=root / "scratch/attempt-report.json",
        gateway_url="http://runtime.invalid",
        gateway_capability="capability",
        manifest={"context": {"epoch_number": 2, "attempt_ordinal": 2}},
    )


def _bootstrap_context(root: Path) -> RuntimeLineageBootstrapContext:
    (root / "scratch").mkdir(parents=True)
    return RuntimeLineageBootstrapContext(
        workspace=root,
        repository=root,
        manifest_path=root / ".runtime/lineage-bootstrap.json",
        report_path=root / "scratch/attempt-report.json",
        token_usage_path=root / "scratch/token-usage.json",
        session_trace_path=None,
        gateway_url="http://runtime.invalid",
        gateway_capability="capability",
        agent_problem={},
        wiki_url=None,
        wiki_capability=None,
        usage_unit="provider_tokens",
        usage_budget=1000,
        timeout_seconds=60,
        manifest={
            "bootstrap_attempt_id": "attempt_0123456789abcdef0123456789abcdef",
            "dsl": "triton",
        },
    )


_FAKE_JOURNALS: dict[str, dict[str, list[dict[str, Any]]]] = {}
_FAKE_HISTORY: dict[str, dict[str, list[dict[str, Any]]]] = {}
_REGISTERED_REPORTS: list[dict[str, Any]] = []


def _fake_state(context: Any) -> dict[str, list[dict[str, Any]]]:
    return _FAKE_JOURNALS.setdefault(
        str(context.workspace),
        {"direction_events": [], "experiments": []},
    )


def _fake_visible(context: Any, field: str) -> list[dict[str, Any]]:
    history = _FAKE_HISTORY.get(str(context.workspace), {}).get(field, [])
    return [*deepcopy(history), *deepcopy(_fake_state(context)[field])]


def _fake_direction_views(context: Any) -> dict[str, dict[str, Any]]:
    statuses = {
        "propose": "proposed",
        "start": "in_progress",
        "complete": "completed",
        "abandon": "abandoned",
        "block": "blocked",
        "defer": "deferred",
    }
    directions: dict[str, dict[str, Any]] = {}
    for event in _fake_visible(context, "direction_events"):
        direction_id = str(event["direction_id"])
        if event["action"] == "propose":
            directions[direction_id] = {
                "direction_id": direction_id,
                "name": event["name"],
                "hypothesis": event["hypothesis"],
                "rationale": event["rationale"],
                "plan": event["plan"],
                "success_criteria": event["success_criteria"],
                "stop_conditions": event["stop_conditions"],
                "status": "proposed",
                "analysis": None,
                "supporting_experiment_ids": [],
            }
        else:
            directions[direction_id]["status"] = statuses[event["action"]]
            directions[direction_id]["analysis"] = event["analysis"]
    for experiment in _fake_visible(context, "experiments"):
        direction = directions.get(str(experiment["direction_id"]))
        if direction is not None:
            direction["supporting_experiment_ids"].append(experiment["experiment_id"])
    return directions


@pytest.fixture(autouse=True)
def _runtime_owned_journals(monkeypatch: pytest.MonkeyPatch) -> None:
    _FAKE_JOURNALS.clear()
    _FAKE_HISTORY.clear()

    def journal(context: Any, command: str, request: dict[str, Any]) -> dict[str, Any]:
        state = _fake_state(context)
        if command == "update-direction":
            action = request.get("action")
            if action == "propose":
                expected = {
                    "action",
                    "name",
                    "hypothesis",
                    "rationale",
                    "plan",
                    "success_criteria",
                    "stop_conditions",
                }
                if set(request) != expected:
                    raise ValueError(
                        f"Direction proposal fields must be exactly {sorted(expected)}"
                    )
                direction_id = f"direction_{uuid4().hex}"
                event = {
                    "direction_event_id": f"directionevent_{uuid4().hex}",
                    "direction_id": direction_id,
                    "recorded_at": datetime.now(UTC).isoformat(),
                    **request,
                    "analysis": None,
                    "supporting_experiment_ids": [],
                }
            else:
                expected = {"action", "direction_id", "analysis"}
                if set(request) != expected:
                    raise ValueError(f"Direction update fields must be exactly {sorted(expected)}")
                direction_id = str(request["direction_id"])
                direction = _fake_direction_views(context).get(direction_id)
                if direction is None:
                    raise ValueError(
                        "Direction ID is outside the current Attempt's visible history"
                    )
                if action == "start":
                    started = {
                        event["direction_id"]
                        for event in state["direction_events"]
                        if event["action"] == "start"
                    }
                    if direction_id not in started and len(started) >= 3:
                        raise ValueError("Direction advancement limit exceeded: maximum=3")
                    in_progress = sorted(
                        visible_direction_id
                        for visible_direction_id, visible_direction in _fake_direction_views(
                            context
                        ).items()
                        if visible_direction["status"] == "in_progress"
                        and visible_direction_id != direction_id
                    )
                    if in_progress:
                        raise ValueError(
                            "Only one Direction may be in progress at a time: "
                            f"requested_direction_id={direction_id}; "
                            f"in_progress_direction_ids={in_progress}. "
                            "The requested Direction was not started"
                        )
                if action in {"complete", "abandon"} and not direction["supporting_experiment_ids"]:
                    raise ValueError(
                        f"Direction {action} requires at least one associated Experiment"
                    )
                event = {
                    "direction_event_id": f"directionevent_{uuid4().hex}",
                    "direction_id": direction_id,
                    "recorded_at": datetime.now(UTC).isoformat(),
                    "action": action,
                    "name": None,
                    "hypothesis": None,
                    "rationale": None,
                    "plan": [],
                    "success_criteria": None,
                    "stop_conditions": None,
                    "analysis": request["analysis"],
                    "supporting_experiment_ids": list(direction["supporting_experiment_ids"]),
                }
            state["direction_events"].append(event)
            return {"status": "recorded", "direction_id": direction_id}
        if command == "list-directions":
            return {
                "directions": [
                    {
                        "direction_id": value["direction_id"],
                        "name": value["name"],
                        "status": value["status"],
                    }
                    for value in _fake_direction_views(context).values()
                ]
            }
        if command == "load-direction":
            try:
                return _fake_direction_views(context)[str(request["direction_id"])]
            except KeyError as error:
                raise ValueError(
                    "Direction ID is outside the current Attempt's visible history"
                ) from error
        if command == "record-experiment":
            if set(request) != runtime_tools._EXPERIMENT_FIELDS:
                raise ValueError(
                    f"Experiment fields must be exactly {sorted(runtime_tools._EXPERIMENT_FIELDS)}"
                )
            runtime_tools._validate_experiment_comparison(
                request,
                allow_baseline=isinstance(context, RuntimeLineageBootstrapContext),
            )
            if request["action"] == "baseline" and any(
                value["action"] == "baseline" for value in state["experiments"]
            ):
                raise ValueError(
                    "Bootstrap Experiment journal may contain only one baseline action"
                )
            direction = _fake_direction_views(context).get(str(request["direction_id"]))
            if direction is None or direction["status"] != "in_progress":
                raise ValueError("Experiment Direction must be in progress")
            experiment = {
                "experiment_id": f"experiment_{uuid4().hex}",
                "sequence": len(state["experiments"]) + 1,
                "recorded_at": datetime.now(UTC).isoformat(),
                **request,
            }
            state["experiments"].append(experiment)
            return {"status": "recorded", "experiment_id": experiment["experiment_id"]}
        if command == "list-experiments":
            return {
                "experiments": [
                    {
                        "experiment_id": value["experiment_id"],
                        "sequence": value["sequence"],
                        "name": value["name"],
                        "action": value["action"],
                    }
                    for value in _fake_visible(context, "experiments")
                ]
            }
        if command == "load-experiment":
            for value in _fake_visible(context, "experiments"):
                if value["experiment_id"] == request.get("experiment_id"):
                    return value
            raise ValueError("Experiment ID is outside the current Attempt's visible history")
        if command == "_journal-snapshot":
            return {
                "direction_events": deepcopy(state["direction_events"]),
                "experiments": deepcopy(state["experiments"]),
                "directions": list(_fake_direction_views(context).values()),
            }
        raise AssertionError(f"unexpected Runtime Journal command: {command}")

    monkeypatch.setattr(runtime_tools, "runtime_journal", journal)

    def register(context: Any, report: dict[str, Any]) -> None:
        del context
        _REGISTERED_REPORTS.append(report)

    _REGISTERED_REPORTS.clear()
    monkeypatch.setattr(runtime_tools, "_register_attempt_report", register)


def _propose_and_start_direction(context: Any) -> str:
    proposed = update_direction(
        context,
        {
            "action": "propose",
            "name": "vectorize loads",
            "hypothesis": "one transaction replaces two",
            "rationale": "profile indicates excess memory transactions",
            "plan": ["replace scalar loads"],
            "success_criteria": "latency improves",
            "stop_conditions": "alignment cannot be preserved",
        },
    )
    direction_id = str(proposed["direction_id"])
    update_direction(
        context,
        {
            "action": "start",
            "direction_id": direction_id,
            "analysis": "beginning the planned experiment",
        },
    )
    return direction_id


def _experiment(direction_id: str = "direction_" + "a" * 32) -> dict[str, object]:
    return {
        "direction_id": direction_id,
        "name": "vectorize load",
        "hypothesis": "one transaction replaces two",
        "change": "use a vector load",
        "before": {
            "kernel_artifact_digest": "sha256:" + "a" * 64,
            "kernel_trial_id": "gtrial_" + "b" * 32,
            "gateway_result_digests": ["sha256:" + "c" * 64],
        },
        "after": {
            "kernel_artifact_digest": "sha256:" + "d" * 64,
            "kernel_trial_id": "gtrial_" + "e" * 32,
            "gateway_result_digests": ["sha256:" + "f" * 64],
        },
        "evidence": "evaluation result sha256:example",
        "analysis": "the hypothesis held because latency improved",
        "action": "keep_after",
    }


def test_baseline_experiment_is_bootstrap_only_and_one_sided(tmp_path: Path) -> None:
    ordinary = _context(tmp_path / "ordinary")
    ordinary_direction = _propose_and_start_direction(ordinary)
    baseline = _experiment(ordinary_direction)
    baseline.update({"action": "baseline", "before": None})
    with pytest.raises(ValueError, match="only valid during Bootstrap"):
        record_experiment(ordinary, baseline)

    bootstrap = _bootstrap_context(tmp_path / "bootstrap")
    direction_id = _propose_and_start_direction(bootstrap)
    baseline = _experiment(direction_id)
    baseline.update({"action": "baseline", "before": None})
    receipt = record_experiment(bootstrap, baseline)
    assert str(receipt["experiment_id"]).startswith("experiment_")
    with pytest.raises(ValueError, match="only one baseline"):
        record_experiment(bootstrap, baseline)

    _complete_direction(bootstrap, direction_id)
    result = attempt_report(bootstrap, _report(str(receipt["experiment_id"])))
    assert result["status"] == "candidate_ready"


def test_blocked_bootstrap_cannot_omit_measured_baseline(tmp_path: Path) -> None:
    bootstrap = _bootstrap_context(tmp_path / "bootstrap")
    direction_id = _propose_and_start_direction(bootstrap)
    receipt = record_experiment(bootstrap, _experiment(direction_id))
    _complete_direction(bootstrap, direction_id)
    report = _report(str(receipt["experiment_id"]))
    report.update(
        {
            "status": "blocked",
            "final_candidate": None,
            "blocker": "the measured candidate could not be repaired",
        }
    )

    with pytest.raises(
        ValueError,
        match="may omit baseline only when no Experiment has identity-bearing Gateway evidence",
    ):
        attempt_report(bootstrap, report)


def _complete_direction(context: Any, direction_id: str) -> None:
    update_direction(
        context,
        {
            "action": "complete",
            "direction_id": direction_id,
            "analysis": "the success criterion was met",
        },
    )


def _completed_test_experiment(context: Any) -> tuple[str, dict[str, Any]]:
    direction_id = _propose_and_start_direction(context)
    receipt = record_experiment(context, _experiment(direction_id))
    _complete_direction(context, direction_id)
    return direction_id, receipt


def _report(experiment_id: str) -> dict[str, object]:
    return {
        "status": "candidate_ready",
        "hypothesis": "one transaction replaces two",
        "diagnosis": {
            "bottleneck": "load issue rate",
            "evidence": "survey localized load issue pressure",
        },
        "approach": {
            "summary": "vectorize the load",
            "steps": ["replace scalar loads"],
            "expected_impact": "reduce memory transactions",
            "risks": ["alignment must be checked"],
        },
        "final_candidate": {"change_summary": "used a vector load"},
        "evidence_summary": {
            "correctness": "evaluate result is correct",
            "performance": "evaluate result is faster",
        },
        "profile_evidence": {
            "tool_used": "gateway-execute/profile",
            "profiler": "ncu",
            "profile_level": "survey",
            "bottleneck_type": "memory_bound",
            "evidence_summary": "survey result localized the bottleneck",
            "evidence_chain": "survey counters support the diagnosed bottleneck",
            "supporting_results": [
                {
                    "operation": "profile",
                    "kernel_artifact_digest": "sha256:" + "d" * 64,
                    "kernel_trial_id": "gtrial_" + "e" * 32,
                    "gateway_result_digest": "sha256:" + "f" * 64,
                }
            ],
        },
        "analysis": "candidate is correct and faster",
        "knowledge_used": [],
        "findings": [
            {
                "category": "correctness",
                "observation": "vector loads require alignment",
                "root_cause": "unaligned accesses are unsafe",
                "resolution": "added an alignment guard",
                "lesson": "alignment must be checked",
                "supporting_experiment_ids": [experiment_id],
            }
        ],
        "blocker": None,
    }


def test_request_must_be_a_bounded_regular_file_under_scratch(tmp_path: Path) -> None:
    context = _context(tmp_path)
    request = tmp_path / "scratch/request.json"
    request.write_text('{"operation":"evaluate"}', encoding="utf-8")
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")

    assert _request_object(context, Path("scratch/request.json"), "gateway-execute") == {
        "operation": "evaluate"
    }
    with pytest.raises(ValueError, match="under scratch"):
        _request_object(context, outside, "gateway-execute")


def test_runtime_http_error_preserves_structured_repair_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "error": "invalid_request",
        "detail": "dev.command: Field required",
        "issues": [{"path": "command", "code": "missing", "message": "Field required"}],
        "request_schema": {"operations": {"dev": {"required": ["operation", "command"]}}},
    }

    def reject(*_args: object, **_kwargs: object) -> object:
        raise urllib.error.HTTPError(
            "http://runtime.invalid/v1/operations",
            400,
            "Bad Request",
            Message(),
            io.BytesIO(json.dumps(payload).encode()),
        )

    monkeypatch.setattr("urllib.request.urlopen", reject)
    with pytest.raises(RuntimeServiceError) as raised:
        runtime_tools._post(
            "http://runtime.invalid",
            "capability",
            "/v1/operations",
            {"operation": "dev"},
        )

    assert raised.value.status_code == 400
    assert raised.value.payload == payload


def test_cli_prints_structured_runtime_error_without_traceback(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(runtime_tools, "_context", lambda _command: object())
    monkeypatch.setattr(runtime_tools, "_request_object", lambda *_args: {"operation": "dev"})

    def reject(*_args: object) -> object:
        raise RuntimeServiceError(
            400,
            {
                "error": "invalid_request",
                "detail": "dev.command: Field required",
                "issues": [{"path": "command", "code": "missing", "message": "Field required"}],
                "request_schema": {"operations": {"dev": {"required": ["command"]}}},
            },
        )

    monkeypatch.setattr(runtime_tools, "gateway_execute", reject)
    status = runtime_tools.main(["gateway-execute", "--request", "scratch/request.json"])

    assert status == 2
    assert json.loads(capsys.readouterr().out) == {
        "status": "error",
        "error": "invalid_request",
        "command": "gateway-execute",
        "http_status": 400,
        "detail": "dev.command: Field required",
        "issues": [{"path": "command", "code": "missing", "message": "Field required"}],
        "request_schema": {"operations": {"dev": {"required": ["command"]}}},
    }


def test_cli_local_validation_adds_schema_and_recovery(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(runtime_tools, "_context", lambda _command: object())

    def reject(*_args: object) -> dict[str, object]:
        raise ValueError("Experiment Direction must be in progress")

    monkeypatch.setattr(runtime_tools, "_request_object", reject)
    status = runtime_tools.main(["record-experiment", "--request", "scratch/request.json"])

    assert status == 2
    response = json.loads(capsys.readouterr().out)
    assert response["issues"] == [
        {
            "path": "direction_id",
            "code": "invalid_state",
            "message": "Experiment Direction must be in progress",
        }
    ]
    assert set(response["request_schema"]["required"]) == runtime_tools._EXPERIMENT_FIELDS
    assert response["recovery"][0] == {
        "tool": "list-directions",
        "request": {"file": "scratch/directions-index.json"},
    }


def test_runtime_query_error_is_rewritten_to_agent_wrapper_contract() -> None:
    response = runtime_tools._augment_agent_error(
        "kernel-artifact-read",
        {
            "error": "invalid_request",
            "issues": [{"path": "file", "code": "invalid", "message": "unsafe path"}],
            "request_schema": {"title": "internal Runtime query"},
        },
        detail="unsafe path",
    )

    assert response["issues"] == [
        {"path": "artifact_file", "code": "invalid", "message": "unsafe path"}
    ]
    assert response["request_schema"]["required"] == [
        "kernel_artifact_digest",
        "file",
    ]
    assert "artifact_file" in response["request_schema"]["properties"]


def test_attempt_report_error_returns_actionable_direction_recovery() -> None:
    detail = (
        "Attempt report cannot leave any Direction in progress: "
        "['direction_11111111111111111111111111111111']"
    )

    response = runtime_tools._augment_agent_error(
        "attempt-report",
        {"status": "error", "error": "invalid_request", "detail": detail},
        detail=detail,
    )

    assert response["issues"] == [
        {
            "path": "direction_events",
            "code": "invalid_state",
            "message": detail,
        }
    ]
    assert response["recovery"][0]["tool"] == "list-directions"
    assert response["recovery"][1]["tool"] == "list-experiments"
    assert "close every in_progress Direction" in response["recovery"][2]["instruction"]


def test_attempt_report_validates_and_atomically_publishes_once(tmp_path: Path) -> None:
    context = _context(tmp_path)
    _direction_id, receipt = _completed_test_experiment(context)
    assert receipt["status"] == "recorded"
    assert isinstance(receipt["experiment_id"], str)
    assert receipt["experiment_id"].startswith("experiment_")

    report = attempt_report(context, _report(receipt["experiment_id"]))

    assert report["schema_version"] == 12
    assert report["experiments"][0]["experiment_id"] == receipt["experiment_id"]
    assert json.loads(context.report_path.read_text(encoding="utf-8")) == report
    with pytest.raises(FileExistsError, match="already exists"):
        attempt_report(context, _report(receipt["experiment_id"]))


def test_attempt_report_registers_with_runtime_before_publishing(tmp_path: Path) -> None:
    context = _context(tmp_path)
    _direction_id, receipt = _completed_test_experiment(context)

    report = attempt_report(context, _report(receipt["experiment_id"]))

    assert [report] == _REGISTERED_REPORTS


def test_a_refused_nomination_publishes_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path)
    _direction_id, receipt = _completed_test_experiment(context)

    def refuse(_context: Any, _report: dict[str, Any]) -> None:
        raise ValueError("no Agent evaluate covers it")

    monkeypatch.setattr(runtime_tools, "_register_attempt_report", refuse)

    with pytest.raises(ValueError, match="no Agent evaluate covers it"):
        attempt_report(context, _report(receipt["experiment_id"]))
    assert not context.report_path.exists()


def test_direction_journal_can_be_recorded_listed_and_loaded(tmp_path: Path) -> None:
    context = _context(tmp_path)
    request = {"file": "scratch/directions-index.json"}
    assert list_directions(context, request) == {
        "status": "completed",
        "file": "scratch/directions-index.json",
        "count": 0,
    }
    index = context.workspace / "scratch/directions-index.json"
    assert json.loads(index.read_text(encoding="utf-8")) == {"directions": []}
    direction_id = _propose_and_start_direction(context)

    assert list_directions(context, request) == {
        "status": "completed",
        "file": "scratch/directions-index.json",
        "count": 1,
    }
    assert json.loads(index.read_text(encoding="utf-8")) == {
        "directions": [
            {
                "direction_id": direction_id,
                "name": "vectorize loads",
                "status": "in_progress",
            }
        ]
    }
    experiment = record_experiment(context, _experiment(direction_id))
    assert load_direction(context, {"direction_id": direction_id})["supporting_experiment_ids"] == [
        experiment["experiment_id"]
    ]
    update_direction(
        context,
        {
            "action": "abandon",
            "direction_id": direction_id,
            "analysis": "the measured candidate regressed",
        },
    )

    loaded = load_direction(context, {"direction_id": direction_id})
    assert loaded == {
        "direction_id": direction_id,
        "name": "vectorize loads",
        "hypothesis": "one transaction replaces two",
        "rationale": "profile indicates excess memory transactions",
        "plan": ["replace scalar loads"],
        "success_criteria": "latency improves",
        "stop_conditions": "alignment cannot be preserved",
        "status": "abandoned",
        "analysis": "the measured candidate regressed",
        "supporting_experiment_ids": [experiment["experiment_id"]],
    }


def test_direction_reads_include_frozen_history_without_provenance(
    tmp_path: Path,
) -> None:
    source = _context(tmp_path / "source")
    direction_id = _propose_and_start_direction(source)
    reader = _context(tmp_path / "reader")
    _FAKE_HISTORY[str(reader.workspace)] = {
        "direction_events": deepcopy(_fake_state(source)["direction_events"]),
        "experiments": [],
    }

    assert list_directions(
        reader,
        {"file": "scratch/directions-index.json"},
    ) == {
        "status": "completed",
        "file": "scratch/directions-index.json",
        "count": 1,
    }
    assert json.loads(
        (reader.workspace / "scratch/directions-index.json").read_text(encoding="utf-8")
    ) == {
        "directions": [
            {
                "direction_id": direction_id,
                "name": "vectorize loads",
                "status": "in_progress",
            }
        ]
    }
    loaded = load_direction(reader, {"direction_id": direction_id})
    assert set(loaded) == {
        "direction_id",
        "name",
        "hypothesis",
        "rationale",
        "plan",
        "success_criteria",
        "stop_conditions",
        "status",
        "analysis",
        "supporting_experiment_ids",
    }


def test_attempt_report_allows_any_number_of_continuable_directions(tmp_path: Path) -> None:
    context = _context(tmp_path)
    _direction_id, receipt = _completed_test_experiment(context)
    for ordinal in range(4):
        update_direction(
            context,
            {
                "action": "propose",
                "name": f"follow-up {ordinal}",
                "hypothesis": "a follow-up mechanism may improve latency",
                "rationale": "the completed experiment exposed another opportunity",
                "plan": ["test the follow-up mechanism"],
                "success_criteria": "latency improves",
                "stop_conditions": "the mechanism is falsified",
            },
        )

    report = attempt_report(context, _report(str(receipt["experiment_id"])))

    assert sum(event["action"] == "propose" for event in report["direction_events"]) == 5


def test_attempt_may_advance_at_most_three_distinct_directions(tmp_path: Path) -> None:
    context = _context(tmp_path)
    for _ordinal in range(3):
        direction_id = _propose_and_start_direction(context)
        update_direction(
            context,
            {
                "action": "defer",
                "direction_id": direction_id,
                "analysis": "preserve this direction for a later Attempt",
            },
        )

    fourth = update_direction(
        context,
        {
            "action": "propose",
            "name": "fourth direction",
            "hypothesis": "a fourth independent mechanism may improve latency",
            "rationale": "the mechanism is distinct from the first three",
            "plan": ["test the fourth mechanism"],
            "success_criteria": "latency improves",
            "stop_conditions": "the mechanism is falsified",
        },
    )
    with pytest.raises(
        ValueError,
        match="Direction advancement limit exceeded: maximum=3",
    ):
        update_direction(
            context,
            {
                "action": "start",
                "direction_id": fourth["direction_id"],
                "analysis": "start the fourth direction",
            },
        )


def test_attempt_may_explore_only_one_direction_at_a_time(tmp_path: Path) -> None:
    context = _context(tmp_path)
    first = _propose_and_start_direction(context)
    second = update_direction(
        context,
        {
            "action": "propose",
            "name": "second hypothesis",
            "hypothesis": "another mechanism may improve latency",
            "rationale": "the mechanism is independent",
            "plan": ["test the second mechanism"],
            "success_criteria": "latency improves",
            "stop_conditions": "the mechanism is falsified",
        },
    )["direction_id"]

    with pytest.raises(ValueError, match="Only one Direction may be in progress at a time"):
        update_direction(
            context,
            {
                "action": "start",
                "direction_id": second,
                "analysis": "incorrectly interleave two explorations",
            },
        )
    assert load_direction(context, {"direction_id": first})["status"] == "in_progress"
    assert load_direction(context, {"direction_id": second})["status"] == "proposed"

    update_direction(
        context,
        {
            "action": "defer",
            "direction_id": first,
            "analysis": "pause this exploration before switching",
        },
    )
    update_direction(
        context,
        {
            "action": "start",
            "direction_id": second,
            "analysis": "begin only after the first Direction is closed",
        },
    )
    assert load_direction(context, {"direction_id": second})["status"] == "in_progress"


def test_attempt_report_rejects_unexperimented_direction_left_in_progress(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    _direction_id, receipt = _completed_test_experiment(context)
    open_direction_id = _propose_and_start_direction(context)

    with pytest.raises(ValueError, match="cannot leave any Direction in progress"):
        attempt_report(context, _report(str(receipt["experiment_id"])))

    update_direction(
        context,
        {
            "action": "defer",
            "direction_id": open_direction_id,
            "analysis": "no Experiment was run, so defer this direction",
        },
    )
    report = attempt_report(context, _report(str(receipt["experiment_id"])))
    assert report["status"] == "candidate_ready"


def test_experiment_journal_can_be_listed_and_loaded_by_id(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    request = {"file": "scratch/experiments-index.json"}
    assert list_experiments(context, request) == {
        "status": "completed",
        "file": "scratch/experiments-index.json",
        "count": 0,
    }
    index = context.workspace / "scratch/experiments-index.json"
    assert json.loads(index.read_text(encoding="utf-8")) == {"experiments": []}
    historical_id = "experiment_" + "8" * 32
    historical_experiment = {
        "experiment_id": historical_id,
        "sequence": 1,
        "recorded_at": "2026-08-24T00:00:00+00:00",
        **_experiment(),
    }
    _FAKE_HISTORY[str(context.workspace)] = {
        "direction_events": [],
        "experiments": [historical_experiment],
    }
    direction_id = _propose_and_start_direction(context)
    first = record_experiment(context, _experiment(direction_id))
    second_value = _experiment(direction_id)
    second_value["name"] = "second vectorization experiment"
    second_value["action"] = "restore_before"
    second = record_experiment(context, second_value)

    assert list_experiments(context, request) == {
        "status": "completed",
        "file": "scratch/experiments-index.json",
        "count": 3,
    }
    assert json.loads(index.read_text(encoding="utf-8")) == {
        "experiments": [
            {
                "experiment_id": historical_id,
                "sequence": 1,
                "name": "vectorize load",
                "action": "keep_after",
            },
            {
                "experiment_id": first["experiment_id"],
                "sequence": 1,
                "name": "vectorize load",
                "action": "keep_after",
            },
            {
                "experiment_id": second["experiment_id"],
                "sequence": 2,
                "name": "second vectorization experiment",
                "action": "restore_before",
            },
        ]
    }
    loaded = load_experiment(context, {"experiment_id": second["experiment_id"]})
    assert loaded["experiment_id"] == second["experiment_id"]
    assert loaded["sequence"] == 2
    assert loaded["name"] == "second vectorization experiment"
    assert loaded["before"] == second_value["before"]
    assert loaded["after"] == second_value["after"]
    assert loaded["evidence"] == second_value["evidence"]
    assert loaded["analysis"] == second_value["analysis"]
    assert loaded["action"] == "restore_before"
    historical = load_experiment(context, {"experiment_id": historical_id})
    assert historical == {
        **historical_experiment,
    }


def test_experiment_journal_reads_reject_unknown_or_extra_selection(tmp_path: Path) -> None:
    context = _context(tmp_path)
    direction_id = _propose_and_start_direction(context)
    record_experiment(context, _experiment(direction_id))

    with pytest.raises(ValueError, match="requires exactly file"):
        list_experiments(context, {"limit": 1})
    with pytest.raises(ValueError, match="safe path under scratch"):
        list_experiments(context, {"file": "../experiments.json"})
    with pytest.raises(ValueError, match="exactly experiment_id"):
        load_experiment(context, {})
    with pytest.raises(ValueError, match="outside the current Attempt's visible history"):
        load_experiment(context, {"experiment_id": "experiment_" + "9" * 32})


def test_attempt_report_rejects_invalid_lists_before_publication(tmp_path: Path) -> None:
    context = _context(tmp_path)
    _direction_id, receipt = _completed_test_experiment(context)
    report = _report(receipt["experiment_id"])
    report["approach"]["steps"] = []  # type: ignore[index]

    with pytest.raises(ValueError, match=r"approach\.steps must not be empty"):
        attempt_report(context, report)
    assert not context.report_path.exists()

    approach = report["approach"]
    assert isinstance(approach, dict)
    approach["steps"] = ["apply the measured optimization"]
    published = attempt_report(context, report)
    assert published["status"] == "candidate_ready"
    assert context.report_path.is_file()


def test_attempt_report_rejects_incomplete_profile_evidence(tmp_path: Path) -> None:
    context = _context(tmp_path)
    _direction_id, receipt = _completed_test_experiment(context)
    report = _report(receipt["experiment_id"])
    profile = report["profile_evidence"]
    assert isinstance(profile, dict)
    profile.pop("evidence_chain")

    with pytest.raises(ValueError, match="profile_evidence fields must be exactly"):
        attempt_report(context, report)
    assert not context.report_path.exists()


def test_attempt_report_rejects_profile_result_absent_from_journal(tmp_path: Path) -> None:
    context = _context(tmp_path)
    _direction_id, receipt = _completed_test_experiment(context)
    report = _report(receipt["experiment_id"])
    profile = report["profile_evidence"]
    assert isinstance(profile, dict)
    results = profile["supporting_results"]
    assert isinstance(results, list)
    results[0]["gateway_result_digest"] = "sha256:" + "1" * 64

    with pytest.raises(ValueError, match="not referenced by the Experiment journal"):
        attempt_report(context, report)
    assert not context.report_path.exists()


def test_attempt_report_rejects_finding_from_another_experiment(tmp_path: Path) -> None:
    context = _context(tmp_path)
    _direction_id, receipt = _completed_test_experiment(context)
    report = _report(receipt["experiment_id"])
    findings = report["findings"]
    assert isinstance(findings, list)
    findings[0]["supporting_experiment_ids"] = ["experiment_" + "9" * 32]

    with pytest.raises(ValueError, match="outside this journal"):
        attempt_report(context, report)
    assert not context.report_path.exists()


def test_attempt_report_rejects_legacy_top_level_decision(tmp_path: Path) -> None:
    context = _context(tmp_path)
    _direction_id, receipt = _completed_test_experiment(context)
    report = _report(receipt["experiment_id"])
    report["decision"] = "keep"

    with pytest.raises(ValueError, match="Attempt report fields must be exactly"):
        attempt_report(context, report)


def test_record_experiment_rejects_legacy_result_field(tmp_path: Path) -> None:
    context = _context(tmp_path)
    experiment = _experiment()
    experiment["result"] = experiment.pop("analysis")

    with pytest.raises(ValueError, match="Experiment fields must be exactly"):
        record_experiment(context, experiment)


def test_record_experiment_rejects_legacy_decision_field(tmp_path: Path) -> None:
    context = _context(tmp_path)
    experiment = _experiment()
    experiment["decision"] = "continue"
    experiment.pop("action")

    with pytest.raises(ValueError, match="Experiment fields must be exactly"):
        record_experiment(context, experiment)


def test_exclusive_atomic_write_never_replaces_existing_file(tmp_path: Path) -> None:
    path = tmp_path / "report.json"
    _atomic_json(path, {"first": True}, exclusive=True)

    with pytest.raises(FileExistsError):
        _atomic_json(path, {"second": True}, exclusive=True)
    assert json.loads(path.read_text(encoding="utf-8")) == {"first": True}


def test_agent_knowledge_hides_runtime_audit_envelope() -> None:
    content = {
        "records": {
            "nvidia.hopper.triton.kernel-opt.reduction": {
                "store": "gpu_wiki",
                "source": "kernel_wiki",
                "type": "technique-card",
                "applies_to": {"arch": "hopper", "dsl": "triton"},
                "match": {"arch": "exact"},
                "payload": {"goal": "tile a reduction"},
            }
        },
        "notes": [],
    }

    assert (
        _agent_knowledge(
            {
                "schema_version": 1,
                "interaction_artifact_digest": "sha256:internal",
                "snapshot_id": "internal-snapshot",
                "content_digest": "sha256:internal",
                "content": content,
            }
        )
        == content
    )


def test_runtime_queries_have_dedicated_commands_and_endpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path)
    calls: list[tuple[str, object]] = []

    def fake_post(url: str, capability: str, path: str, value: object) -> dict[str, Any]:
        assert url == context.gateway_url
        assert capability == context.gateway_capability
        calls.append((path, value))
        if isinstance(value, dict) and value.get("operation") == "gateway_result_read":
            return {
                "status": "completed",
                "result": {
                    "operation": "evaluate",
                    "status": "completed",
                    "result": {"correct": True},
                },
            }
        if isinstance(value, dict) and value.get("operation") == "kernel_artifact_read":
            return {
                "status": "completed",
                "result": {
                    "encoding": "utf-8",
                    "content": "# recovered\n",
                },
            }
        return {
            "status": "completed",
            "result": {
                "kernel_artifact_digest": "sha256:" + "c" * 64,
                "gateway_results": [],
            },
        }

    monkeypatch.setattr(runtime_tools, "_post", fake_post)

    trial_id = "gtrial_" + "d" * 32
    assert runtime_query(context, "kernel-trial-show", {"kernel_trial_id": trial_id}) == {
        "kernel_artifact_digest": "sha256:" + "c" * 64,
        "gateway_results": [],
    }
    path, value = calls[0]
    assert path == "/v1/runtime/queries"
    assert isinstance(value, dict)
    assert value["operation"] == "kernel_trial_show"
    assert value["kernel_trial_id"] == trial_id
    assert runtime_query(
        context,
        "gateway-result-read",
        {"gateway_result_digest": "sha256:" + "a" * 64},
    ) == {
        "operation": "evaluate",
        "status": "completed",
        "result": {"correct": True},
    }
    copied = runtime_query(
        context,
        "kernel-artifact-read",
        {
            "kernel_artifact_digest": "sha256:" + "b" * 64,
            "artifact_file": "kernel.py",
            "file": "scratch/recovered/kernel.py",
        },
    )
    assert copied["status"] == "completed"
    assert copied["file"] == "scratch/recovered/kernel.py"
    assert (tmp_path / "scratch/recovered/kernel.py").read_text() == "# recovered\n"
    _, kernel_request = calls[-1]
    assert isinstance(kernel_request, dict)
    assert kernel_request["file"] == "kernel.py"
    assert "artifact_file" not in kernel_request

    with pytest.raises(ValueError, match="unsupported Runtime query command"):
        runtime_query(context, "kernel-trials", {})
    with pytest.raises(ValueError, match="Runtime-owned fields"):
        runtime_query(
            context,
            "kernel-trial-show",
            {"kernel_trial_id": trial_id, "idempotency_key": "agent-controlled"},
        )
    with pytest.raises(ValueError, match="under scratch"):
        runtime_query(
            context,
            "kernel-artifact-read",
            {
                "kernel_artifact_digest": "sha256:" + "b" * 64,
                "file": "../escaped.py",
            },
        )


def test_gateway_execute_hides_wire_schema_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path)
    sent: list[tuple[str, object]] = []

    def fake_post(_url: str, _capability: str, path: str, value: object) -> dict[str, Any]:
        sent.append((path, value))
        return {
            "schema_version": 2,
            "operation": "health",
            "status": "completed",
            "kernel_artifact_digest": None,
            "kernel_trial_id": None,
            "gateway_result_digest": "sha256:" + "e" * 64,
            "job_id": None,
            "evaluation": None,
            "result": {"ok": True},
        }

    monkeypatch.setattr(runtime_tools, "_post", fake_post)

    response = gateway_execute(context, {"operation": "health"})

    assert response == {"ok": True}
    path, request = sent[0]
    assert path == "/v1/operations"
    assert isinstance(request, dict)
    assert request["schema_version"] == 2
    assert isinstance(request["idempotency_key"], str)
    with pytest.raises(ValueError, match="Runtime-owned fields"):
        gateway_execute(
            context,
            {"operation": "health", "idempotency_key": "agent-controlled"},
        )
    with pytest.raises(ValueError, match="unsupported gateway-execute operation"):
        gateway_execute(context, {"operation": "sol", "solution_path": "solution.json"})
    with pytest.raises(ValueError, match="unsupported gateway-execute operation"):
        gateway_execute(context, {"operation": "submit", "payload_path": "payload.json"})


@pytest.mark.parametrize(
    ("operation", "result"),
    [
        ("env", {"env": [{"name": "L20N"}]}),
        ("health", {"ok": True}),
        ("config", {"url": "https://gateway.invalid", "gpu": "L20N"}),
    ],
)
def test_gateway_execute_returns_read_only_service_result_directly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    result: dict[str, object],
) -> None:
    context = _context(tmp_path)

    def fake_post(_url: str, _capability: str, _path: str, _value: object) -> dict[str, Any]:
        return {
            "schema_version": 2,
            "operation": operation,
            "status": "completed",
            "kernel_artifact_digest": None,
            "kernel_trial_id": None,
            "gateway_result_digest": "sha256:" + "e" * 64,
            "job_id": None,
            "evaluation": None,
            "result": result,
        }

    monkeypatch.setattr(runtime_tools, "_post", fake_post)

    assert gateway_execute(context, {"operation": operation}) == result


def test_gateway_execute_merges_evaluation_into_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path)
    kernel = tmp_path / "work/kernel"
    kernel.mkdir(parents=True)
    kernel.joinpath("kernel.py").write_text("def kernel(): pass\n")
    context.working_kernel = kernel

    def fake_post(_url: str, _capability: str, _path: str, _value: object) -> dict[str, Any]:
        return {
            "schema_version": 2,
            "operation": "evaluate",
            "status": "completed",
            "kernel_artifact_digest": "sha256:" + "a" * 64,
            "kernel_trial_id": "gtrial_" + "b" * 32,
            "gateway_result_digest": "sha256:" + "c" * 64,
            "job_id": None,
            "evaluation": {"correct": True, "latency_us": 12.288},
            "result": {
                "all_pass": True,
                "correctness": {
                    "status": "PASS",
                    "rel_err": 0.001,
                    "max_abs_err": 0.0009765625,
                    "max_rel_err": 0.0078125,
                },
                "failures": [],
                "latency_us_geomean": 12.288,
                "latency_us_by_shape": {"0": 12.288},
            },
        }

    monkeypatch.setattr(runtime_tools, "_post", fake_post)

    response = gateway_execute(context, {"operation": "evaluate"})

    assert "evaluation" not in response
    assert response["result"] == {
        "correct": True,
        "correctness": {
            "status": "PASS",
            "rel_err": 0.001,
            "max_abs_err": 0.0009765625,
            "max_rel_err": 0.0078125,
        },
        "latency_us": 12.288,
        "failures": [],
        "latency_us_by_shape": {"0": 12.288},
    }


def test_gateway_execute_keeps_check_kernel_identities(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path)
    kernel = tmp_path / "work/kernel"
    kernel.mkdir(parents=True)
    kernel.joinpath("kernel.py").write_text("def kernel(): pass\n")
    context.working_kernel = kernel

    def fake_post(_url: str, _capability: str, _path: str, _value: object) -> dict[str, Any]:
        return {
            "schema_version": 2,
            "operation": "check",
            "status": "completed",
            "kernel_artifact_digest": "sha256:" + "a" * 64,
            "kernel_trial_id": "gtrial_" + "b" * 32,
            "gateway_result_digest": "sha256:" + "c" * 64,
            "job_id": "cp_example",
            "evaluation": None,
            "result": {"job_id": "cp_example", "status": "succeeded"},
        }

    monkeypatch.setattr(runtime_tools, "_post", fake_post)

    assert gateway_execute(context, {"operation": "check"}) == {
        "operation": "check",
        "status": "completed",
        "kernel_artifact_digest": "sha256:" + "a" * 64,
        "kernel_trial_id": "gtrial_" + "b" * 32,
        "gateway_result_digest": "sha256:" + "c" * 64,
        "job_id": "cp_example",
        "result": {"job_id": "cp_example", "status": "succeeded"},
    }


def test_gateway_execute_returns_jobs_result_directly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path)

    def fake_post(_url: str, _capability: str, _path: str, _value: object) -> dict[str, Any]:
        return {
            "schema_version": 2,
            "operation": "jobs",
            "status": "completed",
            "kernel_artifact_digest": None,
            "kernel_trial_id": None,
            "gateway_result_digest": "sha256:" + "d" * 64,
            "job_id": None,
            "evaluation": None,
            "result": {
                "jobs": [
                    {
                        "job_id": "pr_example",
                        "operation": "profile",
                        "status": "running",
                    }
                ]
            },
        }

    monkeypatch.setattr(runtime_tools, "_post", fake_post)

    assert gateway_execute(context, {"operation": "jobs"}) == {
        "jobs": [
            {
                "job_id": "pr_example",
                "operation": "profile",
                "status": "running",
            }
        ]
    }


def test_gateway_execute_returns_cancel_result_directly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path)

    def fake_post(_url: str, _capability: str, _path: str, _value: object) -> dict[str, Any]:
        return {
            "schema_version": 2,
            "operation": "cancel",
            "status": "cancelled",
            "kernel_artifact_digest": None,
            "kernel_trial_id": None,
            "gateway_result_digest": "sha256:" + "e" * 64,
            "job_id": "pr_example",
            "evaluation": None,
            "result": {"status": "cancelled", "job_id": "pr_example"},
        }

    monkeypatch.setattr(runtime_tools, "_post", fake_post)

    assert gateway_execute(
        context,
        {"operation": "cancel", "job_id": "pr_example"},
    ) == {"status": "cancelled", "job_id": "pr_example"}


def test_gateway_execute_returns_poll_result_directly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path)

    def fake_post(_url: str, _capability: str, _path: str, _value: object) -> dict[str, Any]:
        return {
            "schema_version": 2,
            "operation": "poll",
            "status": "completed",
            "kernel_artifact_digest": None,
            "kernel_trial_id": None,
            "gateway_result_digest": "sha256:" + "f" * 64,
            "job_id": "pr_example",
            "evaluation": None,
            "result": {
                "job_id": "pr_example",
                "status": "succeeded",
                "result": {"kernels": []},
            },
        }

    monkeypatch.setattr(runtime_tools, "_post", fake_post)

    assert gateway_execute(
        context,
        {"operation": "poll", "job_id": "pr_example"},
    ) == {
        "job_id": "pr_example",
        "status": "succeeded",
        "result": {"kernels": []},
    }


def test_gateway_execute_returns_normalized_kernel_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path)
    kernel = tmp_path / "work/kernel"
    kernel.mkdir(parents=True)
    kernel.joinpath("kernel.py").write_text("def kernel(): pass\n")
    context.working_kernel = kernel

    def fake_post(_url: str, _capability: str, _path: str, _value: object) -> dict[str, Any]:
        return {
            "schema_version": 2,
            "operation": "profile",
            "status": "completed",
            "kernel_artifact_digest": "sha256:" + "a" * 64,
            "kernel_trial_id": "gtrial_" + "b" * 32,
            "gateway_result_digest": "sha256:" + "c" * 64,
            "job_id": "pr_example",
            "evaluation": None,
            "result": {
                "job_id": "pr_example",
                "status": "succeeded",
                "result": {
                    "kernels": [
                        {
                            "name": "kernel",
                            "duration": 12_500,
                            "duration_unit": "ns",
                            "compute_sol_pct": 22.5,
                            "mem_sol_pct": 71.25,
                            "occupancy_pct": 48.0,
                            "registers": 96,
                            "smem_bytes": 65_536,
                            "traffic": {"achieved_dram_gbps": 802.5},
                            "counters": {"sm__throughput.avg.pct": 22.5},
                        },
                        {
                            "kernel_name": "epilogue",
                            "duration_us": 2.5,
                            "compute_sol_pct": 80.0,
                            "memory_sol_pct": 20.0,
                        },
                    ]
                },
            },
        }

    monkeypatch.setattr(runtime_tools, "_post", fake_post)

    assert gateway_execute(
        context,
        {"operation": "profile", "level": "sol", "shape_id": "3"},
    ) == {
        "kernel_artifact_digest": "sha256:" + "a" * 64,
        "kernel_trial_id": "gtrial_" + "b" * 32,
        "gateway_result_digest": "sha256:" + "c" * 64,
        "job_id": "pr_example",
        "status": "succeeded",
        "result": {
            "shape_id": "3",
            "profile_level": "sol",
            "kernel_count": 2,
            "total_duration_us": 15.0,
            "dominant_kernel": "kernel",
            "weighted_sol_pct": 72.70833333333333,
            "dominant_bound": "memory",
            "kernels": [
                {
                    "name": "kernel",
                    "duration_us": 12.5,
                    "duration_share_pct": 83.33333333333333,
                    "compute_sol_pct": 22.5,
                    "memory_sol_pct": 71.25,
                    "bound": "memory",
                    "occupancy_pct": 48.0,
                    "registers_per_thread": 96.0,
                    "shared_memory_bytes": 65_536.0,
                    "traffic": {"achieved_dram_gbps": 802.5},
                    "counters": {"sm__throughput.avg.pct": 22.5},
                },
                {
                    "name": "epilogue",
                    "duration_us": 2.5,
                    "duration_share_pct": 16.666666666666668,
                    "compute_sol_pct": 80.0,
                    "memory_sol_pct": 20.0,
                    "bound": "compute",
                },
            ],
        },
    }


def test_gateway_execute_returns_dev_result_directly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path)
    kernel = tmp_path / "work/kernel"
    kernel.mkdir(parents=True)
    kernel.joinpath("kernel.py").write_text("def kernel(): pass\n")
    context.working_kernel = kernel

    def fake_post(_url: str, _capability: str, _path: str, _value: object) -> dict[str, Any]:
        return {
            "schema_version": 2,
            "operation": "dev",
            "status": "completed",
            "kernel_artifact_digest": "sha256:" + "7" * 64,
            "kernel_trial_id": "gtrial_" + "8" * 32,
            "gateway_result_digest": "sha256:" + "9" * 64,
            "job_id": "dv_example",
            "evaluation": None,
            "result": {
                "job_id": "dv_example",
                "status": "succeeded",
                "result": {"exit_code": 0, "stdout": "ok\n", "stderr": ""},
            },
        }

    monkeypatch.setattr(runtime_tools, "_post", fake_post)

    assert gateway_execute(
        context,
        {"operation": "dev", "command": "python3 kernel.py"},
    ) == {
        "job_id": "dv_example",
        "status": "succeeded",
        "result": {"exit_code": 0, "stdout": "ok\n", "stderr": ""},
    }


def test_gateway_execute_returns_disassemble_result_directly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path)
    kernel = tmp_path / "work/kernel"
    kernel.mkdir(parents=True)
    kernel.joinpath("kernel.py").write_text("def kernel(): pass\n")
    context.working_kernel = kernel

    def fake_post(_url: str, _capability: str, _path: str, _value: object) -> dict[str, Any]:
        return {
            "schema_version": 2,
            "operation": "disassemble",
            "status": "completed",
            "kernel_artifact_digest": "sha256:" + "4" * 64,
            "kernel_trial_id": "gtrial_" + "5" * 32,
            "gateway_result_digest": "sha256:" + "6" * 64,
            "job_id": "da_example",
            "evaluation": None,
            "result": {
                "job_id": "da_example",
                "status": "succeeded",
                "result": {"format": "sass", "assembly": "/* SASS */"},
            },
        }

    monkeypatch.setattr(runtime_tools, "_post", fake_post)

    assert gateway_execute(
        context,
        {"operation": "disassemble", "fmt": "sass"},
    ) == {
        "job_id": "da_example",
        "status": "succeeded",
        "result": {"format": "sass", "assembly": "/* SASS */"},
    }


def test_wiki_query_assigns_request_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path)
    context.wiki_url = "http://wiki.invalid"
    context.wiki_capability = "wiki-capability"
    sent: list[dict[str, Any]] = []

    def fake_post(_url: str, _capability: str, _path: str, value: object) -> dict[str, Any]:
        assert isinstance(value, dict)
        sent.append(value)
        return {"content": {"matches": []}}

    monkeypatch.setattr(runtime_tools, "_post", fake_post)

    assert wiki_query(context, {"query": "shared memory bank conflict"}) == {"matches": []}
    assert sent[0]["query"] == "shared memory bank conflict"
    assert isinstance(sent[0]["idempotency_key"], str)
    with pytest.raises(ValueError, match="unknown Wiki request fields"):
        wiki_query(
            context,
            {"query": "shared memory", "idempotency_key": "agent-controlled"},
        )


def test_a_slow_operation_is_collected_on_reconnect(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts: list[int] = []

    def urlopen(request: Any, timeout: float) -> Any:
        attempts.append(len(attempts))
        if len(attempts) < 3:
            raise TimeoutError("read timed out")

        class _Response:
            def __enter__(self) -> Any:
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def read(self, _limit: int) -> bytes:
                return json.dumps({"result": {"status": "completed"}}).encode()

        return _Response()

    monkeypatch.setattr(runtime_tools.urllib.request, "urlopen", urlopen)

    assert runtime_tools._post("http://runtime.invalid", "cap", "/v1/operations", {}) == {
        "result": {"status": "completed"}
    }
    assert len(attempts) == 3


def test_an_unreachable_runtime_still_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    def urlopen(request: Any, timeout: float) -> Any:
        raise runtime_tools.urllib.error.URLError("connection refused")

    monkeypatch.setattr(runtime_tools.urllib.request, "urlopen", urlopen)

    with pytest.raises(RuntimeError, match="Runtime service is unavailable"):
        runtime_tools._post("http://runtime.invalid", "cap", "/v1/operations", {})
