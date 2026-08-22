from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from runtime_tools import (
    _agent_knowledge,
    _atomic_json,
    _request_object,
    attempt_report,
    record_experiment,
)


def _context(root: Path) -> Any:
    (root / "scratch").mkdir()
    return SimpleNamespace(
        workspace=root,
        attempt_id="attempt_0123456789abcdef0123456789abcdef",
        report_path=root / "scratch/attempt-report.json",
    )


def _experiment() -> dict[str, str]:
    return {
        "name": "vectorize load",
        "hypothesis": "one transaction replaces two",
        "change": "use a vector load",
        "candidate_artifact_digest": "sha256:" + "a" * 64,
        "evidence": "evaluation result sha256:example",
        "result": "latency improved",
        "decision": "continue",
    }


def _report() -> dict[str, object]:
    return {
        "status": "candidate_ready",
        "hypothesis": "one transaction replaces two",
        "bottleneck": "load issue rate",
        "plan": ["vectorize the load"],
        "change_summary": "used a vector load",
        "profile_evidence": "survey result sha256:profile",
        "evaluation_evidence": "evaluate result sha256:evaluate",
        "result_interpretation": "candidate is correct and faster",
        "decision": "keep",
        "research_sources": [],
        "lessons": ["alignment must be checked"],
        "next_directions": [],
    }


def test_request_must_be_a_bounded_regular_file_under_scratch(tmp_path: Path) -> None:
    context = _context(tmp_path)
    request = tmp_path / "scratch/request.json"
    request.write_text('{"operation":"evaluate"}', encoding="utf-8")
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")

    assert _request_object(context, Path("scratch/request.json"), "gateway-execute") == {
        "operation": "evaluate"
    }
    with pytest.raises(ValueError, match="under scratch"):
        _request_object(context, outside, "gateway-execute")


def test_attempt_report_validates_and_atomically_publishes_once(tmp_path: Path) -> None:
    context = _context(tmp_path)
    record_experiment(context, _experiment())

    report = attempt_report(context, _report())

    assert report["schema_version"] == 3
    assert json.loads(context.report_path.read_text(encoding="utf-8")) == report
    with pytest.raises(FileExistsError, match="already exists"):
        attempt_report(context, _report())


def test_attempt_report_rejects_invalid_lists_before_publication(tmp_path: Path) -> None:
    context = _context(tmp_path)
    record_experiment(context, _experiment())
    report = _report()
    report["plan"] = []

    with pytest.raises(ValueError, match="plan must not be empty"):
        attempt_report(context, report)
    assert not context.report_path.exists()


def test_exclusive_atomic_write_never_replaces_existing_file(tmp_path: Path) -> None:
    path = tmp_path / "report.json"
    _atomic_json(path, {"first": True}, exclusive=True)

    with pytest.raises(FileExistsError):
        _atomic_json(path, {"second": True}, exclusive=True)
    assert json.loads(path.read_text(encoding="utf-8")) == {"first": True}


def test_agent_knowledge_hides_runtime_audit_envelope() -> None:
    content = {
        "records": {
            "nvidia.hopper.triton.kernel-opt.reduction": {
                "store": "gpu_wiki",
                "source": "kernel_wiki",
                "type": "technique-card",
                "applies_to": {"arch": "hopper", "dsl": "triton"},
                "match": {"arch": "exact"},
                "payload": {"goal": "tile a reduction"},
            }
        },
        "notes": [],
    }

    assert (
        _agent_knowledge(
            {
                "schema_version": 1,
                "interaction_artifact_digest": "sha256:internal",
                "snapshot_id": "internal-snapshot",
                "content_digest": "sha256:internal",
                "content": content,
            }
        )
        == content
    )
