from __future__ import annotations

import json
from pathlib import Path

import pytest

from contexts.attempt import RuntimeAttemptContext


def _workspace(root: Path) -> tuple[Path, dict[str, object]]:
    for relative in (
        "input/kernel",
        "input/evidence",
        "input/attempt-evidence",
        "input/agent-problem",
        "agent/optimizer",
        "work/kernel",
        "sessions",
        "scratch",
    ):
        (root / relative).mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": 5,
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
            "branch": "active",
            "attempt_ordinal": 1,
            "operator": "vector_add",
            "hardware_target": "h100",
            "evaluation_contract_digest": "sha256:contract",
            "agent_problem_digest": "sha256:problem",
        },
        "paths": {
            "input_kernel": "input/kernel",
            "working_kernel": "work/kernel",
            "evidence": "input/evidence",
            "attempt_evidence": "input/attempt-evidence",
            "agent_problem": "input/agent-problem",
            "optimizer": "agent/optimizer",
        },
    }
    path = root / "attempt.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path, manifest


def _environment(monkeypatch: pytest.MonkeyPatch, root: Path, manifest: Path) -> None:
    values = {
        "ATREX_CORE_PHASE": "optimization_attempt",
        "ATREX_ATTEMPT_MANIFEST": str(manifest),
        "ATREX_ATTEMPT_REPORT_PATH": str(root / "scratch/attempt-report.json"),
        "ATREX_GATEWAY_CAPABILITY": "scoped-capability",
        "ATREX_GATEWAY_PROXY_URL": "http://runtime.invalid",
        "ATREX_OPTIMIZER_REPOSITORY": str(root / "agent/optimizer"),
        "ATREX_SESSION_TIMEOUT_SECONDS": "60",
        "ATREX_TOKEN_BUDGET": "1000",
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


def test_attempt_context_requires_explicit_phase(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manifest_path, _manifest = _workspace(tmp_path)
    _environment(monkeypatch, tmp_path, manifest_path)
    monkeypatch.delenv("ATREX_CORE_PHASE")

    with pytest.raises(ValueError, match="optimization_attempt phase"):
        RuntimeAttemptContext.from_environment()
