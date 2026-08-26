"""Strict Core-side reader for a Runtime-managed lineage bootstrap session."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .common import json_object_file, object_value, text_value

_REQUIRED_ENVIRONMENT = (
    "ATREX_LINEAGE_BOOTSTRAP_MANIFEST",
    "ATREX_ATTEMPT_REPORT_PATH",
    "ATREX_GATEWAY_CAPABILITY",
    "ATREX_GATEWAY_PROXY_URL",
    "ATREX_OPTIMIZER_REPOSITORY",
    "ATREX_SESSION_TIMEOUT_SECONDS",
    "ATREX_USAGE_BUDGET",
    "ATREX_USAGE_UNIT",
    "ATREX_TOKEN_USAGE_REPORT",
)
_EXPECTED_PATHS = {
    "input_kernel": "input/kernel",
    "working_kernel": "work/kernel",
    "agent_problem": ".runtime/agent-problem.json",
    "optimizer": "agent/optimizer",
    "reference": "reference",
}
_MANIFEST_FIELDS = {
    "schema_version",
    "bootstrap_attempt_id",
    "lineage_id",
    "kernel_agent_revision_id",
    "input_kernel_digest",
    "optimizer_digest",
    "evaluation_contract_digest",
    "agent_problem_digest",
    "dsl",
    "operator",
    "hardware_target",
    "paths",
}
_MANIFEST_RELATIVE_PATH = Path(".runtime/lineage-bootstrap.json")


@dataclass(frozen=True)
class RuntimeLineageBootstrapContext:
    """Immutable paths and authority injected for one framework-baseline session."""

    workspace: Path
    repository: Path
    manifest_path: Path
    report_path: Path
    token_usage_path: Path
    session_trace_path: Path | None
    gateway_url: str
    gateway_capability: str
    agent_problem: Mapping[str, Any]
    wiki_url: str | None
    wiki_capability: str | None
    usage_unit: str
    usage_budget: float
    timeout_seconds: float
    manifest: Mapping[str, Any]

    @classmethod
    def from_environment(cls) -> RuntimeLineageBootstrapContext:
        if os.environ.get("ATREX_CORE_PHASE") != "framework_baseline":
            raise ValueError("lineage bootstrap requires framework_baseline phase")
        missing = [name for name in _REQUIRED_ENVIRONMENT if not os.environ.get(name)]
        if missing:
            raise ValueError(f"missing Runtime environment: {missing}")
        manifest_value = Path(os.environ["ATREX_LINEAGE_BOOTSTRAP_MANIFEST"])
        if manifest_value.is_symlink() or not manifest_value.is_file():
            raise ValueError("lineage bootstrap manifest must be a regular file")
        manifest_path = manifest_value.resolve()
        workspace = manifest_path.parent.parent.resolve()
        if manifest_path != workspace / _MANIFEST_RELATIVE_PATH:
            raise ValueError("lineage bootstrap manifest must use the internal control path")
        manifest = object_value(
            json.loads(manifest_path.read_text(encoding="utf-8")),
            "lineage bootstrap manifest",
        )
        if manifest.get("schema_version") != 2:
            raise ValueError("unsupported lineage bootstrap manifest schema_version")
        if set(manifest) != _MANIFEST_FIELDS:
            raise ValueError("lineage bootstrap manifest fields do not match the Core protocol")
        paths = object_value(manifest.get("paths"), "lineage bootstrap paths")
        if paths != _EXPECTED_PATHS:
            raise ValueError("lineage bootstrap paths do not match the Core protocol")
        for key in (
            "bootstrap_attempt_id",
            "lineage_id",
            "kernel_agent_revision_id",
            "input_kernel_digest",
            "optimizer_digest",
            "evaluation_contract_digest",
            "agent_problem_digest",
            "dsl",
            "operator",
            "hardware_target",
        ):
            text_value(manifest.get(key), f"lineage bootstrap {key}")

        repository_value = Path(os.environ["ATREX_OPTIMIZER_REPOSITORY"])
        if repository_value.is_symlink() or not repository_value.is_dir():
            raise ValueError("Optimizer repository must be a real directory")
        repository = repository_value.resolve()
        if repository != workspace / _EXPECTED_PATHS["optimizer"]:
            raise ValueError("Optimizer repository disagrees with lineage bootstrap manifest")
        for relative in _EXPECTED_PATHS.values():
            path = workspace / relative
            if not path.exists() or path.is_symlink():
                raise ValueError(f"lineage bootstrap workspace path is missing: {relative}")
        agent_problem = json_object_file(
            workspace / _EXPECTED_PATHS["agent_problem"],
            "public operator contract",
        )

        report_path = Path(os.environ["ATREX_ATTEMPT_REPORT_PATH"]).resolve()
        token_path = Path(os.environ["ATREX_TOKEN_USAGE_REPORT"]).resolve()
        if not report_path.is_relative_to(workspace / "scratch"):
            raise ValueError("lineage bootstrap report must be under scratch")
        if not token_path.is_relative_to(workspace / "scratch"):
            raise ValueError("Token usage path must be under scratch")
        trace_value = os.environ.get("ATREX_SESSION_TRACE_PATH")
        trace_path = Path(trace_value).resolve() if trace_value else None
        if trace_path is not None and not trace_path.is_relative_to(workspace / "sessions"):
            raise ValueError("Session trace path must be under sessions")
        try:
            budget = float(os.environ["ATREX_USAGE_BUDGET"])
            timeout = float(os.environ["ATREX_SESSION_TIMEOUT_SECONDS"])
        except ValueError as error:
            raise ValueError("Runtime budget and timeout must be numeric") from error
        if budget <= 0 or timeout <= 0:
            raise ValueError("Runtime budget and timeout must be positive")
        usage_unit = os.environ["ATREX_USAGE_UNIT"]
        if usage_unit not in {"provider_tokens", "credits"}:
            raise ValueError("Runtime usage unit is unsupported")
        wiki_url = os.environ.get("ATREX_WIKI_PROXY_URL")
        wiki_capability = os.environ.get("ATREX_WIKI_CAPABILITY")
        if (wiki_url is None) != (wiki_capability is None):
            raise ValueError("Wiki endpoint and capability must be provided together")
        return cls(
            workspace=workspace,
            repository=repository,
            manifest_path=manifest_path,
            report_path=report_path,
            token_usage_path=token_path,
            session_trace_path=trace_path,
            gateway_url=text_value(os.environ["ATREX_GATEWAY_PROXY_URL"], "Gateway URL"),
            gateway_capability=text_value(
                os.environ["ATREX_GATEWAY_CAPABILITY"], "Gateway capability"
            ),
            agent_problem=agent_problem,
            wiki_url=wiki_url,
            wiki_capability=wiki_capability,
            usage_unit=usage_unit,
            usage_budget=budget,
            timeout_seconds=timeout,
            manifest=manifest,
        )

    @property
    def attempt_id(self) -> str:
        """Expose the Runtime operation identity used by the Gateway protocol."""
        return str(self.manifest["bootstrap_attempt_id"])

    @property
    def working_kernel(self) -> Path:
        return self.workspace / _EXPECTED_PATHS["working_kernel"]

    @property
    def reference(self) -> Path:
        return self.workspace / _EXPECTED_PATHS["reference"]
