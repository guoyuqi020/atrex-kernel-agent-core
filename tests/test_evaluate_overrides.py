from __future__ import annotations

import json
import os
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import runtime_tools
from runtime_tools import gateway_execute

_INPUT = "# 自定义输入\ndef _make_inputs(n):\n    return {'n': n}\n"
_SHAPES = {"0": {"init_kwargs": None, "input_kwargs": {"n": 128}}}


@pytest.fixture
def context(tmp_path: Path) -> Any:
    kernel = tmp_path / "work/kernel"
    kernel.mkdir(parents=True)
    (kernel / "kernel.py").write_text("def kernel(): pass\n", encoding="utf-8")
    (tmp_path / "scratch").mkdir()
    return SimpleNamespace(
        workspace=tmp_path,
        working_kernel=kernel,
        attempt_id="attempt_" + "a" * 32,
        gateway_url="http://runtime.invalid",
        gateway_capability="test-capability",
    )


@pytest.fixture
def gateway_server(context: Any) -> Iterator[list[dict[str, Any]]]:
    requests: list[dict[str, Any]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            assert self.path == "/v1/operations"
            assert self.headers["Authorization"] == "Bearer test-capability"
            requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            body = json.dumps(
                {
                    "schema_version": 2,
                    "operation": "evaluate",
                    "status": "completed",
                    "kernel_artifact_digest": "sha256:" + "b" * 64,
                    "result_artifact_digest": "sha256:" + "d" * 64,
                    "evaluation": None,
                    "result": {
                        "correct": True,
                        "correctness": {"status": "PASS"},
                        "failures": [],
                        "mode": "correctness_only",
                        "input_scope": "custom",
                    },
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            pass

    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        context.gateway_url = f"http://127.0.0.1:{server.server_port}"
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
        thread.start()
        try:
            yield requests
        finally:
            server.shutdown()
            thread.join(timeout=5)


def test_evaluate_expands_files_and_preserves_exploratory_result(
    context: Any, gateway_server: list[dict[str, Any]]
) -> None:
    (context.workspace / "scratch/input.py").write_text(_INPUT, encoding="utf-8")
    (context.workspace / "scratch/shapes.json").write_text(json.dumps(_SHAPES), encoding="utf-8")
    request = {
        "operation": "evaluate",
        "mode": "correctness_only",
        "input_path": "scratch/input.py",
        "shapes_path": "scratch/shapes.json",
    }

    response = gateway_execute(context, request)

    sent = gateway_server[0]
    assert sent["input_py"] == _INPUT
    assert sent["shapes"] == _SHAPES
    assert sent["mode"] == "correctness_only"
    assert sent["candidate"]["files"][0]["path"] == "kernel.py"
    assert "input_path" not in sent and "shapes_path" not in sent
    assert request["input_path"] == "scratch/input.py"
    assert response["kernel_artifact_digest"] == "sha256:" + "b" * 64
    assert "kernel_trial_id" not in response
    assert response["result_artifact_digest"] == "sha256:" + "d" * 64
    assert response["result"] == {
        "correct": True,
        "correctness": {"status": "PASS"},
        "failures": [],
        "mode": "correctness_only",
        "input_scope": "custom",
    }
    assert "evaluation" not in response


def test_evaluate_idempotency_tracks_contents_not_paths(
    context: Any, gateway_server: list[dict[str, Any]]
) -> None:
    source = context.workspace / "scratch/input.py"
    source.write_text(_INPUT, encoding="utf-8")
    shapes = context.workspace / "scratch/shapes.json"
    shapes.write_text(json.dumps(_SHAPES), encoding="utf-8")
    paths = {
        "operation": "evaluate",
        "mode": "correctness_only",
        "input_path": "scratch/input.py",
        "shapes_path": "scratch/shapes.json",
    }
    gateway_execute(context, paths)
    gateway_execute(context, paths)
    gateway_execute(
        context,
        {
            "operation": "evaluate",
            "mode": "correctness_only",
            "input_py": _INPUT,
            "shapes": _SHAPES,
        },
    )
    source.rename(context.workspace / "scratch/renamed.py")
    paths["input_path"] = "scratch/renamed.py"
    shapes.write_text(json.dumps(_SHAPES, indent=2), encoding="utf-8")
    gateway_execute(context, paths)
    assert len({request["idempotency_key"] for request in gateway_server}) == 1

    (context.workspace / paths["input_path"]).write_text(_INPUT + "# changed\n", encoding="utf-8")
    gateway_execute(context, paths)
    shapes.write_text(json.dumps({"0": {"input_kwargs": {"n": 256}}}), encoding="utf-8")
    gateway_execute(context, paths)
    gateway_execute(context, {**paths, "mode": "full"})
    assert len({request["idempotency_key"] for request in gateway_server}) == 4


@pytest.mark.parametrize(
    "fields",
    [{}, {"input_py": _INPUT}, {"shapes": _SHAPES}, {"mode": "correctness_only"}],
)
def test_evaluate_forwards_only_requested_overrides(
    context: Any, gateway_server: list[dict[str, Any]], fields: dict[str, Any]
) -> None:
    gateway_execute(context, {"operation": "evaluate", **fields})
    sent = gateway_server[0]
    assert {name: sent[name] for name in ("input_py", "shapes", "mode") if name in sent} == fields


@pytest.mark.parametrize(
    "path_field,inline_field,content,expected",
    [
        ("input_path", "input_py", _INPUT, _INPUT),
        ("shapes_path", "shapes", json.dumps(_SHAPES), _SHAPES),
    ],
)
def test_evaluate_accepts_each_path_override_independently(
    context: Any,
    gateway_server: list[dict[str, Any]],
    path_field: str,
    inline_field: str,
    content: str,
    expected: Any,
) -> None:
    (context.workspace / "scratch/override").write_text(content, encoding="utf-8")
    gateway_execute(context, {"operation": "evaluate", path_field: "scratch/override"})
    sent = gateway_server[0]
    assert {name: sent[name] for name in ("input_py", "shapes", "mode") if name in sent} == {
        inline_field: expected
    }


@pytest.mark.parametrize(
    "path_field,inline_field", [("input_path", "input_py"), ("shapes_path", "shapes")]
)
def test_evaluate_rejects_inline_and_path_conflicts(
    context: Any, path_field: str, inline_field: str
) -> None:
    with pytest.raises(ValueError, match="mutually exclusive"):
        gateway_execute(
            context, {"operation": "evaluate", path_field: "missing", inline_field: None}
        )


@pytest.mark.parametrize("field", ["input_path", "shapes_path"])
@pytest.mark.parametrize(
    "path",
    [
        None,
        1,
        "",
        "/tmp/input.py",
        "../input.py",
        "./scratch/a",
        "scratch/../a",
        "scratch//a",
        "scratch/a/",
        "scratch/\x00",
    ],
)
def test_evaluate_rejects_unsafe_paths(context: Any, field: str, path: Any) -> None:
    with pytest.raises(ValueError, match="workspace-relative path"):
        gateway_execute(context, {"operation": "evaluate", field: path})


@pytest.mark.parametrize("kind", ["missing", "directory", "fifo", "symlink", "parent_symlink"])
def test_evaluate_rejects_nonregular_or_linked_files(context: Any, kind: str) -> None:
    source = context.workspace / "scratch/input.py"
    if kind == "directory":
        source.mkdir()
    elif kind == "fifo":
        os.mkfifo(source)
    elif kind in {"symlink", "parent_symlink"}:
        outside = context.workspace.parent / "outside"
        outside.mkdir(exist_ok=True)
        (outside / "input.py").write_text(_INPUT, encoding="utf-8")
        if kind == "symlink":
            source.symlink_to(outside / "input.py")
        else:
            (context.workspace / "scratch/linked").symlink_to(outside, target_is_directory=True)
            source = context.workspace / "scratch/linked/input.py"
    with pytest.raises(ValueError, match="regular file"):
        gateway_execute(
            context,
            {"operation": "evaluate", "input_path": str(source.relative_to(context.workspace))},
        )


def test_evaluate_does_not_read_runtime_control_files(context: Any) -> None:
    with pytest.raises(ValueError, match="must not read Runtime control files"):
        gateway_execute(context, {"operation": "evaluate", "input_path": ".runtime/attempt.json"})


@pytest.mark.parametrize(
    "field,limit",
    [
        ("input_path", runtime_tools._MAX_EVALUATE_INPUT_BYTES),
        ("shapes_path", runtime_tools._MAX_EVALUATE_SHAPES_BYTES),
    ],
)
def test_evaluate_rejects_oversize_files(context: Any, field: str, limit: int) -> None:
    (context.workspace / "scratch/override").write_bytes(b" " * (limit + 1))
    with pytest.raises(ValueError, match="exceeds its byte limit"):
        gateway_execute(context, {"operation": "evaluate", field: "scratch/override"})


@pytest.mark.parametrize("field", ["input_path", "shapes_path"])
def test_evaluate_requires_utf8_files(context: Any, field: str) -> None:
    (context.workspace / "scratch/override").write_bytes(b"\xff\xfe")
    with pytest.raises(ValueError, match="UTF-8"):
        gateway_execute(context, {"operation": "evaluate", field: "scratch/override"})


@pytest.mark.parametrize("content", ["not JSON", "[]", '"text"', "null"])
def test_evaluate_requires_shapes_json_object(context: Any, content: str) -> None:
    (context.workspace / "scratch/shapes.json").write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match="JSON"):
        gateway_execute(context, {"operation": "evaluate", "shapes_path": "scratch/shapes.json"})
