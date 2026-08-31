"""Strict Core-side reader for one Runtime-prepared ATREX Attempt."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .common import json_object_file, object_value, text_value, within

_REQUIRED_ENVIRONMENT = (
    "ATREX_ATTEMPT_MANIFEST",
    "ATREX_ATTEMPT_REPORT_PATH",
    "ATREX_EVIDENCE_PROMPT_PATH",
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
    "evidence": "input/evidence",
    "agent_problem": ".runtime/agent-problem.json",
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
}
_CONTEXT_FIELDS = {
    "campaign_id",
    "lineage_id",
    "epoch_id",
    "epoch_number",
    "attempt_ordinal",
    "operator",
    "hardware_target",
    "evaluation_contract_digest",
    "agent_problem_digest",
}
_EVIDENCE_VIEW_FIELDS = {
    "schema_version",
    "role",
    "lineage_checkpoint",
    "prompt_fragment_sha256",
    "through_completed_epoch",
    "current_epoch",
    "visibility",
}
_MAX_EVIDENCE_PROMPT_BYTES = 32 * 1024
_MANIFEST_RELATIVE_PATH = Path(".runtime/attempt.json")


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
    evidence_prompt: str
    agent_problem: Mapping[str, Any]
    wiki_url: str | None
    wiki_capability: str | None
    usage_unit: str
    usage_budget: float
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
        workspace = manifest_path.parent.parent.resolve()
        if manifest_path != workspace / _MANIFEST_RELATIVE_PATH:
            raise ValueError("Attempt manifest must use the internal Runtime control path")
        manifest = object_value(
            json.loads(manifest_path.read_text(encoding="utf-8")), "Attempt manifest"
        )
        if manifest.get("schema_version") != 9:
            raise ValueError("unsupported Attempt manifest schema_version")
        if set(manifest) != _MANIFEST_FIELDS:
            raise ValueError("Attempt manifest fields do not match the Core protocol")
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
            if label == "agent_problem":
                if not path.is_file():
                    raise ValueError(f"Attempt workspace path is missing: {relative}")
            elif not path.is_dir():
                raise ValueError(f"Attempt workspace path is missing: {relative}")
        agent_problem = json_object_file(
            workspace / _EXPECTED_PATHS["agent_problem"],
            "public operator contract",
        )

        evidence_manifest_path = workspace / ".runtime/evidence-manifest.json"
        if evidence_manifest_path.is_symlink() or not evidence_manifest_path.is_file():
            raise ValueError("Evidence view manifest must be a regular file")
        evidence_view = object_value(
            json.loads(evidence_manifest_path.read_text(encoding="utf-8")),
            "Evidence view manifest",
        )
        if set(evidence_view) != _EVIDENCE_VIEW_FIELDS:
            raise ValueError("Evidence view manifest fields do not match the Core protocol")
        current_epoch = object_value(evidence_view.get("current_epoch"), "current Evidence Epoch")
        visibility = object_value(evidence_view.get("visibility"), "Evidence visibility")
        expected_visibility = {
            "completed_epochs": "promoted_lineage",
            "current_attempts_before": context["attempt_ordinal"],
        }
        trajectory_ordinal = visibility.get("current_trajectory_ordinal")
        if (
            not isinstance(trajectory_ordinal, int)
            or isinstance(trajectory_ordinal, bool)
            or trajectory_ordinal <= 0
        ):
            raise ValueError("Evidence visibility requires a positive current Trajectory")
        expected_visibility["current_trajectory_ordinal"] = trajectory_ordinal
        if (
            evidence_view.get("schema_version") != 1
            or evidence_view.get("role") != "optimizer"
            or evidence_view.get("lineage_checkpoint") != manifest["epoch_evidence_checkpoint"]
            or evidence_view.get("through_completed_epoch") != context["epoch_number"] - 1
            or current_epoch
            != {
                "number": context["epoch_number"],
                "snapshot_digest": manifest["attempt_evidence_digest"],
                "status": "in_progress",
                "trigger": None,
            }
            or visibility != expected_visibility
        ):
            raise ValueError("Evidence view disagrees with the trusted Attempt manifest")
        prompt_input = Path(os.environ["ATREX_EVIDENCE_PROMPT_PATH"])
        if prompt_input.is_symlink() or not prompt_input.is_file():
            raise ValueError("Evidence Prompt Fragment must be a regular file")
        prompt_path = prompt_input.resolve()
        expected_prompt_path = workspace / ".runtime/evidence-instructions.md"
        if prompt_path != expected_prompt_path:
            raise ValueError("Evidence Prompt Fragment path disagrees with the Evidence view")
        prompt_bytes = prompt_path.read_bytes()
        if not prompt_bytes or len(prompt_bytes) > _MAX_EVIDENCE_PROMPT_BYTES:
            raise ValueError("Evidence Prompt Fragment is empty or exceeds its byte limit")
        if hashlib.sha256(prompt_bytes).hexdigest() != evidence_view.get("prompt_fragment_sha256"):
            raise ValueError("Evidence Prompt Fragment digest disagrees with the manifest")
        try:
            evidence_prompt = prompt_bytes.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError("Evidence Prompt Fragment must be UTF-8") from error
        current_attempts = (
            workspace
            / _EXPECTED_PATHS["evidence"]
            / "epochs"
            / f"{int(context['epoch_number']):08d}"
            / "trajectories"
            / f"{trajectory_ordinal:08d}"
            / "attempts"
        )
        if current_attempts.is_symlink() or not current_attempts.is_dir():
            raise ValueError("Evidence view is missing its visible current Attempts")

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
            evidence_prompt=evidence_prompt,
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
        return str(self.manifest["attempt_id"])

    @property
    def working_kernel(self) -> Path:
        return self.workspace / _EXPECTED_PATHS["working_kernel"]
