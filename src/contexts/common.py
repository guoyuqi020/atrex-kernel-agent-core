"""Shared strict parsing helpers for Runtime-authored session inputs."""

from __future__ import annotations

import json
import math
from pathlib import Path, PurePosixPath
from typing import Any


def object_value(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{label} must be a JSON object")
    return value


def json_object_file(path: Path, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} must be a regular file")
    try:
        value: object = json.loads(path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} must contain valid JSON") from error
    return object_value(value, label)


def text_value(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value


def correctness_policy_value(value: object) -> dict[str, Any]:
    """Validate the exact Agent-safe correctness projection supplied by the controller."""
    policy = object_value(value, "Correctness policy")
    expected = {"comparison", "formula", "default_tolerance", "output_tolerances"}
    if set(policy) != expected:
        raise ValueError("Correctness policy fields do not match the Agent protocol")
    if policy["comparison"] != "elementwise" or policy["formula"] != (
        "abs(candidate - reference) <= atol + rtol * abs(reference)"
    ):
        raise ValueError("Correctness policy comparison is unsupported")

    def tolerance(raw: object, label: str) -> dict[str, float]:
        item = object_value(raw, label)
        if set(item) != {"atol", "rtol"}:
            raise ValueError(f"{label} fields do not match the Agent protocol")
        result: dict[str, float] = {}
        for key in ("atol", "rtol"):
            number = item[key]
            if (
                not isinstance(number, int | float)
                or isinstance(number, bool)
                or not math.isfinite(number)
                or number < 0
            ):
                raise ValueError(f"{label} {key} must be a finite non-negative number")
            result[key] = float(number)
        return result

    defaults = tolerance(policy["default_tolerance"], "Default correctness tolerance")
    raw_outputs = object_value(policy["output_tolerances"], "Output correctness tolerances")
    outputs = {
        text_value(name, "Correctness output name"): tolerance(
            raw, f"Correctness tolerance for {name}"
        )
        for name, raw in raw_outputs.items()
    }
    return {
        "comparison": policy["comparison"],
        "formula": policy["formula"],
        "default_tolerance": defaults,
        "output_tolerances": outputs,
    }


def safe_relative(value: str, label: str) -> PurePosixPath:
    relative = PurePosixPath(value)
    if (
        relative.is_absolute()
        or not relative.parts
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise ValueError(f"{label} must be a safe relative path")
    return relative


def within(root: Path, value: str, label: str) -> Path:
    relative = safe_relative(value, label)
    path = root.joinpath(*relative.parts).resolve()
    if not path.is_relative_to(root):
        raise ValueError(f"{label} escapes the session workspace")
    return path
