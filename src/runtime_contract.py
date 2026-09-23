"""Dynamic projection of the Runtime-owned Session contract for one Agent."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from tool_contracts import tool_request_schema

_CONTRACT_ENV = "ATREX_RUNTIME_CONTRACT_PATH"
_FILES = ("tools.json", "environment.json", "limits.json")
_LOCAL_COMMANDS = (
    "kernel-artifact-read",
    "result-artifact-read",
    "update-direction",
    "list-directions",
    "load-direction",
    "record-experiment",
    "list-experiments",
    "load-experiment",
    "attempt-report",
)


def _object_file(path: Path, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} must be a regular file")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValueError(f"{label} has an unsupported schema")
    return value


def load_live_contract() -> tuple[Path, dict[str, Any]]:
    """Load only the immutable Runtime-injected contract for this Session."""
    raw = os.environ.get(_CONTRACT_ENV)
    if not raw:
        raise ValueError(f"{_CONTRACT_ENV} is missing")
    root = Path(raw)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Runtime contract path must be a real directory")
    workspace = root.parent.parent.resolve()
    if root.resolve() not in {
        workspace / "input/runtime-contract",
        workspace / "input/next-session-contract",
    }:
        raise ValueError("Runtime contract path is outside the fixed workspace location")
    if set(path.name for path in root.iterdir()) != set(_FILES):
        raise ValueError("Runtime contract directory has unexpected entries")
    return workspace, {
        name.removesuffix(".json"): _object_file(root / name, f"Runtime contract {name}")
        for name in _FILES
    }


def project_contract(
    *,
    command: str | None = None,
    operation: str | None = None,
    allow_baseline: bool,
) -> tuple[Path, dict[str, Any]]:
    """Merge live Runtime schemas with this Bundle's local CLI bindings."""
    workspace, live = load_live_contract()
    tools = live["tools"]
    gateway = tools.get("gateway")
    bindings = tools.get("bindings")
    if not isinstance(gateway, dict) or not isinstance(bindings, dict):
        raise ValueError("Runtime tool contract is incomplete")
    operations = gateway.get("operations")
    if not isinstance(operations, dict):
        raise ValueError("Runtime Gateway contract has no operations")

    projected_tools: dict[str, Any] = {
        "runtime-contract": {
            "invocation": (
                "python3 agent/optimizer/src/runtime_tools.py runtime-contract "
                "--output scratch/runtime-contract.json"
            ),
            "request_style": "arguments",
        },
        "gateway-execute": {
            "invocation": (
                "python3 agent/optimizer/src/runtime_tools.py gateway-execute "
                "--request scratch/<request>.json"
            ),
            "request_style": "json_file",
            "operations": operations,
        },
    }
    for name in _LOCAL_COMMANDS:
        schema = tool_request_schema(name, allow_baseline=allow_baseline)
        if schema is None:
            raise ValueError(f"Agent Bundle has no local request schema for {name}")
        projected_tools[name] = {
            "invocation": (
                f"python3 agent/optimizer/src/runtime_tools.py {name} "
                "--request scratch/<request>.json"
            ),
            "request_style": "json_file",
            "request_schema": schema,
        }

    if set(projected_tools) != set(bindings):
        raise ValueError("Agent Bundle commands disagree with the live Runtime bindings")
    if command is not None:
        selected = projected_tools.get(command)
        if selected is None:
            raise ValueError(f"unknown Runtime tool: {command}")
        if operation is not None:
            if command != "gateway-execute":
                raise ValueError("--operation is valid only for gateway-execute")
            selected_operation = operations.get(operation)
            if selected_operation is None:
                raise ValueError(f"unsupported Gateway operation: {operation}")
            selected = {
                **selected,
                "operations": {operation: selected_operation},
            }
        projected_tools = {command: selected}
    elif operation is not None:
        raise ValueError("--operation requires --tool gateway-execute")

    return workspace, {
        "schema_version": 1,
        "tools": projected_tools,
        "environment": live["environment"],
        "limits": live["limits"],
    }
