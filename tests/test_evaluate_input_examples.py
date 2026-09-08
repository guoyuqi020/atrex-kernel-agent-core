"""The Agent's paired custom-input examples agree with the documented public ABI."""

from __future__ import annotations

import inspect
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tool_contracts import tool_recovery, tool_request_schema


def _example(prompt: str, path: str, language: str) -> str:
    match = re.search(
        rf"Contents of `{re.escape(path)}`:\n\n```{language}\n(.*?)\n```", prompt, re.DOTALL
    )
    assert match is not None, f"Missing complete {path} example"
    return match.group(1)


@dataclass(frozen=True)
class _Tensor:
    shape: tuple[int, ...]
    device: str
    dtype: str


def test_paired_input_examples_match_generator_and_forward_without_gpu(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prompt = (Path(__file__).resolve().parents[1] / "prompts/attempt-tools.md").read_text()
    source = _example(prompt, "scratch/custom-input.py", "python")
    shapes = json.loads(_example(prompt, "scratch/custom-shapes.json", "json"))
    assert shapes == {
        "0": {"input_kwargs": {"num_elements": 1024}, "init_kwargs": None},
        "1": {"input_kwargs": {"num_elements": 4097}, "init_kwargs": None},
    }

    def randn(shape: tuple[int, ...], *, device: str, dtype: str) -> _Tensor:
        return _Tensor(shape, device, dtype)

    def randn_like(tensor: _Tensor) -> _Tensor:
        return _Tensor(tensor.shape, tensor.device, tensor.dtype)

    torch = ModuleType("torch")
    monkeypatch.setattr(torch, "Tensor", _Tensor, raising=False)
    monkeypatch.setattr(torch, "float32", "float32", raising=False)
    monkeypatch.setattr(torch, "randn", randn, raising=False)
    monkeypatch.setattr(torch, "randn_like", randn_like, raising=False)
    monkeypatch.setitem(sys.modules, "torch", torch)
    namespace: dict[str, Any] = {}
    exec(compile(source, "scratch/custom-input.py", "exec"), namespace)
    make_inputs = namespace["_make_inputs"]
    assert list(inspect.signature(make_inputs).parameters) == ["num_elements"]

    class Model:
        def forward(self, left: _Tensor, right: _Tensor) -> tuple[_Tensor, _Tensor]:
            return left, right

    for shape_id, record in shapes.items():
        int(shape_id)
        inspect.signature(make_inputs).bind(**record["input_kwargs"])
        inputs = make_inputs(**record["input_kwargs"])
        model = Model(**(record["init_kwargs"] or {}))
        inspect.signature(model.forward).bind(**inputs)
        left, right = model.forward(**inputs)
        assert set(inputs) == {"left", "right"}
        expected = _Tensor((record["input_kwargs"]["num_elements"],), "cuda", "float32")
        assert left == right == expected
        assert left is not right
    assert "seed" not in source
    assert "not Tensor definitions" in prompt
    assert "Prefer supplying both custom files together" in prompt
    assert "not private evaluator cases" in prompt


def test_evaluate_schema_and_recovery_explain_input_and_shape_mapping() -> None:
    schema = tool_request_schema("gateway-execute", operation="evaluate")
    assert schema is not None
    source_description = schema["properties"]["input_py"]["description"]
    shape_description = schema["properties"]["shapes"]["description"]
    assert "_make_inputs(**input_kwargs)" in source_description
    assert "Model.forward argument names" in source_description
    assert "not Tensor definitions" in shape_description
    assert "init_kwargs" in shape_description
    assert "Model constructor arguments (null or {}" in shape_description
    assert "_make_inputs" in schema["properties"]["input_path"]["description"]
    assert "input_kwargs" in schema["properties"]["shapes_path"]["description"]
    assert "init_kwargs" in schema["properties"]["shapes_path"]["description"]
    for field in ("input_py", "shapes", "shapes_path"):
        recovery = tool_recovery(
            "gateway-execute",
            operation="evaluate",
            detail=f"evaluate {field} must contain a JSON object",
        )
        assert recovery is not None
        instructions = json.dumps(recovery)
        assert "_make_inputs" in instructions
        assert "input_kwargs" in instructions
        assert len(instructions) < 4000
