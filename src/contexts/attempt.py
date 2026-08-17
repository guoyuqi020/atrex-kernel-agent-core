"""Strict Core-side reader for one Runtime-prepared ATREX Attempt."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .common import object_value, text_value, within

_REQUIRED_ENVIRONMENT = (
    "ATREX_ATTEMPT_MANIFEST",
    "ATREX_ATTEMPT_REPORT_PATH",
    "ATREX_GATEWAY_CAPABILITY",
    "ATREX_GATEWAY_PROXY_URL",
    "ATREX_OPTIMIZER_REPOSITORY",
    "ATREX_SESSION_TIMEOUT_SECONDS",
    "ATREX_TOKEN_BUDGET",
    "ATREX_TOKEN_USAGE_REPORT",
)
_EXPECTED_PATHS = {
    "input_kernel": "input/kernel",
    "working_kernel": "work/kernel",
    "evidence": "input/evidence",
    "attempt_evidence": "input/attempt-evidence",
    "agent_problem": "input/agent-problem",
    "optimizer": "agent/optimizer",
}
_MANIFEST_FIELDS = {
    "schema_version",
    "attempt_id",
    "kernel_agent_revision_id",
    "input_kernel_revision_id",
    "input_kernel_digest",
    "epoch_evidence_checkpoint",
    "attempt_evidence_digest",
    "optimizer_digest",
    "dsl",
    "context",
    "paths",
}
_CONTEXT_FIELDS = {
    "campaign_id",
    "lineage_id",
    "epoch_id",
    "epoch_number",
    "branch",
    "attempt_ordinal",
    "operator",
    "hardware_target",
    "evaluation_contract_digest",
    "agent_problem_digest",
}


@dataclass(frozen=True)
class RuntimeAttemptContext:
    workspace: Path
    repository: Path
    manifest_path: Path
    report_path: Path
    token_usage_path: Path
    session_trace_path: Path | None
    gateway_url: str
    gateway_capability: str
    wiki_url: str | None
    wiki_capability: str | None
    token_budget: int
    timeout_seconds: float
    manifest: Mapping[str, Any]

    @classmethod
    def from_environment(cls) -> RuntimeAttemptContext:
        if os.environ.get("ATREX_CORE_PHASE") != "optimization_attempt":
            raise ValueError("Attempt context requires optimization_attempt phase")
        missing = [name for name in _REQUIRED_ENVIRONMENT if not os.environ.get(name)]
        if missing:
            raise ValueError(f"missing Runtime environment: {missing}")
        manifest_value = Path(os.environ["ATREX_ATTEMPT_MANIFEST"])
        if manifest_value.is_symlink() or not manifest_value.is_file():
            raise ValueError("Attempt manifest must be a regular file")
        manifest_path = manifest_value.resolve()
        workspace = manifest_path.parent.resolve()
        manifest = object_value(
            json.loads(manifest_path.read_text(encoding="utf-8")), "Attempt manifest"
        )
        if manifest.get("schema_version") != 5:
            raise ValueError("unsupported Attempt manifest schema_version")
        if set(manifest) != _MANIFEST_FIELDS:
            raise ValueError("Attempt manifest fields do not match the Core protocol")
        paths = object_value(manifest.get("paths"), "Attempt manifest paths")
        if paths != _EXPECTED_PATHS:
            raise ValueError("Attempt manifest paths do not match the Core protocol")
        for key in (
            "attempt_id",
            "kernel_agent_revision_id",
            "input_kernel_revision_id",
            "input_kernel_digest",
            "epoch_evidence_checkpoint",
            "attempt_evidence_digest",
            "optimizer_digest",
            "dsl",
        ):
            text_value(manifest.get(key), f"Attempt manifest {key}")
        context = object_value(manifest.get("context"), "Attempt task context")
        if set(context) != _CONTEXT_FIELDS:
            raise ValueError("Attempt task context fields do not match the Core protocol")
        for key in (
            "campaign_id",
            "lineage_id",
            "epoch_id",
            "branch",
            "operator",
            "hardware_target",
            "evaluation_contract_digest",
            "agent_problem_digest",
        ):
            text_value(context.get(key), f"Attempt task context {key}")
        for key in ("epoch_number", "attempt_ordinal"):
            if not isinstance(context.get(key), int) or context[key] <= 0:
                raise ValueError(f"Attempt task context {key} must be positive")

        repository_value = Path(os.environ["ATREX_OPTIMIZER_REPOSITORY"])
        if repository_value.is_symlink() or not repository_value.is_dir():
            raise ValueError("Optimizer repository must be a real directory")
        repository = repository_value.resolve()
        if repository != workspace / _EXPECTED_PATHS["optimizer"]:
            raise ValueError("Optimizer repository disagrees with Attempt manifest")
        for label, relative in _EXPECTED_PATHS.items():
            unresolved = workspace / relative
            if unresolved.is_symlink():
                raise ValueError(f"Attempt workspace path must not be a link: {relative}")
            path = within(workspace, relative, label)
            if not path.is_dir():
                raise ValueError(f"Attempt workspace path is missing: {relative}")

        report_path = Path(os.environ["ATREX_ATTEMPT_REPORT_PATH"]).resolve()
        token_path = Path(os.environ["ATREX_TOKEN_USAGE_REPORT"]).resolve()
        if not report_path.is_relative_to(workspace / "scratch"):
            raise ValueError("Attempt report path must be under scratch")
        if not token_path.is_relative_to(workspace / "scratch"):
            raise ValueError("Token usage path must be under scratch")
        trace_value = os.environ.get("ATREX_SESSION_TRACE_PATH")
        trace_path = Path(trace_value).resolve() if trace_value else None
        if trace_path is not None and not (
            trace_path.is_relative_to(workspace / "sessions")
            or trace_path.is_relative_to(workspace / "scratch")
        ):
            raise ValueError("Session trace path must be under sessions or scratch")

        try:
            budget = int(os.environ["ATREX_TOKEN_BUDGET"])
            timeout = float(os.environ["ATREX_SESSION_TIMEOUT_SECONDS"])
        except ValueError as error:
            raise ValueError("Runtime budget and timeout must be numeric") from error
        if budget <= 0 or timeout <= 0:
            raise ValueError("Runtime budget and timeout must be positive")
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
            wiki_url=wiki_url,
            wiki_capability=wiki_capability,
            token_budget=budget,
            timeout_seconds=timeout,
            manifest=manifest,
        )

    @property
    def attempt_id(self) -> str:
        return str(self.manifest["attempt_id"])

    @property
    def working_kernel(self) -> Path:
        return self.workspace / _EXPECTED_PATHS["working_kernel"]
