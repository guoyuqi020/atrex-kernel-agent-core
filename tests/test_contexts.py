from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from contexts.attempt import RuntimeAttemptContext
from contexts.lineage_bootstrap import RuntimeLineageBootstrapContext

EVIDENCE_PROMPT = "# Evidence input\n\nInjected by the trusted controller.\n"
EVIDENCE_PROMPT_SHA256 = hashlib.sha256(EVIDENCE_PROMPT.encode()).hexdigest()


def _workspace(root: Path) -> tuple[Path, dict[str, object]]:
    for relative in (
        "input/kernel",
        "input/evidence",
        ".runtime",
        "agent/optimizer",
        "work/kernel",
        "reference",
        "sessions",
        "scratch",
    ):
        (root / relative).mkdir(parents=True, exist_ok=True)
    (root / ".runtime/agent-problem.json").write_text(
        json.dumps(
            {
                "schema_version": "atrex.agent_problem.v1",
                "objective": "implement vector addition while exact cases remain private",
            }
        ),
        encoding="utf-8",
    )
    manifest = {
        "schema_version": 9,
        "attempt_id": "attempt_0123456789abcdef0123456789abcdef",
        "kernel_agent_revision_id": "agent-revision",
        "input_kernel_revision_id": "kernel-revision",
        "input_kernel_digest": "sha256:kernel",
        "epoch_evidence_checkpoint": "sha256:epoch",
        "attempt_evidence_digest": "sha256:attempt",
        "optimizer_digest": "sha256:optimizer",
        "dsl": "triton",
        "context": {
            "campaign_id": "campaign",
            "lineage_id": "lineage",
            "epoch_id": "epoch",
            "epoch_number": 1,
            "attempt_ordinal": 1,
            "operator": "vector_add",
            "hardware_target": "h100",
            "evaluation_contract_digest": "sha256:contract",
            "agent_problem_digest": "sha256:problem",
        },
    }
    current_attempts = (
        root
        / "input/evidence/epochs/00000001/trajectories/00000001/attempts"
    )
    current_attempts.mkdir(parents=True)
    (root / ".runtime/evidence-instructions.md").write_text(EVIDENCE_PROMPT, encoding="utf-8")
    (root / ".runtime/evidence-manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "role": "optimizer",
                "lineage_checkpoint": "sha256:epoch",
                "prompt_fragment_sha256": EVIDENCE_PROMPT_SHA256,
                "through_completed_epoch": 0,
                "current_epoch": {
                    "number": 1,
                    "status": "in_progress",
                    "snapshot_digest": "sha256:attempt",
                    "trigger": None,
                },
                "visibility": {
                    "completed_epochs": "promoted_lineage",
                    "current_attempts_before": 1,
                    "current_trajectory_ordinal": 1,
                },
            }
        ),
        encoding="utf-8",
    )
    path = root / ".runtime/attempt.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path, manifest


def _environment(monkeypatch: pytest.MonkeyPatch, root: Path, manifest: Path) -> None:
    values = {
        "ATREX_CORE_PHASE": "optimization_attempt",
        "ATREX_ATTEMPT_MANIFEST": str(manifest),
        "ATREX_ATTEMPT_REPORT_PATH": str(root / "scratch/attempt-report.json"),
        "ATREX_EVIDENCE_PROMPT_PATH": str(root / ".runtime/evidence-instructions.md"),
        "ATREX_GATEWAY_CAPABILITY": "scoped-capability",
        "ATREX_GATEWAY_PROXY_URL": "http://runtime.invalid",
        "ATREX_OPTIMIZER_REPOSITORY": str(root / "agent/optimizer"),
        "ATREX_SESSION_TIMEOUT_SECONDS": "60",
        "ATREX_USAGE_BUDGET": "1000",
        "ATREX_USAGE_UNIT": "provider_tokens",
        "ATREX_TOKEN_USAGE_REPORT": str(root / "scratch/token-usage.json"),
        "ATREX_SESSION_TRACE_PATH": str(root / "sessions/core"),
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)


def test_attempt_context_accepts_only_the_exact_manifest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manifest_path, manifest = _workspace(tmp_path)
    _environment(monkeypatch, tmp_path, manifest_path)

    context = RuntimeAttemptContext.from_environment()

    assert context.manifest == manifest
    assert context.working_kernel == tmp_path / "work/kernel"
    assert context.agent_problem["objective"] == (
        "implement vector addition while exact cases remain private"
    )


