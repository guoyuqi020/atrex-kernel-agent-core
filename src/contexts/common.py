"""Shared strict parsing helpers for Runtime-authored session inputs."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any


def object_value(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{label} must be a JSON object")
    return value


def text_value(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value


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
