"""Strict Core reader for a Runtime-managed problem-generalization session."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .common import object_value, text_value

_REQUIRED_ENVIRONMENT = (
    "ATREX_AGENT_PROBLEM_OUTPUT",
    "ATREX_OPTIMIZER_REPOSITORY",
    "ATREX_PROBLEM_GENERALIZATION_MANIFEST",
    "ATREX_SESSION_TIMEOUT_SECONDS",
    "ATREX_TOKEN_BUDGET",
    "ATREX_TOKEN_USAGE_REPORT",
)
_EXPECTED_PATHS = {
    "private_inputs": "input/private",
    "output": "work/output",
    "optimizer": "agent/optimizer",
}
_MANIFEST_FIELDS = {
    "schema_version",
    "generalization_id",
    "optimizer_digest",
    "evaluation_contract_digest",
    "dsl",
    "operator",
    "hardware_target",
    "paths",
}


@dataclass(frozen=True)
class RuntimeProblemGeneralizationContext:
    workspace: Path
    repository: Path
    manifest_path: Path
    output_path: Path
    token_usage_path: Path
    session_trace_path: Path | None
    token_budget: int
    timeout_seconds: float
    manifest: Mapping[str, Any]

    @classmethod
    def from_environment(cls) -> RuntimeProblemGeneralizationContext:
        if os.environ.get("ATREX_CORE_PHASE") != "problem_generalization":
            raise ValueError("problem generalization requires its explicit Core phase")
        missing = [name for name in _REQUIRED_ENVIRONMENT if not os.environ.get(name)]
        if missing:
            raise ValueError(f"missing Runtime environment: {missing}")
        manifest_value = Path(os.environ["ATREX_PROBLEM_GENERALIZATION_MANIFEST"])
        if manifest_value.is_symlink() or not manifest_value.is_file():
            raise ValueError("problem generalization manifest must be a regular file")
        manifest_path = manifest_value.resolve()
        workspace = manifest_path.parent.resolve()
        manifest = object_value(
            json.loads(manifest_path.read_text(encoding="utf-8")),
            "problem generalization manifest",
        )
        if manifest.get("schema_version") != 1:
            raise ValueError("unsupported problem generalization manifest schema_version")
        if set(manifest) != _MANIFEST_FIELDS:
            raise ValueError(
                "problem generalization manifest fields do not match the Core protocol"
            )
        if object_value(manifest.get("paths"), "problem generalization paths") != _EXPECTED_PATHS:
            raise ValueError("problem generalization paths do not match the Core protocol")
        for key in (
            "generalization_id",
            "optimizer_digest",
            "evaluation_contract_digest",
            "dsl",
            "operator",
            "hardware_target",
        ):
            text_value(manifest.get(key), f"problem generalization {key}")
        repository_value = Path(os.environ["ATREX_OPTIMIZER_REPOSITORY"])
        if repository_value.is_symlink() or not repository_value.is_dir():
            raise ValueError("Optimizer repository must be a real directory")
        repository = repository_value.resolve()
        if repository != workspace / _EXPECTED_PATHS["optimizer"]:
            raise ValueError("Optimizer repository disagrees with generalization manifest")
        for relative in _EXPECTED_PATHS.values():
            path = workspace / relative
            if not path.exists() or path.is_symlink():
                raise ValueError(f"problem generalization path is missing: {relative}")
        output_path = Path(os.environ["ATREX_AGENT_PROBLEM_OUTPUT"]).resolve()
        if output_path != workspace / "work/output/agent_problem.json":
            raise ValueError("Agent Problem output path is not the protocol path")
        token_path = Path(os.environ["ATREX_TOKEN_USAGE_REPORT"]).resolve()
        if not token_path.is_relative_to(workspace / "scratch"):
            raise ValueError("Token usage path must be under scratch")
        trace_value = os.environ.get("ATREX_SESSION_TRACE_PATH")
        trace_path = Path(trace_value).resolve() if trace_value else None
        if trace_path is not None and not trace_path.is_relative_to(workspace / "sessions"):
            raise ValueError("Session trace path must be under sessions")
        try:
            budget = int(os.environ["ATREX_TOKEN_BUDGET"])
            timeout = float(os.environ["ATREX_SESSION_TIMEOUT_SECONDS"])
        except ValueError as error:
            raise ValueError("Runtime budget and timeout must be numeric") from error
        if budget <= 0 or timeout <= 0:
            raise ValueError("Runtime budget and timeout must be positive")
        return cls(
            workspace,
            repository,
            manifest_path,
            output_path,
            token_path,
            trace_path,
            budget,
            timeout,
            manifest,
        )