def test_attempt_context_rejects_unknown_manifest_fields(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manifest_path, manifest = _workspace(tmp_path)
    manifest["unexpected"] = True
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    _environment(monkeypatch, tmp_path, manifest_path)

    with pytest.raises(ValueError, match="fields do not match"):
        RuntimeAttemptContext.from_environment()


def test_attempt_context_rejects_manifest_outside_internal_control_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manifest_path, _manifest = _workspace(tmp_path)
    relocated = tmp_path / "attempt.json"
    manifest_path.replace(relocated)
    _environment(monkeypatch, tmp_path, relocated)

    with pytest.raises(ValueError, match="internal Runtime control path"):
        RuntimeAttemptContext.from_environment()


def test_attempt_context_requires_explicit_phase(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manifest_path, _manifest = _workspace(tmp_path)
    _environment(monkeypatch, tmp_path, manifest_path)
    monkeypatch.delenv("ATREX_CORE_PHASE")

    with pytest.raises(ValueError, match="optimization_attempt phase"):
        RuntimeAttemptContext.from_environment()


def test_attempt_context_rejects_tampered_evidence_prompt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manifest_path, _manifest = _workspace(tmp_path)
    (tmp_path / ".runtime/evidence-instructions.md").write_text(
        "tampered",
        encoding="utf-8",
    )
    _environment(monkeypatch, tmp_path, manifest_path)

    with pytest.raises(ValueError, match="digest disagrees"):
        RuntimeAttemptContext.from_environment()


def test_lineage_bootstrap_context_loads_internal_problem_for_prompt_projection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    for relative in (
        "input/kernel",
        ".runtime",
        "agent/optimizer",
        "work/kernel",
        "reference",
        "sessions",
        "scratch",
    ):
        (tmp_path / relative).mkdir(parents=True, exist_ok=True)
    problem = {
        "schema_version": "atrex.agent_problem.v1",
        "objective": "implement vector addition while exact cases remain private",
    }
    (tmp_path / ".runtime/agent-problem.json").write_text(
        json.dumps(problem), encoding="utf-8"
    )
    manifest = {
        "schema_version": 2,
        "bootstrap_attempt_id": "attempt_0123456789abcdef0123456789abcdef",
        "lineage_id": "lineage_0123456789abcdef0123456789abcdef",
        "kernel_agent_revision_id": "agent-revision",
        "input_kernel_digest": "sha256:kernel",
        "optimizer_digest": "sha256:optimizer",
        "evaluation_contract_digest": "sha256:contract",
        "agent_problem_digest": "sha256:problem",
        "dsl": "triton",
        "operator": "vector_add",
        "hardware_target": "h100",
        "paths": {
            "input_kernel": "input/kernel",
            "working_kernel": "work/kernel",
            "agent_problem": ".runtime/agent-problem.json",
            "optimizer": "agent/optimizer",
            "reference": "reference",
        },
    }
    manifest_path = tmp_path / ".runtime/lineage-bootstrap.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    environment = {
        "ATREX_CORE_PHASE": "framework_baseline",
        "ATREX_LINEAGE_BOOTSTRAP_MANIFEST": str(manifest_path),
        "ATREX_ATTEMPT_REPORT_PATH": str(
            tmp_path / "scratch/attempt-report.json"
        ),
        "ATREX_GATEWAY_CAPABILITY": "scoped-capability",
        "ATREX_GATEWAY_PROXY_URL": "http://runtime.invalid",
        "ATREX_OPTIMIZER_REPOSITORY": str(tmp_path / "agent/optimizer"),
        "ATREX_SESSION_TIMEOUT_SECONDS": "60",
        "ATREX_USAGE_BUDGET": "1000",
        "ATREX_USAGE_UNIT": "provider_tokens",
        "ATREX_TOKEN_USAGE_REPORT": str(tmp_path / "scratch/token-usage.json"),
        "ATREX_SESSION_TRACE_PATH": str(tmp_path / "sessions/core"),
    }
    for key, value in environment.items():
        monkeypatch.setenv(key, value)

    context = RuntimeLineageBootstrapContext.from_environment()

    assert context.workspace == tmp_path
    assert context.agent_problem == problem
