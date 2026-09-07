"""Evaluate failures expose safe, field-specific guidance through the Agent CLI."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import runtime_tools
from tool_contracts import tool_recovery, tool_request_schema

_SOURCE_MARKER = "private-input-source-must-not-appear"
_CAPABILITY_MARKER = "private-runtime-capability-must-not-appear"


@pytest.fixture
def feedback_context(tmp_path: Path) -> Any:
    kernel = tmp_path / "work/kernel"
    kernel.mkdir(parents=True)
    (kernel / "kernel.py").write_text("class Model: pass\n", encoding="utf-8")
    (tmp_path / "scratch").mkdir()
    return SimpleNamespace(
        workspace=tmp_path,
        working_kernel=kernel,
        attempt_id="attempt_" + "a" * 32,
        gateway_url="http://runtime.invalid",
        gateway_capability=_CAPABILITY_MARKER,
    )


def _local_cli_error(
    context: Any,
    request: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> dict[str, Any]:
    monkeypatch.setattr(runtime_tools, "_context", lambda _command: context)
    monkeypatch.setattr(runtime_tools, "_request_object", lambda *_args: deepcopy(request))

    def unexpected_post(*_args: object, **_kwargs: object) -> dict[str, Any]:
        pytest.fail("Invalid local Evaluate files must never be uploaded")

    monkeypatch.setattr(runtime_tools, "_post", unexpected_post)
    status = runtime_tools.main(["gateway-execute", "--request", "scratch/request.json"])
    captured = capsys.readouterr()

    assert status == 2
    assert captured.err == ""
    assert "Traceback" not in captured.out
    assert _SOURCE_MARKER not in captured.out
    assert _CAPABILITY_MARKER not in captured.out
    response = json.loads(captured.out)
    assert isinstance(response, dict)
    assert response["status"] == "error"
    assert response["error"] == "invalid_request"
    assert response["command"] == "gateway-execute"
    return response


def _assert_evaluate_repair(response: dict[str, Any], field: str, code: str) -> None:
    assert len(response["issues"]) == 1
    issue = response["issues"][0]
    assert issue["path"] == field
    assert issue["code"] == code
    assert issue["message"] == response["detail"]

    schema = response["request_schema"]
    assert schema["type"] == "object"
    assert schema["required"] == ["operation"]
    assert schema["properties"]["operation"] == {"const": "evaluate"}
    assert field in schema["properties"]
    assert schema["additionalProperties"] is False
    recovery = response["recovery"]
    assert isinstance(recovery, list) and 1 <= len(recovery) <= 6
    encoded = json.dumps(recovery)
    assert field in encoded
    assert len(encoded) < 4000


@pytest.mark.parametrize(
    ("field", "invalid", "code"),
    [
        ("input_path", "missing", "invalid_file"),
        ("shapes_path", "missing", "invalid_file"),
        ("input_path", "absolute", "invalid_path"),
        ("shapes_path", "traversal", "invalid_path"),
        ("input_path", "control", "invalid_path"),
        ("shapes_path", "symlink", "invalid_file"),
        ("input_path", "directory", "invalid_file"),
        ("input_path", "encoding", "invalid_encoding"),
        ("shapes_path", "encoding", "invalid_encoding"),
        ("shapes_path", "json", "invalid_json"),
        ("shapes_path", "json_array", "invalid_shape_file"),
        ("input_path", "limit", "limit_exceeded"),
        ("shapes_path", "limit", "limit_exceeded"),
    ],
)
def test_local_evaluate_file_errors_identify_field_and_repair(
    feedback_context: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    field: str,
    invalid: str,
    code: str,
) -> None:
    source = feedback_context.workspace / "scratch/custom-input"
    raw_path = "scratch/custom-input"
    if invalid == "absolute":
        raw_path = str(source)
    elif invalid == "traversal":
        raw_path = "../private-file"
    elif invalid == "control":
        raw_path = ".runtime/capability.json"
    elif invalid == "symlink":
        target = feedback_context.workspace / "scratch/source-target"
        target.write_text(_SOURCE_MARKER, encoding="utf-8")
        source.symlink_to(target)
    elif invalid == "directory":
        source.mkdir()
    elif invalid == "encoding":
        source.write_bytes(b"\xff" + _SOURCE_MARKER.encode())
    elif invalid == "json":
        source.write_text('{"' + _SOURCE_MARKER + '":', encoding="utf-8")
    elif invalid == "json_array":
        source.write_text(json.dumps([_SOURCE_MARKER]), encoding="utf-8")
    elif invalid == "limit":
        limit = (
            runtime_tools._MAX_EVALUATE_INPUT_BYTES
            if field == "input_path"
            else runtime_tools._MAX_EVALUATE_SHAPES_BYTES
        )
        source.write_bytes(b"x" * (limit + 1))

    response = _local_cli_error(
        feedback_context,
        {"operation": "evaluate", "mode": "correctness_only", field: raw_path},
        monkeypatch,
        capsys,
    )

    _assert_evaluate_repair(response, field, code)


@pytest.mark.parametrize(
    ("path_field", "inline_field", "inline_value"),
    [
        ("input_path", "input_py", _SOURCE_MARKER),
        ("shapes_path", "shapes", {"7": {"marker": _SOURCE_MARKER}}),
    ],
)
def test_local_evaluate_conflicting_forms_return_specific_repair(
    feedback_context: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    path_field: str,
    inline_field: str,
    inline_value: object,
) -> None:
    response = _local_cli_error(
        feedback_context,
        {
            "operation": "evaluate",
            "mode": "correctness_only",
            path_field: "scratch/source",
            inline_field: inline_value,
        },
        monkeypatch,
        capsys,
    )

    _assert_evaluate_repair(response, path_field, "mutually_exclusive")
    recovery = json.dumps(response["recovery"])
    assert inline_field in recovery
    assert "mutually exclusive" in response["detail"]


def test_evaluate_repair_schema_describes_only_public_fields_and_canonical_modes() -> None:
    schema = tool_request_schema("gateway-execute", operation="evaluate")

    assert schema is not None
    assert schema["type"] == "object"
    assert schema["required"] == ["operation"]
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == {
        "operation", "mode", "candidate_path", "comparison",
        "input_py", "shapes", "input_path", "shapes_path",
    }
    assert schema["properties"]["operation"] == {"const": "evaluate"}
    mode = schema["properties"]["mode"]
    assert mode["enum"] == ["full", "correctness_only"]
    assert mode["default"] == "full"
    conflicting_pairs = {
        frozenset(clause["not"]["required"])
        for clause in schema["allOf"]
        if "not" in clause
    }
    assert conflicting_pairs == {
        frozenset({"input_py", "input_path"}),
        frozenset({"shapes", "shapes_path"}),
    }
    assert "candidate" not in schema["properties"]
    assert "attempt_id" not in schema["properties"]
    assert "idempotency_key" not in schema["properties"]
    assert "reference_py" not in schema["properties"]
    assert "evaluate_repeats" not in schema["properties"]


def test_remote_evaluate_error_guidance_is_preserved() -> None:
    remote: dict[str, Any] = {
        "error": "invalid_request",
        "detail": "shapes.7: invalid shape parameters",
        "issues": [{"path": "shapes.7", "code": "shape_invalid", "message": "invalid shape"}],
        "request_schema": {"title": "Authoritative Runtime Evaluate schema"},
        "recovery": [{"instruction": "Repair the documented shape parameters"}],
    }

    response = runtime_tools._augment_agent_error(
        "gateway-execute",
        deepcopy(remote),
        detail=remote["detail"],
        operation="evaluate",
    )

    assert response == remote


def test_evaluate_error_adds_local_schema_only_when_remote_schema_is_absent() -> None:
    remote_issues = [{"path": "mode", "code": "literal_error", "message": "invalid mode"}]
    remote_recovery = [{"instruction": "Use the current Runtime mode"}]

    response = runtime_tools._augment_agent_error(
        "gateway-execute",
        {"issues": deepcopy(remote_issues), "recovery": deepcopy(remote_recovery)},
        detail="invalid mode",
        operation="evaluate",
    )

    assert response["issues"] == remote_issues
    assert response["recovery"] == remote_recovery
    assert response["request_schema"] == tool_request_schema(
        "gateway-execute", operation="evaluate",
    )


def test_local_dev_validation_keeps_existing_feedback_without_evaluate_schema(
    feedback_context: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    response = _local_cli_error(
        feedback_context,
        {"operation": "dev", "command": "true", "file_paths": "not-a-list"},
        monkeypatch,
        capsys,
    )

    assert response["detail"] == "dev file_paths must be a list"
    assert response["issues"] == [
        {"path": "$", "code": "invalid_value", "message": "dev file_paths must be a list"},
    ]
    assert "request_schema" not in response
    assert "recovery" not in response
    assert tool_request_schema("gateway-execute", operation="dev") is None
    assert tool_request_schema("gateway-execute") is None
    assert tool_recovery("gateway-execute", operation="dev") is None
