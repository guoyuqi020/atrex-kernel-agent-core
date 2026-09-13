"""Evaluate selects local sources and optionally performs an exploratory ABBA comparison."""

from __future__ import annotations

import base64
import json
import re
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import runtime_tools
from tool_contracts import local_validation_issue, tool_recovery, tool_request_schema

_INPUT = "def _make_inputs(n): return {'n': n}\n"
_SHAPES = {"0": {"input_kwargs": {"n": 128}}}
_RESULT = {
    "comparison": {"method": "abba", "repeats": 2},
    "baseline_kernel_artifact_digest": "sha256:" + "c" * 64,
    "kernel_artifact_digest": "sha256:" + "d" * 64,
    "mode": "full",
    "input_scope": "contract",
    "schedule": [{"side": "A", "repeat": 0}, {"side": "B", "repeat": 0}],
    "baseline": {"correct": True, "latency_us_geomean": 10.0},
    "candidate": {"correct": True, "latency_us_geomean": 8.0},
    "correct": True,
    "speedup": 1.25,
    "improvement_pct": 20.0,
    "aggregation": "geometric_mean",
    "shape_batch_count": 1,
}


def _comparison(**changes: Any) -> dict[str, Any]:
    return {"method": "abba", "baseline_path": "scratch/baseline.py", **changes}


def _request(**changes: Any) -> dict[str, Any]:
    return {"operation": "evaluate", "comparison": _comparison(), **changes}


@pytest.fixture
def context(tmp_path: Path) -> Any:
    kernel = tmp_path / "work/kernel"
    kernel.mkdir(parents=True)
    (kernel / "kernel.py").write_text("def candidate(): pass\n", encoding="utf-8")
    (tmp_path / "scratch").mkdir()
    (tmp_path / "scratch/baseline.py").write_text("def baseline(): pass\n", encoding="utf-8")
    return SimpleNamespace(
        workspace=tmp_path,
        working_kernel=kernel,
        attempt_id="attempt_" + "e" * 32,
        gateway_url="http://runtime.invalid",
        gateway_capability="local-test-capability",
    )


@pytest.fixture
def captured_requests(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    requests: list[dict[str, Any]] = []

    def post(url: str, capability: str, path: str, value: dict[str, Any]) -> dict[str, Any]:
        assert (url, capability, path) == (
            "http://runtime.invalid",
            "local-test-capability",
            "/v1/operations",
        )
        requests.append(deepcopy(value))
        return {
            "schema_version": 2,
            "operation": "evaluate",
            "status": "completed",
            "kernel_artifact_digest": "sha256:" + "d" * 64,
            "result_artifact_digest": "sha256:" + "f" * 64,
            "evaluation": None,
            "result": deepcopy(_RESULT),
        }

    monkeypatch.setattr(runtime_tools, "_post", post)
    return requests


def test_evaluate_comparison_uploads_a_and_b_and_preserves_result(
    context: Any, captured_requests: list[dict[str, Any]]
) -> None:
    request = _request()
    response = runtime_tools.gateway_execute(context, request)
    sent = captured_requests[0]
    for field, text in [
        ("baseline", b"def baseline(): pass\n"),
        ("candidate", b"def candidate(): pass\n"),
    ]:
        assert sent[field]["files"] == [
            {"path": "kernel.py", "content_base64": base64.b64encode(text).decode()}
        ]
    assert sent["comparison"] == {"method": "abba"}
    assert request["comparison"]["baseline_path"] == "scratch/baseline.py"
    assert set(sent) == {
        "schema_version",
        "attempt_id",
        "operation",
        "comparison",
        "baseline",
        "candidate",
        "idempotency_key",
    }
    assert response["operation"] == "evaluate"
    assert "kernel_trial_id" not in response
    assert response["result"] == _RESULT
    assert "repeats" not in response["result"]
    assert "evaluation" not in response and "schema_version" not in response


def test_default_evaluate_uses_a_fresh_invocation_identity(
    context: Any, captured_requests: list[dict[str, Any]]
) -> None:
    runtime_tools.gateway_execute(context, {"operation": "evaluate"})
    expected = {
        "schema_version": 2,
        "attempt_id": context.attempt_id,
        "operation": "evaluate",
        "candidate": {
            "files": [
                {
                    "path": "kernel.py",
                    "content_base64": base64.b64encode(b"def candidate(): pass\n").decode(),
                }
            ]
        },
    }
    sent = captured_requests[0]
    assert {key: value for key, value in sent.items() if key != "idempotency_key"} == expected
    assert sent["idempotency_key"].startswith("core-gateway-")


@pytest.mark.parametrize("comparison", [None, _comparison()])
def test_candidate_path_is_supported_with_or_without_comparison(
    context: Any, captured_requests: list[dict[str, Any]], comparison: dict[str, Any] | None
) -> None:
    proposed = context.workspace / "scratch/proposed.py"
    proposed.write_text("def proposed(): pass\n")
    request: dict[str, Any] = {"operation": "evaluate", "candidate_path": "scratch/proposed.py"}
    if comparison is not None:
        request["comparison"] = comparison
    runtime_tools.gateway_execute(context, request)
    sent = captured_requests[0]
    assert sent["candidate"]["files"] == [
        {"path": "kernel.py", "content_base64": base64.b64encode(proposed.read_bytes()).decode()}
    ]
    assert ("baseline" in sent) == (comparison is not None)
    assert "candidate_path" not in sent


def test_null_comparison_keeps_single_kernel_correctness_mode(
    context: Any, captured_requests: list[dict[str, Any]]
) -> None:
    runtime_tools.gateway_execute(
        context, {"operation": "evaluate", "comparison": None, "mode": "correctness_only"}
    )
    assert captured_requests[0]["comparison"] is None
    assert captured_requests[0]["mode"] == "correctness_only"
    assert "baseline" not in captured_requests[0]


def test_source_directory_preserves_relative_names(
    context: Any, captured_requests: list[dict[str, Any]]
) -> None:
    baseline = context.workspace / "scratch/baseline-kernel"
    (baseline / "include").mkdir(parents=True)
    (baseline / "kernel.py").write_text("def baseline(): pass\n")
    (baseline / "include/helper.cuh").write_text("// helper\n")
    runtime_tools.gateway_execute(
        context, _request(comparison=_comparison(baseline_path="scratch/baseline-kernel"))
    )
    assert [entry["path"] for entry in captured_requests[0]["baseline"]["files"]] == [
        "include/helper.cuh",
        "kernel.py",
    ]


def test_comparison_uses_fresh_invocation_identities_and_content_payloads(
    context: Any, captured_requests: list[dict[str, Any]]
) -> None:
    source = context.workspace / "scratch/input.py"
    shapes = context.workspace / "scratch/shapes.json"
    source.write_text(_INPUT, encoding="utf-8")
    shapes.write_text(json.dumps(_SHAPES), encoding="utf-8")
    request = _request(
        comparison=_comparison(repeats=2),
        input_path="scratch/input.py",
        shapes_path="scratch/shapes.json",
    )
    runtime_tools.gateway_execute(context, request)
    runtime_tools.gateway_execute(context, request)
    runtime_tools.gateway_execute(
        context,
        _request(
            comparison=_comparison(repeats=2),
            candidate_path="work/kernel",
            input_py=_INPUT,
            shapes=_SHAPES,
        ),
    )
    (context.workspace / "scratch/baseline.py").rename(context.workspace / "scratch/renamed.py")
    request["comparison"]["baseline_path"] = "scratch/renamed.py"
    runtime_tools.gateway_execute(context, request)
    assert len({value["idempotency_key"] for value in captured_requests}) == 4
    normalized = [
        {key: value for key, value in request.items() if key != "idempotency_key"}
        for request in captured_requests
    ]
    assert all(value == normalized[0] for value in normalized[1:])

    source.write_text(_INPUT + "# changed\n", encoding="utf-8")
    runtime_tools.gateway_execute(context, request)
    shapes.write_text(json.dumps({"0": {"input_kwargs": {"n": 256}}}), encoding="utf-8")
    runtime_tools.gateway_execute(context, request)
    (context.workspace / "scratch/renamed.py").write_text("def changed_baseline(): pass\n")
    runtime_tools.gateway_execute(context, request)
    runtime_tools.gateway_execute(
        context, {**request, "comparison": {**request["comparison"], "repeats": 3}}
    )
    (context.working_kernel / "kernel.py").write_text("def next_candidate(): pass\n")
    runtime_tools.gateway_execute(context, request)
    assert len({value["idempotency_key"] for value in captured_requests}) == 9
    assert captured_requests[0]["comparison"] == {"method": "abba", "repeats": 2}


@pytest.mark.parametrize(
    "fields,field",
    [
        ({"comparison": []}, "comparison"),
        ({"comparison": {}}, "comparison.method"),
        ({"comparison": _comparison(method="other")}, "comparison.method"),
        ({"comparison": _comparison(baseline_path=None)}, "comparison.baseline_path"),
        (
            {"comparison": _comparison(baseline_path=".runtime/attempt.json")},
            "comparison.baseline_path",
        ),
        (
            {"comparison": _comparison(baseline_path="scratch/missing.py")},
            "comparison.baseline_path",
        ),
        ({"comparison": _comparison(repeats=1)}, "comparison.repeats"),
        ({"comparison": _comparison(repeats=21)}, "comparison.repeats"),
        ({"comparison": _comparison(repeats=True)}, "comparison.repeats"),
        ({"comparison": _comparison(extra="not allowed")}, "comparison"),
        ({"mode": "correctness_only"}, "mode"),
        ({"candidate_path": "../outside"}, "candidate_path"),
        ({"input_path": ".runtime/attempt.json"}, "input_path"),
        ({"shapes_path": "scratch/shapes.json"}, "shapes_path"),
        ({"input_py": _INPUT, "input_path": "scratch/input.py"}, "input_path"),
        ({"baseline_path": "scratch/baseline.py"}, "baseline_path"),
        ({"repeats": 2}, "repeats"),
    ],
)
def test_local_errors_identify_nested_fields_and_preserve_evaluate_schema(
    context: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    fields: dict[str, Any],
    field: str,
) -> None:
    (context.workspace / "scratch/shapes.json").write_text('{"broken":')
    request = _request(**fields)
    monkeypatch.setattr(runtime_tools, "_context", lambda _command: context)
    monkeypatch.setattr(runtime_tools, "_request_object", lambda *_args: deepcopy(request))

    def reject_post(*_args: object) -> dict[str, Any]:
        pytest.fail("Invalid local comparison requests must not contact Runtime")

    monkeypatch.setattr(runtime_tools, "_post", reject_post)
    assert runtime_tools.main(["gateway-execute", "--request", "scratch/request.json"]) == 2
    captured = capsys.readouterr()
    assert captured.err == ""
    response = json.loads(captured.out)
    assert response["detail"].startswith("evaluate ")
    assert response["issues"][0]["path"] == field
    assert response["request_schema"] == tool_request_schema(
        "gateway-execute", operation="evaluate"
    )
    assert field in json.dumps(response["recovery"])
    assert "Traceback" not in captured.out and "local-test-capability" not in captured.out


def test_no_standalone_abba_operation_or_schema(context: Any) -> None:
    with pytest.raises(ValueError, match="unsupported gateway-execute operation"):
        runtime_tools.gateway_execute(context, {"operation": "abba"})
    assert tool_request_schema("gateway-execute", operation="abba") is None
    assert tool_recovery("gateway-execute", operation="abba") is None


@pytest.mark.parametrize("field", ["baseline", "candidate"])
def test_wire_sources_cannot_be_provided_inline(context: Any, field: str) -> None:
    with pytest.raises(ValueError, match="Runtime-owned fields"):
        runtime_tools.gateway_execute(context, _request(**{field: {}}))


@pytest.mark.parametrize(
    "kind", ["symlink", "parent_symlink", "nested_symlink", "empty", "not_python"]
)
def test_source_paths_reject_unsafe_or_empty_sources(context: Any, kind: str) -> None:
    path = context.workspace / "scratch/source"
    if kind == "symlink":
        path.symlink_to(context.workspace / "scratch/baseline.py")
    elif kind == "parent_symlink":
        path.symlink_to(context.workspace / "work", target_is_directory=True)
        path = path / "kernel"
    elif kind in {"nested_symlink", "empty"}:
        path.mkdir()
        if kind == "nested_symlink":
            (path / "kernel.py").symlink_to(context.workspace / "scratch/baseline.py")
    else:
        path.write_text("not a Python source filename")
    with pytest.raises(ValueError, match=r"evaluate comparison\.baseline_path"):
        runtime_tools.gateway_execute(
            context,
            _request(
                comparison=_comparison(baseline_path=str(path.relative_to(context.workspace)))
            ),
        )


@pytest.mark.parametrize("kind", ["single_file_bytes", "directory_count"])
def test_source_limits_are_enforced(
    context: Any, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    path = "scratch/baseline.py"
    if kind == "single_file_bytes":
        monkeypatch.setattr(runtime_tools, "_MAX_CANDIDATE_BYTES", 8)
    else:
        directory = context.workspace / "scratch/baseline-kernel"
        directory.mkdir()
        (directory / "kernel.py").write_text("def baseline(): pass")
        (directory / "helper.py").write_text("# helper")
        monkeypatch.setattr(runtime_tools, "_MAX_CANDIDATE_FILES", 1)
        path = "scratch/baseline-kernel"
    with pytest.raises(ValueError, match=r"evaluate comparison\.baseline_path"):
        runtime_tools.gateway_execute(context, _request(comparison=_comparison(baseline_path=path)))


def test_evaluate_schema_and_prompt_examples_describe_nested_comparisons() -> None:
    schema = tool_request_schema("gateway-execute", operation="evaluate")
    assert schema is not None
    assert schema["required"] == ["operation"]
    assert schema["additionalProperties"] is False
    properties = schema["properties"]
    assert {"candidate_path", "comparison"} <= properties.keys()
    assert not {"baseline_path", "repeats", "baseline", "candidate"} & properties.keys()
    comparison = properties["comparison"]["anyOf"][0]
    assert comparison["required"] == ["method", "baseline_path"]
    assert comparison["additionalProperties"] is False
    assert comparison["properties"]["method"] == {"const": "abba"}
    assert comparison["properties"]["repeats"]["minimum"] == 2
    assert comparison["properties"]["repeats"]["maximum"] == 20
    assert comparison["properties"]["repeats"]["default"] == 2
    assert schema["allOf"][-1]["then"] == {"properties": {"mode": {"const": "full"}}}
    for field in ["comparison.baseline_path", "comparison.method", "comparison.repeats"]:
        assert local_validation_issue(f"evaluate {field} is invalid")["path"] == field

    prompt = (Path(__file__).resolve().parents[1] / "prompts/attempt-tools.md").read_text()
    examples = [
        json.loads(block) for block in re.findall(r"```json\n(.*?)\n```", prompt, re.DOTALL)
    ]
    comparisons = [value for value in examples if value.get("comparison")]
    assert len(comparisons) == 2
    for value in comparisons:
        assert value["operation"] == "evaluate"
        assert set(value) <= properties.keys()
        assert (
            set(comparison["required"])
            <= set(value["comparison"])
            <= comparison["properties"].keys()
        )
        assert value["comparison"]["method"] == "abba"
        assert value.get("mode", "full") == "full"
    assert not any(value.get("operation") == "abba" for value in examples)


def test_runtime_nested_error_guidance_is_preserved() -> None:
    remote = {
        "issues": [{"path": "comparison.repeats", "code": "schedule_exceeds_allocation"}],
        "request_schema": {"title": "Runtime Evaluate schema"},
        "recovery": [{"instruction": "Reduce comparison.repeats to fit the allocation."}],
    }
    assert (
        runtime_tools._augment_agent_error(
            "gateway-execute", deepcopy(remote), operation="evaluate", detail="invalid schedule"
        )
        == remote
    )
