from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from threading import Event, Lock
from types import SimpleNamespace

import pytest

from aka.contracts.agent import AgentRuntimeCapabilities, NativeInvocation, TokenUsage
from aka.contracts.workspace import CandidateHandle
from aka.legacy.task.problem.layout import public_shapes_payload, validate_public_shapes
from aka.light.agent.home import SESSION_HOME_ROOT_ENV, prepare_agent_environment
from aka.light.agent.runtime.model import AgentRunRequest as RuntimeAgentRunRequest
from aka.light.agent.runtime.runtime import build_agent_runtime
from aka.light.experiment.store import MeasurementStore
from aka.light.research import ResearchStore
from aka.light.runtime.server import _journal_evidence_hint
from aka.light.runtime.paths import supervisor_campaign_root
from aka.light.runtime.request_errors import AgentRequestError
from aka.light.runtime.request_feedback import augment_error, journal_schema
from aka.light.runtime.request_errors import REPAIR_REQUEST
from aka.light.runtime.server import RuntimeConfig, SupervisorRuntime
from aka.light.workflow.journal_projection import validate_terminal
from aka.light.workflow.journal_service import (
    SupervisorJournalService,
    _remove_stale_direction_projection,
    copy_experiment_reference_archive,
    initialize_journal,
    load_journal,
    private_write,
    research_projection,
    shared_research_path,
    update_direction,
)
from aka.light.workflow.models import EpisodeHandoff, SupervisorState
from aka.light.workflow.bootstrap import Campaign
from aka.light.workflow.episode import LongHorizonCampaign
from aka.light.workflow.search import PooledSearch, SearchOptions, read_state
from aka.light.workflow.search_driver import EpisodeSearchDriver
from aka.light.workflow.session import LongSessionRunner
from aka.light.workspace.agent_git import AgentGit, DirtyKernelWorktree
from aka.light.workspace.episode import EpisodeWorkspace
from aka.light.workspace.files import publish_if_changed
from aka.light.workspace.git import EpisodeWorktree, promote_candidate
from aka.light.workspace.provider import ManagedCandidateWorkspace
from aka.light.workspace.shared_history import export_epoch, import_exports
from aka.light.workspace.sources import default_tree, install, pack, snapshot


def _measurement(store: MeasurementStore, source: bytes, marker: str) -> tuple[str, str]:
    kernel_id = hashlib.sha1((marker + "\0").encode() + source).hexdigest()
    identity = store.kernel(source, kernel_id)
    request = {
        "operation": "evaluate",
        "options": {},
        "inputs": {"kernel.py": hashlib.sha256(source).hexdigest()},
        "kernel_ids": {"candidate": kernel_id},
        "marker": marker,
    }
    response = {"exit_code": 0, "stdout": "", "stderr": ""}
    with store.reserve(request, {"candidate": identity}) as task:
        record = task.finish(response, cacheable=True)
    return record["gateway_record_id"], identity["kernel_id"]


def _passing_full_measurement(
    store: MeasurementStore,
    source: bytes,
    marker: str,
    latencies: dict[str, float],
) -> tuple[str, str]:
    kernel_commit_id = hashlib.sha1((marker + "\0").encode() + source).hexdigest()
    identity = store.kernel(source, kernel_commit_id)
    request = {
        "operation": "evaluate",
        "options": {
            "command": ["python3", "test_kernel.py"],
            "evaluation_mode": "full",
            "evaluation_policy": {"mode": "full"},
        },
        "inputs": {"kernel.py": hashlib.sha256(source).hexdigest()},
        "kernel_ids": {"candidate": kernel_commit_id},
        "marker": marker,
    }
    sample = {
        "all_pass": True,
        "correct": True,
        "mode": "full",
        "latency_us_by_shape": latencies,
    }
    response = {
        "exit_code": 0,
        "stdout": "[test_kernel] RESULT_JSON=" + json.dumps(sample) + "\n",
        "stderr": "",
    }
    with store.reserve(request, {"candidate": identity}) as task:
        record = task.finish(response, cacheable=True)
    return record["gateway_record_id"], identity["kernel_id"]


def test_research_records_are_projected_as_normal_workspace_files(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    journals = tmp_path / "scope" / "journals"
    evidence = journals / "episodes" / "e00000001"
    evidence.mkdir(parents=True)
    initialize_journal(
        evidence / "journal.json",
        episode=1,
        base_commit="a" * 40,
        branch="work",
    )
    service = SupervisorJournalService(
        workspace=workspace,
        campaign_root=journals,
        evidence_root=evidence,
        research_root=tmp_path / "research",
    )

    proposed = service.execute({
        "operation": "direction_update",
        "request": {
            "action": "propose",
            "name": "Split KV",
            "change_scope": "major_redesign",
            "hypothesis": "Split KV should improve long contexts.",
            "applicability": "Long-context attention shapes on the target GPU.",
            "expected_benefit": "Reduce full-workload geometric-mean latency.",
            "plan": ["Compile a probe", "Evaluate the complete workload"],
        },
    })
    direction_id = proposed["direction_id"]
    assert proposed["archive_path"] == (
        f"directions/proposed/{direction_id}.json"
    )
    started = service.execute({
        "operation": "direction_update",
        "request": {
            "action": "start",
            "direction_id": direction_id,
            "analysis": "Highest expected value.",
        },
    })
    assert started["archive_path"] == (
        f"directions/in_progress/{direction_id}.json"
    )
    service.execute({
        "operation": "direction_progress",
        "request": {"direction_id": direction_id, "plan_step": 0},
    })
    (workspace / "scratch" / "probe.md").parent.mkdir(parents=True)
    (workspace / "scratch" / "probe.md").write_text("probe evidence\n", encoding="utf-8")
    (workspace / "scratch" / "analysis" / "empty").mkdir(parents=True)
    (workspace / "scratch" / "analysis" / "result.json").write_text(
        '{"winner":"ksplit2"}\n', encoding="utf-8"
    )

    store = MeasurementStore(tmp_path / "scope" / "measurements")
    record_id, kernel_id = _measurement(store, b"def kernel():\n    return 1\n", "probe")
    pareto_record_id, pareto_kernel_commit_id = _passing_full_measurement(
        store,
        b"def kernel():\n    return 2\n",
        "full",
        {"shape_a": 12.5, "shape_b": 8.25},
    )
    recorded = service.execute({
        "operation": "experiment_record",
        "request": {
            "name": "KSPLIT probe",
            "intent": "Check whether the mechanism compiles and runs.",
            "gateway_record_ids": [record_id],
            "interpretation": "The mechanism is executable; full performance remains open.",
            "references": ["scratch/probe.md", "scratch/analysis"],
        },
    })
    experiment_id = recorded["experiment_id"]
    assert recorded["archive_path"] == (
        f"experiments/{experiment_id}/experiment.json"
    )
    stored_experiment = load_journal(evidence / "journal.json")["experiments"][0]
    assert set(stored_experiment) == {
        "experiment_id", "sequence", "name", "intent", "gateway_record_ids",
        "interpretation", "direction_id", "references", "revision",
        "reference_revision",
    }

    direction = json.loads(
        (workspace / "directions" / "in_progress" / f"{direction_id}.json").read_text()
    )
    experiment = json.loads(
        (workspace / "experiments" / experiment_id / "experiment.json").read_text()
    )
    gateway = json.loads(
        (workspace / "records" / f"{record_id}.json").read_text()
    )
    assert direction["status"] == "in_progress"
    assert direction["proposed_by"] == "T1"
    assert direction["applicability"] == (
        "Long-context attention shapes on the target GPU."
    )
    assert direction["expected_benefit"] == (
        "Reduce full-workload geometric-mean latency."
    )
    assert direction["completed_plan_steps"] == [0]
    assert direction["next_step"] == "Evaluate the complete workload"
    assert direction["queue_rank"] == 1
    assert experiment["direction_id"] == direction_id
    assert set(experiment) == {
        "experiment_id", "name", "intent", "gateway_record_ids", "interpretation",
        "direction_id", "references", "revision",
    }
    archived_probe = (
        workspace / "experiments" / experiment_id / "references" / "scratch" / "probe.md"
    )
    archived_result = (
        workspace / "experiments" / experiment_id / "references"
        / "scratch" / "analysis" / "result.json"
    )
    archived_empty = (
        workspace / "experiments" / experiment_id / "references"
        / "scratch" / "analysis" / "empty"
    )
    assert experiment["references"] == [
        f"experiments/{experiment_id}/references/scratch/probe.md",
        f"experiments/{experiment_id}/references/scratch/analysis",
    ]
    assert archived_probe.read_text() == "probe evidence\n"
    assert json.loads(archived_result.read_text()) == {"winner": "ksplit2"}
    assert archived_empty.is_dir()
    copied_journals = tmp_path / "copied" / "journals"
    copy_experiment_reference_archive(journals, copied_journals, experiment_id)
    assert (
        copied_journals / "experiment-archives" / experiment_id
        / "files" / "scratch" / "probe.md"
    ).read_text() == "probe evidence\n"
    assert gateway["gateway_record_id"] == record_id
    assert gateway["kernel_commit_id"] == kernel_id
    assert "kernel_id" not in gateway
    assert "request" not in gateway and "inputs" not in gateway
    assert not (workspace / "experiments" / experiment_id / "records").exists()

    service.research.append(
        "promotion.accepted",
        {"kernel_id": pareto_kernel_commit_id},
        origin={"trajectory": 1},
    )
    service.publish_research_files()
    progress = (workspace / "PROGRESS.md").read_text()
    assert "proposed by `T1`" in progress
    assert "Applicability: Long-context attention shapes on the target GPU." in progress


    assert "Expected benefit: Reduce full-workload geometric-mean latency." in progress
    assert "atrex.research_board.v2" not in progress
    assert '"schema_version"' not in progress
    assert '"revision"' not in progress
    assert '"recent_experiments"' not in progress
    assert "# Current state" in progress
    assert "## Lineages" in progress
    assert "## Directions" in progress
    assert "### In Progress" in progress
    assert direction_id in progress

    assert "**Best verified Kernel commit ID:**" in progress
    assert "also the promoted incumbent" in progress
    assert "**Promoted incumbent commit ID:**" not in progress
    assert "**Last measured working Kernel commit ID:**" in progress
    assert "**Latest visible Gateway result and scope:** evaluate (completed; PASS; GeoMean" in progress
    assert "scope: full, contract, 2 measured Shapes" in progress
    assert "**Suggested next work/probe:**" in progress
    assert "**Current real blocker:** none recorded." in progress
    assert pareto_kernel_commit_id in progress
    assert pareto_record_id in progress
    assert (
        "Eligible visible full evaluations across local and imported peer history: "
        "**1**; covered Shapes: **2**."
    ) in progress
    assert (workspace / "records" / f"{pareto_record_id}.json").is_file()
    assert "| `shape_a` | 12.5 |" in progress
    assert "| `shape_b` | 8.25 |" in progress

    # Recovery rebuilds deleted convenience files from authoritative stores.
    (workspace / "directions" / "in_progress" / f"{direction_id}.json").unlink()
    (workspace / "records" / f"{record_id}.json").unlink()
    (workspace / "scratch" / "probe.md").write_text("mutated later\n", encoding="utf-8")
    archived_probe.unlink()
    service.publish_research_files()
    assert (
        workspace / "directions" / "in_progress" / f"{direction_id}.json"
    ).is_file()
    assert (workspace / "records" / f"{record_id}.json").is_file()
    assert archived_probe.read_text() == "probe evidence\n"


def test_local_experiment_can_be_revised_without_losing_audit_or_references(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    journals = tmp_path / "scope" / "journals"
    evidence = journals / "episodes" / "e00000001"
    evidence.mkdir(parents=True)
    initialize_journal(
        evidence / "journal.json",
        episode=1,
        base_commit="a" * 40,
        branch="work",
        tool_modules=("experiments",),
    )
    service = SupervisorJournalService(
        workspace=workspace,
        campaign_root=journals,
        evidence_root=evidence,
        research_root=tmp_path / "research",
    )
    (workspace / "scratch").mkdir()
    (workspace / "scratch" / "note.txt").write_text("revision one\n")
    created = service.execute({
        "operation": "experiment_record",
        "request": {
            "name": "Split-K probe",
            "intent": "Test whether the decomposition is viable.",
            "interpretation": "The mechanism compiles; performance is unresolved.",
            "references": ["scratch/note.txt"],
        },
    })
    experiment_id = created["experiment_id"]
    assert created["status"] == "recorded"
    assert created["revision"] == 1

    revised = service.execute({
        "operation": "experiment_record",
        "request": {
            "experiment_id": experiment_id,
            "name": "Split-K full measurement",
            "intent": "Determine whether the decomposition improves the workload.",
            "interpretation": "The measured implementation is useful only for long-K shapes.",
        },
    })
    assert revised == {
        "status": "updated",
        "experiment_id": experiment_id,
        "revision": 2,
        "archive_path": f"experiments/{experiment_id}/experiment.json",
    }
    journal = load_journal(evidence / "journal.json")
    assert len(journal["experiments"]) == 1
    stored = journal["experiments"][0]
    assert stored["revision"] == 2
    assert stored["sequence"] == 1
    assert stored["references"] == [
        f"experiments/{experiment_id}/references/scratch/note.txt"
    ]
    assert stored["reference_revision"] == 1
    projected = json.loads(
        (workspace / "experiments" / experiment_id / "experiment.json").read_text()
    )
    assert projected["revision"] == 2
    assert projected["interpretation"].startswith("The measured implementation")
    assert "reference_revision" not in projected
    assert (
        workspace / "experiments" / experiment_id / "references" / "scratch" / "note.txt"
    ).read_text() == "revision one\n"
    assert len(list((evidence / "claim-audit").glob("*/submission.json"))) == 2

    (workspace / "scratch" / "note.txt").write_text("revision three\n")
    replaced = service.execute({
        "operation": "experiment_record",
        "request": {
            "experiment_id": experiment_id,
            "name": "Split-K full measurement",
            "intent": "Determine whether the decomposition improves the workload.",
            "interpretation": "The updated evidence confirms the scoped result.",
            "references": ["scratch/note.txt"],
        },
    })
    assert replaced["revision"] == 3
    assert load_journal(evidence / "journal.json")["experiments"][0][
        "reference_revision"
    ] == 3
    assert (
        workspace / "experiments" / experiment_id / "references" / "scratch" / "note.txt"
    ).read_text() == "revision three\n"

    finalized = load_journal(evidence / "journal.json")
    finalized.update(state="pivot", finalized_at="2026-11-04T00:00:00+00:00")
    private_write(evidence / "journal.json", finalized)
    next_evidence = journals / "episodes" / "e00000002"
    next_evidence.mkdir()
    initialize_journal(
        next_evidence / "journal.json",
        episode=2,
        base_commit="b" * 40,
        branch="work-2",
        tool_modules=("experiments",),
    )
    next_service = SupervisorJournalService(
        workspace=workspace,
        campaign_root=journals,
        evidence_root=next_evidence,
        research_root=tmp_path / "research",
    )
    cross_episode = next_service.execute({
        "operation": "experiment_record",
        "request": {
            "experiment_id": experiment_id,
            "name": "Split-K full measurement",
            "intent": "Determine whether the decomposition improves the workload.",
            "interpretation": "A later Episode narrows the result to long-K shapes.",
        },
    })
    assert cross_episode["revision"] == 4
    assert len(load_journal(next_evidence / "journal.json")["experiments"]) == 1
    assert json.loads(
        (workspace / "experiments" / experiment_id / "experiment.json").read_text()
    )["revision"] == 4


def test_open_local_direction_can_be_revised_with_stable_identity(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    journals = tmp_path / "scope" / "journals"
    evidence = journals / "episodes" / "e00000001"
    evidence.mkdir(parents=True)
    initialize_journal(
        evidence / "journal.json",
        episode=1,
        base_commit="a" * 40,
        branch="work",
        tool_modules=("directions",),
    )
    service = SupervisorJournalService(
        workspace=workspace,
        campaign_root=journals,
        evidence_root=evidence,
        research_root=tmp_path / "research",
    )
    proposed = service.execute({
        "operation": "direction_update",
        "request": {
            "action": "propose",
            "name": "Split K",
            "change_scope": "major_redesign",
            "hypothesis": "Splitting K may improve long-context shapes.",
            "applicability": "Long-context shapes.",
            "expected_benefit": "Reduce long-context latency.",
            "plan": ["Compile the bounded probe", "Evaluate the workload"],
        },
    })
    direction_id = proposed["direction_id"]
    assert proposed["revision"] == 1
    service.execute({
        "operation": "direction_update",
        "request": {
            "action": "start",
            "direction_id": direction_id,
            "analysis": "Highest expected benefit.",
        },
    })
    service.execute({
        "operation": "direction_progress",
        "request": {"direction_id": direction_id, "plan_step": 0},
    })

    revised = service.execute({
        "operation": "direction_update",
        "request": {
            "action": "revise",
            "direction_id": direction_id,
            "name": "Split K crossover",
            "change_scope": "major_redesign",
            "hypothesis": "Splitting K helps only beyond a measurable K crossover.",
            "applicability": "Shapes beyond the measured K crossover.",
            "expected_benefit": "Reduce long-context latency without short-shape regression.",
            "plan": [
                "Measure the crossover",
                "Compile the bounded probe",
                "Evaluate the workload",
            ],
            "stop_conditions": ["Reduction overhead dominates beyond the crossover"],
        },
    })
    assert revised == {
        "status": "updated",
        "direction_id": direction_id,
        "revision": 2,
        "archive_path": f"directions/in_progress/{direction_id}.json",
    }
    events = load_journal(evidence / "journal.json")["direction_events"]
    assert [event["action"] for event in events] == ["propose", "start", "revise"]
    projected = json.loads(
        (workspace / "directions" / "in_progress" / f"{direction_id}.json").read_text()
    )
    assert projected["revision"] == 2
    assert projected["hypothesis"].endswith("K crossover.")
    assert projected["status"] == "in_progress"
    assert projected["completed_plan_steps"] == [1]
    assert projected["next_step"] == "Measure the crossover"
    assert len(list((evidence / "claim-audit").glob("*/submission.json"))) == 4

    service.execute({
        "operation": "direction_update",
        "request": {
            "action": "defer",
            "direction_id": direction_id,
            "analysis": "Pause until the profiler is restored.",
            "hypothesis_status": "unresolved",
            "reopen_condition": "Profiler is available.",
        },
    })
    with pytest.raises((AgentRequestError, ValueError)) as error:
        service.execute({
            "operation": "direction_update",
            "request": {
                "action": "revise",
                "direction_id": direction_id,
                "name": "Split K crossover",
                "change_scope": "major_redesign",
                "hypothesis": "A revised but closed claim.",
                "applicability": "Long-context shapes.",
                "expected_benefit": "Reduce latency.",
                "plan": ["Measure"],
            },
        })
    assert "start it before revising" in str(error.value)


def test_claim_revisions_resume_the_prior_review_session(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    journals = tmp_path / "scope" / "journals"
    evidence = journals / "episodes" / "e00000001"
    evidence.mkdir(parents=True)
    initialize_journal(
        evidence / "journal.json",
        episode=1,
        base_commit="a" * 40,
        branch="work",
    )
    review_calls = []

    def reviewer(root, *, resume_session_id=None):
        target = json.loads((root / "submissions" / "target.json").read_text())
        session_id = resume_session_id or f"review-session-{len(review_calls) + 1}"
        review_calls.append((target["action"] if "action" in target else "experiment",
                             resume_session_id, session_id))
        return {
            "verdict": "supported",
            "primary_issue": "No unsupported decision-critical claim.",
            "findings": [{
                "claim": "The revised claim is bounded.",
                "judgment": "supported",
                "decision_critical": True,
                "reason": "The supplied evidence is sufficient for this scope.",
                "evidence": ["submissions/target.json"],
            }],
            "scope_safe_interpretation": "Use only within the stated scope.",
            "next_check": "Run the planned bounded measurement.",
            "confidence": "high",
            "_review_session_audit": {
                "schema": "atrex.research-claim-review-session.v1",
                "attempts": [{
                    "attempt": 1,
                    "session_id": session_id,
                    "status": "accepted",
                    "exit_status": 0,
                    "timed_out": False,
                    "resumed": bool(resume_session_id),
                    "conversation_path": f"claim-review-sessions/{session_id}/conversation.jsonl",
                }],
            },
        }

    service = SupervisorJournalService(
        workspace=workspace,
        campaign_root=journals,
        evidence_root=evidence,
        research_root=tmp_path / "research",
        claim_reviewer=reviewer,
    )
    proposed = service.execute({
        "operation": "direction_update",
        "request": {
            "action": "propose",
            "name": "Split K",
            "change_scope": "major_redesign",
            "hypothesis": "Split K may improve long-context shapes.",
            "applicability": "Long-context shapes.",
            "expected_benefit": "Reduce latency.",
            "plan": ["Compile a bounded probe"],
        },
    })
    direction_id = proposed["direction_id"]
    service.execute({
        "operation": "direction_update",
        "request": {
            "action": "revise",
            "direction_id": direction_id,
            "name": "Split K crossover",
            "change_scope": "major_redesign",
            "hypothesis": "Split K helps only above a crossover.",
            "applicability": "Shapes above the crossover.",
            "expected_benefit": "Reduce long-context latency.",
            "plan": ["Measure the crossover"],
        },
    })
    service.execute({
        "operation": "direction_update",
        "request": {
            "action": "start",
            "direction_id": direction_id,
            "analysis": "Proceed with the bounded probe.",
        },
    })
    experiment = service.execute({
        "operation": "experiment_record",
        "request": {
            "name": "Crossover note",
            "intent": "Record the bounded interpretation.",
            "interpretation": "The crossover remains unresolved.",
        },
    })
    service.execute({
        "operation": "experiment_record",
        "request": {
            "experiment_id": experiment["experiment_id"],
            "name": "Crossover note",
            "intent": "Record the corrected bounded interpretation.",
            "interpretation": "The evidence supports only long-context shapes.",
        },
    })

    assert review_calls[0] == ("propose", None, "review-session-1")
    assert review_calls[1] == ("revise", "review-session-1", "review-session-1")
    assert review_calls[2][0:2] == ("start", None)
    assert review_calls[3][0:2] == ("experiment", None)
    assert review_calls[4] == (
        "experiment", review_calls[3][2], review_calls[3][2]
    )


def test_missing_claim_revision_ids_return_repairable_guidance(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    journals = tmp_path / "scope" / "journals"
    evidence = journals / "episodes" / "e00000001"
    evidence.mkdir(parents=True)
    initialize_journal(
        evidence / "journal.json",
        episode=1,
        base_commit="a" * 40,
        branch="work",
    )
    service = SupervisorJournalService(
        workspace=workspace,
        campaign_root=journals,
        evidence_root=evidence,
        research_root=tmp_path / "research",
    )

    with pytest.raises(AgentRequestError) as missing_direction:
        service.execute({
            "operation": "direction_update",
            "request": {
                "action": "revise",
                "direction_id": "direction_" + "1" * 32,
                "name": "Unknown Direction",
                "change_scope": "local",
                "hypothesis": "This ID does not exist.",
                "applicability": "No visible scope.",
                "expected_benefit": "No established benefit.",
                "plan": ["Inspect the current projection"],
            },
        })
    direction_error = missing_direction.value.response["error"]
    assert direction_error["code"] == "direction_not_found"
    assert "directions/<status>/" in direction_error["next_action"]
    assert "action=propose" in direction_error["next_action"]

    with pytest.raises(AgentRequestError) as missing_experiment:
        service.execute({
            "operation": "experiment_record",
            "request": {
                "experiment_id": "experiment_" + "2" * 32,
                "name": "Unknown Experiment",
                "intent": "Revise a record that does not exist.",
                "interpretation": "No claim can be revised under this ID.",
            },
        })
    experiment_error = missing_experiment.value.response["error"]
    assert experiment_error["code"] == "experiment_not_found"
    assert "experiments/" in experiment_error["next_action"]
    assert "omit experiment_id" in experiment_error["next_action"]


def test_research_mutation_receipts_include_archive_and_inline_qwen_review(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    journals = tmp_path / "scope" / "journals"
    evidence = journals / "episodes" / "e00000001"
    evidence.mkdir(parents=True)
    initialize_journal(
        evidence / "journal.json",
        episode=1,
        base_commit="a" * 40,
        branch="work",
    )
    reviewed_targets = []

    def reviewer(root):
        target = json.loads((root / "submissions" / "target.json").read_text())
        reviewed_targets.append(target)
        review_number = len(reviewed_targets)
        return {
            "verdict": "supported",
            "primary_issue": "No material unsupported claim was found.",
            "findings": [{
                "claim": "The submitted research statement.",
                "judgment": "supported",
                "decision_critical": True,
                "reason": "The bounded submission is internally consistent.",
                "evidence": ["submissions/target.json"],
            }],
            "scope_safe_interpretation": "Use the claim only within its stated scope.",
            "next_check": "Measure the next candidate against the same baseline.",
            "confidence": "high",
            "_review_session_audit": {
                "schema": "atrex.research-claim-review-session.v1",
                "attempts": [{
                    "attempt": 1,
                    "session_id": f"review-session-{review_number}",
                    "status": "accepted",
                    "exit_status": 0,
                    "timed_out": False,
                    "conversation_path": (
                        f"claim-review-sessions/review-{review_number}/"
                        "run-1/conversation.jsonl"
                    ),
                }],
            },
        }

    service = SupervisorJournalService(
        workspace=workspace,
        campaign_root=journals,
        evidence_root=evidence,
        research_root=tmp_path / "research",
        claim_reviewer=reviewer,
    )
    proposed = service.execute({
        "operation": "direction_update",
        "request": {
            "action": "propose",
            "name": "Split KV",
            "change_scope": "major_redesign",
            "hypothesis": "Split KV should improve long contexts.",
            "applicability": "Long-context attention shapes.",
            "expected_benefit": "Reduce end-to-end latency.",
            "plan": ["Compile a bounded probe"],
        },
    })
    direction_id = proposed["direction_id"]
    assert proposed["archive_path"] == f"directions/proposed/{direction_id}.json"
    assert proposed["review"]["accepted"] is True
    assert proposed["review"]["verdict"] == "supported"
    assert "review_path" not in proposed
    assert "evidence_manifest" not in proposed["review"]
    assert "_review_session_audit" not in proposed["review"]
    assert "session_audit" not in proposed["review"]
    assert not list((workspace / "directions").rglob("*.review.json"))

    service.execute({
        "operation": "direction_update",
        "request": {
            "action": "start",
            "direction_id": direction_id,
            "analysis": "This is the highest-value unresolved path.",
        },
    })
    recorded = service.execute({
        "operation": "experiment_record",
        "request": {
            "name": "Design note",
            "intent": "Specify the first bounded probe.",
            "interpretation": "No measurement claim is made yet.",
        },
    })
    experiment_id = recorded["experiment_id"]
    assert recorded["archive_path"] == (
        f"experiments/{experiment_id}/experiment.json"
    )
    assert "review_path" not in recorded
    assert "evidence_manifest" not in recorded["review"]
    assert "reviewer_model" not in recorded["review"]
    assert "_review_session_audit" not in recorded["review"]
    assert "session_audit" not in recorded["review"]
    assert not (workspace / "experiments" / experiment_id / "review.json").exists()
    assert len(reviewed_targets) == 3
    audit_outcomes = [
        json.loads(path.read_text())
        for path in (evidence / "claim-audit").glob("*/outcome.json")
    ]
    assert len(audit_outcomes) == 3
    assert {item["status"] for item in audit_outcomes} == {"archived"}
    assert all(item["result"]["review"]["verdict"] == "supported"
               for item in audit_outcomes)
    private_reviews = [
        json.loads(path.read_text())
        for path in (evidence / "claim-reviews").glob("review_*.json")
    ]
    assert len(private_reviews) == 3
    assert {
        row["session_audit"]["attempts"][0]["session_id"]
        for row in private_reviews
    } == {"review-session-1", "review-session-2", "review-session-3"}
    audit_links = [
        json.loads(path.read_text())
        for path in (evidence / "claim-audit").glob("*/review.json")
    ]
    assert len(audit_links) == 3
    assert all(
        row["review_archive_path"].startswith("claim-reviews/review_")
        and row["session_audit"]["attempts"][0]["conversation_path"].endswith(
            "/conversation.jsonl"
        )
        for row in audit_links
    )


def test_research_claim_reviewer_returns_durable_conversation_links(tmp_path):
    evidence = tmp_path / "episode"
    bundle = evidence / "claim-review-test"
    bundle.mkdir(parents=True)
    attempts = []
    resume_requests = []

    valid_review = {
        "verdict": "supported",
        "primary_issue": "No material unsupported claim was found.",
        "findings": [{
            "claim": "The submitted research statement.",
            "judgment": "supported",
            "decision_critical": True,
            "reason": "The supplied evidence supports the bounded statement.",
            "evidence": ["submissions/target.json"],
        }],
        "scope_safe_interpretation": "Use the claim only within its stated scope.",
        "next_check": "Measure the next candidate against the same baseline.",
        "confidence": "high",
    }

    def run_session(workspace, _prompt, timeout=None, **kwargs):
        assert timeout == 300
        attempt = len(attempts) + 1
        attempts.append(attempt)
        resume_requests.append(
            (kwargs.get("session_id"), kwargs.get("resume_session"))
        )
        capture_root = Path(kwargs["extra_environment"]["ATREX_SESSION_CAPTURE_DIR"])
        conversation = capture_root / f"run-{attempt}" / "conversation.jsonl"
        conversation.parent.mkdir(parents=True)
        conversation.write_text(
            json.dumps({"type": "assistant", "attempt": attempt}) + "\n",
            encoding="utf-8",
        )
        (workspace / "review.json").write_text(
            json.dumps({} if attempt == 1 else valid_review),
            encoding="utf-8",
        )
        return SimpleNamespace(
            exit_status=0,
            timed_out=False,
            session_id=kwargs.get("session_id") or f"provider-session-{attempt}",
            stdout_tail="",
            stderr_tail="",
        )

    campaign = SimpleNamespace(
        production_review_timeout=300,
        agent_boundary_environment=lambda _role: {},
        _run_session=run_session,
        _account=lambda _result, _label: None,
        _claim_review_evidence_digest=Campaign._claim_review_evidence_digest,
    )
    review = Campaign.review_research_claim(campaign, bundle)

    audit = review.pop("_review_session_audit")
    assert review == valid_review
    assert [row["status"] for row in audit["attempts"]] == ["invalid", "accepted"]
    assert [row["session_id"] for row in audit["attempts"]] == [
        "provider-session-1", "provider-session-2"
    ]
    for row in audit["attempts"]:
        conversation = evidence / row["conversation_path"]
        assert conversation.is_file()
        assert conversation.name == "conversation.jsonl"

    resumed_review = Campaign.review_research_claim(
        campaign, bundle, resume_session_id="provider-session-2"
    )
    resumed_audit = resumed_review.pop("_review_session_audit")
    assert resumed_review == valid_review
    assert resume_requests[-1] == ("provider-session-2", True)
    assert resumed_audit["attempts"][0]["resumed"] is True
    assert resumed_audit["attempts"][0]["session_id"] == "provider-session-2"


def test_light_agent_runtime_uses_native_resume_command(tmp_path):
    commands = []

    def process_runner(command, *, cwd, timeout, env):
        commands.append((list(command), cwd, timeout, dict(env)))
        return "", "", 0, False

    runtime = build_agent_runtime("claude", process_runner=process_runner)
    result = runtime.run(RuntimeAgentRunRequest(
        workspace=tmp_path,
        prompt="Review the revised claim.",
        timeout_s=30,
        reasoning_effort="high",
        session_id="11111111-1111-4111-8111-111111111111",
        resume_session=True,
    ))
    command = commands[0][0]
    assert "--resume" in command
    assert command[command.index("--resume") + 1] == result.session_id
    assert "--session-id" not in command


def test_unsupported_research_claims_are_not_archived(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    journals = tmp_path / "scope" / "journals"
    evidence = journals / "episodes" / "e00000001"
    evidence.mkdir(parents=True)
    initialize_journal(
        evidence / "journal.json",
        episode=1,
        base_commit="a" * 40,
        branch="work",
    )

    def reviewer(_root):
        return {
            "verdict": "unsupported",
            "primary_issue": "The decision-critical mechanism has no evidence.",
            "findings": [{
                "claim": "The proposed mechanism will reduce latency.",
                "judgment": "unsupported",
                "decision_critical": True,
                "reason": "No isolating measurement or source evidence is cited.",
                "evidence": ["submissions/target.json"],
            }],
            "scope_safe_interpretation": "The mechanism remains an untested idea.",
            "next_check": "Run one bounded mechanism-isolating probe.",
            "confidence": "high",
            "_review_session_audit": {
                "schema": "atrex.research-claim-review-session.v1",
                "attempts": [{
                    "attempt": 1,
                    "session_id": "rejected-review-session",
                    "status": "accepted",
                    "exit_status": 0,
                    "timed_out": False,
                    "conversation_path": (
                        "claim-review-sessions/rejected/"
                        "run-1/conversation.jsonl"
                    ),
                }],
            },
        }

    service = SupervisorJournalService(
        workspace=workspace,
        campaign_root=journals,
        evidence_root=evidence,
        research_root=tmp_path / "research",
        claim_reviewer=reviewer,
    )
    with pytest.raises(AgentRequestError) as direction_rejected:
        service.execute({
            "operation": "direction_update",
            "request": {
                "action": "propose",
                "name": "Unsupported split",
                "change_scope": "major_redesign",
                "hypothesis": "An uncited mechanism will reduce latency.",
                "applicability": "All workload shapes.",
                "expected_benefit": "Large speedup.",
                "plan": ["Implement the entire redesign"],
            },
        })
    response = direction_rejected.value.response
    assert response["error"]["code"] == "research_claim_unsupported"
    assert response["error"]["review"]["verdict"] == "unsupported"
    assert "evidence_manifest" not in response["error"]["review"]
    assert "will not be archived" in response["error"]["review"]["next_action"]
    assert load_journal(evidence / "journal.json")["direction_events"] == []
    assert not (workspace / "directions").exists()

    experiment_evidence = journals / "episodes" / "e00000002"
    experiment_evidence.mkdir(parents=True)
    initialize_journal(
        experiment_evidence / "journal.json",
        episode=2,
        base_commit="b" * 40,
        branch="experiment-work",
        tool_modules=("experiments",),
    )
    experiment_service = SupervisorJournalService(
        workspace=workspace,
        campaign_root=journals,
        evidence_root=experiment_evidence,
        research_root=tmp_path / "experiment-research",
        claim_reviewer=reviewer,
    )
    (workspace / "scratch").mkdir()
    (workspace / "scratch" / "unsupported-probe.py").write_text(
        "print('audit me')\n", encoding="utf-8"
    )
    with pytest.raises(AgentRequestError) as experiment_rejected:
        experiment_service.execute({
            "operation": "experiment_record",
            "request": {
                "name": "Unsupported interpretation",
                "intent": "Record an unmeasured causal claim.",
                "interpretation": "The mechanism is proven without measurement.",
                "references": ["scratch/unsupported-probe.py"],
            },
        })
    response = experiment_rejected.value.response
    assert response["error"]["code"] == "research_claim_unsupported"
    assert response["error"]["review"]["verdict"] == "unsupported"
    assert load_journal(experiment_evidence / "journal.json")["experiments"] == []
    assert not (workspace / "experiments").exists()
    assert not (evidence / "claim-reviews").exists()
    assert not (experiment_evidence / "claim-reviews").exists()
    direction_audit = list((evidence / "claim-audit").glob("*/outcome.json"))
    experiment_audit = list(
        (experiment_evidence / "claim-audit").glob("*/outcome.json")
    )
    assert len(direction_audit) == len(experiment_audit) == 1
    for path in (*direction_audit, *experiment_audit):
        outcome = json.loads(path.read_text())
        assert outcome["status"] == "unsupported"
        assert outcome["error"]["error"]["review"]["verdict"] == "unsupported"
        assert outcome["private_review"]["evidence_manifest"]["claim_type"] in {
            "direction", "experiment"
        }
        assert outcome["private_review"]["session_audit"]["attempts"][0][
            "conversation_path"
        ].endswith("/conversation.jsonl")
        assert "_review_session_audit" not in outcome["private_review"]
        submission = json.loads((path.parent / "submission.json").read_text())
        assert submission["claim_type"] in {"direction", "experiment"}
    experiment_audit_root = experiment_audit[0].parent
    reference_manifest = json.loads(
        (experiment_audit_root / "references" / "manifest.json").read_text()
    )
    assert reference_manifest["capture_status"] == "captured"
    assert reference_manifest["files"][0]["path"] == "scratch/unsupported-probe.py"
    assert (
        experiment_audit_root / "references" / "files"
        / "scratch" / "unsupported-probe.py"
    ).read_text() == "print('audit me')\n"


def test_missing_experiment_reference_is_repairable_and_records_nothing(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    journals = tmp_path / "scope" / "journals"
    evidence = journals / "episodes" / "e00000001"
    evidence.mkdir(parents=True)
    initialize_journal(
        evidence / "journal.json",
        episode=1,
        base_commit="a" * 40,
        branch="work",
        tool_modules=("experiments",),
    )
    service = SupervisorJournalService(
        workspace=workspace,
        campaign_root=journals,
        evidence_root=evidence,
        research_root=tmp_path / "research",
    )
    record_id, _ = _measurement(
        MeasurementStore(tmp_path / "scope" / "measurements"),
        b"def kernel():\n    return 1\n",
        "missing-reference",
    )

    with pytest.raises(AgentRequestError) as rejected:
        service.execute({
            "operation": "experiment_record",
            "request": {
                "name": "missing material",
                "intent": "Verify missing references are rejected before mutation.",
                "gateway_record_ids": [record_id],
                "interpretation": "No Experiment should be written.",
                "references": ["scratch/does-not-exist.txt"],
            },
        })

    assert rejected.value.response["error"]["code"] == "reference_not_found"
    assert rejected.value.response["issues"][0]["path"] == "references.0"
    assert load_journal(evidence / "journal.json")["experiments"] == []
    audit_outcomes = list((evidence / "claim-audit").glob("*/outcome.json"))
    assert len(audit_outcomes) == 1
    audit = json.loads(audit_outcomes[0].read_text())
    assert audit["status"] == "rejected"
    assert audit["error"]["error"]["code"] == "reference_not_found"


    (workspace / "scratch").mkdir()
    (workspace / "scratch" / "target.txt").write_text("not through a link\n")
    (workspace / "scratch" / "linked.txt").symlink_to("target.txt")
    with pytest.raises(AgentRequestError) as linked:
        service.execute({
            "operation": "experiment_record",
            "request": {
                "name": "linked material",
                "intent": "Verify links are rejected before mutation.",
                "gateway_record_ids": [record_id],
                "interpretation": "No Experiment should be written.",
                "references": ["scratch/linked.txt"],
            },
        })
    assert linked.value.response["error"]["code"] == "reference_symlink_forbidden"
    assert load_journal(evidence / "journal.json")["experiments"] == []


def test_direction_projection_moves_between_status_directories(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    journals = tmp_path / "scope" / "journals"
    evidence = journals / "episodes" / "e00000001"
    evidence.mkdir(parents=True)
    initialize_journal(
        evidence / "journal.json",
        episode=1,
        base_commit="a" * 40,
        branch="work",
        tool_modules=("directions",),
    )
    service = SupervisorJournalService(
        workspace=workspace,
        campaign_root=journals,
        evidence_root=evidence,
        research_root=tmp_path / "research",
    )

    direction_id = service.execute({
        "operation": "direction_update",
        "request": {
            "action": "propose",
            "name": "Split KV",
            "change_scope": "major_redesign",
            "hypothesis": "Split KV should reduce long-context latency.",
            "applicability": "Long-context attention shapes.",
            "expected_benefit": "Reduce latency by exposing CTA parallelism.",
            "plan": ["Compile a bounded probe"],
        },
    })["direction_id"]
    proposed = workspace / "directions" / "proposed" / f"{direction_id}.json"
    active = workspace / "directions" / "in_progress" / f"{direction_id}.json"
    assert proposed.is_file()
    assert not active.exists()

    service.execute({
        "operation": "direction_update",
        "request": {
            "action": "start",
            "direction_id": direction_id,
            "analysis": "This is the highest-value bounded probe.",
        },
    })
    assert not proposed.exists()
    assert active.is_file()
    assert json.loads(active.read_text())["status"] == "in_progress"

    progress = (workspace / "PROGRESS.md").read_text()
    assert "### Proposed\n\n_None._" in progress
    assert "### In Progress" in progress
    assert direction_id in progress

    service.execute({
        "operation": "direction_update",
        "request": {
            "action": "block",
            "direction_id": direction_id,
            "analysis": "The required compiler feature is unavailable.",
            "hypothesis_status": "unresolved",
            "reopen_condition": "The target compiler exposes the required feature.",
        },
    })
    progress = (workspace / "PROGRESS.md").read_text()
    assert (
        "**Current real blocker:** Split KV — The required compiler feature is unavailable; "
        "reopen when: The target compiler exposes the required feature."
    ) in progress


def test_episode_handoff_requires_terminal_direction_or_enabled_peer_share(tmp_path):
    def service_for(name: str, *, sharing: bool = False):
        workspace = tmp_path / name / "workspace"
        workspace.mkdir(parents=True)
        journals = tmp_path / name / "scope" / "journals"
        evidence = journals / "episodes" / "e00000001"
        evidence.mkdir(parents=True)
        initialize_journal(
            evidence / "journal.json",
            episode=1,
            base_commit="a" * 40,
            branch="work",
            tool_modules=("directions",),
        )
        origin = {"epoch": 1, "trajectory": 1, "episode": 1}
        if sharing:
            origin["cross_trajectory_sharing"] = True
        service = SupervisorJournalService(
            workspace=workspace,
            campaign_root=journals,
            evidence_root=evidence,
            research_root=tmp_path / name / "research",
            origin=origin,
        )
        direction_id = service.execute({
            "operation": "direction_update",
            "request": {
                "action": "propose",
                "name": "Split KV",
                "change_scope": "major_redesign",
                "hypothesis": "Split KV should improve long-context latency.",
                "applicability": "Long-context attention shapes.",
                "expected_benefit": "Reduce latency by increasing KV parallelism.",
                "plan": ["Compile a probe", "Measure a candidate family"],
            },
        })["direction_id"]
        service.execute({
            "operation": "direction_update",
            "request": {
                "action": "start",
                "direction_id": direction_id,
                "analysis": "Highest-value structural path.",
            },
        })
        return service, direction_id, evidence / "journal.json"

    standalone, direction_id, journal = service_for("standalone")
    with pytest.raises(AgentRequestError) as premature:
        standalone.execute({
            "operation": "episode_report",
            "request": {
                "status": "pivot",
                "handoff_reason": "direction_terminal",
                "summary": "A checkpoint exists but the Direction still has useful work.",
            },
        })
    assert premature.value.response["error"]["code"] == "handoff_not_ready"
    assert premature.value.response["error"]["active_direction_ids"] == [direction_id]

    with pytest.raises(AgentRequestError) as unavailable:
        standalone.execute({
            "operation": "episode_report",
            "request": {
                "status": "pivot",
                "handoff_reason": "cross_trajectory_share",
                "summary": "Try to publish a standalone checkpoint.",
            },
        })
    assert unavailable.value.response["error"]["code"] == (
        "cross_trajectory_sharing_unavailable"
    )

    standalone.execute({
        "operation": "direction_update",
        "request": {
            "action": "complete",
            "direction_id": direction_id,
            "analysis": "The planned probes and candidate family are exhausted.",
            "hypothesis_status": "unresolved",
        },
    })
    accepted = standalone.execute({
        "operation": "episode_report",
        "request": {
            "status": "pivot",
            "handoff_reason": "direction_terminal",
            "summary": "The selected Direction is complete.",
        },
    })
    assert accepted["status"] == "accepted"
    assert load_journal(journal)["outcome"]["handoff_reason"] == "direction_terminal"

    pooled, pooled_direction, pooled_journal = service_for("pooled", sharing=True)
    with pytest.raises(AgentRequestError) as missing_checkpoint:
        pooled.execute({
            "operation": "episode_report",
            "request": {
                "status": "pivot",
                "handoff_reason": "cross_trajectory_share",
                "summary": "Try to publish an unmeasured pivot to peer trajectories.",
            },
        })
    assert missing_checkpoint.value.response["error"]["code"] == (
        "handoff_reason_mismatch"
    )
    pooled_state = load_journal(pooled_journal)
    assert pooled_state["state"] == "in_progress"
    assert pooled_state["direction_events"][-1]["direction_id"] == pooled_direction
    assert pooled_state["direction_events"][-1]["action"] == "start"

    blocked, blocked_direction, blocked_journal = service_for("blocked")
    with pytest.raises(AgentRequestError) as active_blocker:
        blocked.execute({
            "operation": "episode_report",
            "request": {
                "status": "blocked",
                "handoff_reason": "blocked",
                "summary": "The required compiler capability is unavailable.",
                "blocker": "Compiler capability is missing.",
            },
        })
    assert active_blocker.value.response["error"]["code"] == "handoff_not_ready"
    blocked.execute({
        "operation": "direction_update",
        "request": {
            "action": "block",
            "direction_id": blocked_direction,
            "analysis": "The required compiler capability is unavailable.",
            "hypothesis_status": "unresolved",
            "reopen_condition": "The target compiler exposes the capability.",
        },
    })
    blocked_result = blocked.execute({
        "operation": "episode_report",
        "request": {
            "status": "blocked",
            "handoff_reason": "blocked",
            "summary": "The required compiler capability is unavailable.",
            "blocker": "Compiler capability is missing.",
        },
    })
    assert blocked_result["status"] == "accepted"
    assert load_journal(blocked_journal)["outcome"]["handoff_reason"] == "blocked"

    # A later infrastructure blocker is valid after local Direction work already reached a
    # terminal state; there is no active Direction left to close a second time.
    completed, completed_direction, completed_journal = service_for("completed-then-blocked")
    completed.execute({
        "operation": "direction_update",
        "request": {
            "action": "complete",
            "direction_id": completed_direction,
            "analysis": "The planned Direction work is complete.",
            "hypothesis_status": "unresolved",
        },
    })
    completed_blocker = completed.execute({
        "operation": "episode_report",
        "request": {
            "status": "blocked",
            "handoff_reason": "blocked",
            "summary": "A separate infrastructure failure prevents final validation.",
            "blocker": "The evaluator service is unavailable.",
        },
    })
    assert completed_blocker["status"] == "accepted"
    assert load_journal(completed_journal)["outcome"]["handoff_reason"] == "blocked"


def test_episode_report_schema_and_prompt_require_explicit_handoff_reason():
    schema = journal_schema(
        "episode_report", {"status": "pivot"}, research_records=False
    )
    assert "handoff_reason" in schema["required"]
    assert schema["properties"]["handoff_reason"]["enum"] == [
        "direction_terminal",
        "cross_trajectory_share",
        "blocked",
    ]
    prompt = (
        Path(__file__).resolve().parents[1]
        / "src/aka/light/workflow/prompts/episode.md"
    ).read_text(encoding="utf-8")
    assert "Repeat `probe → candidate → measurement → revert/keep` cycles" in prompt
    assert "pooled search must publish a reusable measured checkpoint to peers" in prompt
    assert "plausible full-workload latency impact and applicability" in prompt
    assert "not to discard a higher-upside path" in prompt
    assert "Use local Git to manage the Kernel development history" in prompt


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (
            "candidate_ready",
            "Select a current-Episode Experiment that cites a passing standard full Evaluate",
        ),
        (
            "pivot",
            "A pivot does not nominate a candidate: omit selected_experiment_id",
        ),
        (
            "blocked",
            "A blocked report does not nominate a candidate: omit selected_experiment_id",
        ),
    ],
)
def test_episode_report_recovery_guidance_matches_status_schema(status, expected):
    request = {
        "operation": "episode_report",
        "request": {
            "status": status,
            "handoff_reason": (
                "blocked" if status == "blocked" else "direction_terminal"
            ),
            "summary": "repair this report",
        },
    }
    response = {
        "ok": False,
        "repairable": True,
        "issues": [],
        "recovery": [{"instruction": REPAIR_REQUEST}],
        "error": {
            "code": "invalid_request",
            "message": "invalid report",
            "next_action": REPAIR_REQUEST,
        },
    }
    projected = augment_error(
        response,
        route="/v1/journal/execute",
        request=request,
        research_records=True,
        directions_enabled=True,
        experiments_enabled=True,
    )
    guidance = projected["error"]["next_action"]
    assert expected in guidance
    assert "Only candidate_ready may use cross_trajectory_share" in guidance


def test_direction_proposal_schema_requires_applicability_and_expected_benefit():
    schema = journal_schema(
        "direction_update", {"action": "propose"}, research_records=True
    )
    assert "applicability" in schema["required"]
    assert "expected_benefit" in schema["required"]
    assert schema["properties"]["applicability"]["type"] == "string"
    assert schema["properties"]["expected_benefit"]["type"] == "string"
    assert "change Kernel design" in schema["properties"]["hypothesis"]["description"]
    assert "implementation decision" in schema["properties"]["expected_benefit"]["description"]
    assert "probe-led Direction" in schema["properties"]["change_scope"]["description"]


def test_episode_may_sequentially_start_more_than_three_directions(tmp_path):
    campaign = tmp_path / "scope" / "journals"
    evidence = campaign / "episodes" / "e00000001"
    evidence.mkdir(parents=True)
    journal = evidence / "journal.json"
    initialize_journal(
        journal,
        episode=1,
        base_commit="a" * 40,
        branch="work",
        tool_modules=("directions",),
    )

    for index in range(6):
        proposed = update_direction(
            journal,
            campaign,
            {
                "action": "propose",
                "name": f"Direction {index}",
                "change_scope": "local",
                "hypothesis": f"Probe {index} can select a better Kernel path.",
                "applicability": "The current workload.",
                "expected_benefit": "Resolve one consequential implementation choice.",
                "plan": ["Run the discriminating probe."],
            },
        )
        direction_id = proposed["direction_id"]
        update_direction(
            journal,
            campaign,
            {
                "action": "start",
                "direction_id": direction_id,
                "analysis": "Highest-value unresolved choice.",
            },
        )
        update_direction(
            journal,
            campaign,
            {
                "action": "complete",
                "direction_id": direction_id,
                "analysis": "The bounded probe resolved this choice.",
                "hypothesis_status": "unresolved",
            },
        )

    events = load_journal(journal)["direction_events"]
    assert sum(event["action"] == "start" for event in events) == 6


def test_second_local_direction_start_returns_precise_recovery(tmp_path):
    campaign = tmp_path / "scope" / "journals"
    evidence = campaign / "episodes" / "e00000001"
    evidence.mkdir(parents=True)
    journal = evidence / "journal.json"
    initialize_journal(
        journal,
        episode=1,
        base_commit="a" * 40,
        branch="work",
        tool_modules=("directions",),
    )

    direction_ids = []
    for index in range(2):
        direction_ids.append(
            update_direction(
                journal,
                campaign,
                {
                    "action": "propose",
                    "name": f"Direction {index}",
                    "change_scope": "local",
                    "hypothesis": f"Probe {index} can select a better Kernel path.",
                    "applicability": "The current workload.",
                    "expected_benefit": "Resolve one consequential implementation choice.",
                    "plan": ["Run the discriminating probe."],
                },
            )["direction_id"]
        )
    update_direction(
        journal,
        campaign,
        {
            "action": "start",
            "direction_id": direction_ids[0],
            "analysis": "Highest-value unresolved choice.",
        },
    )

    second = update_direction(
        journal,
        campaign,
        {
            "action": "start",
            "direction_id": direction_ids[1],
            "analysis": "Run another independent bounded investigation.",
        },
    )
    assert second["status"] == "recorded"
    starts = [
        event["direction_id"]
        for event in load_journal(journal)["direction_events"]
        if event["action"] == "start"
    ]
    assert starts == direction_ids


def test_completion_diagnosis_uses_public_terms_and_actionable_repair(tmp_path, monkeypatch):
    import aka.light.workflow.episode as episode_module

    monkeypatch.setattr(
        episode_module,
        "validate_terminal",
        lambda *args, **kwargs: "published Episode handoff state is missing",
    )
    campaign = LongHorizonCampaign.__new__(LongHorizonCampaign)
    campaign.base_campaign = SimpleNamespace(
        read_runtime_episode_journal=lambda _episode: {},
    )
    diagnosis = campaign._completion_check(
        CandidateHandle(1, "a" * 40, "work", tmp_path),
        tmp_path / "journal.json",
        EpisodeHandoff("pivot"),
    )

    assert "Runtime-owned" in diagnosis
    assert "identical accepted episode-report" in diagnosis
    assert "report the concrete blocker" in diagnosis
    assert "private runtime journal" not in diagnosis.lower()


def test_accepted_legacy_report_remains_exactly_replayable_after_schema_upgrade(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    journals = tmp_path / "scope" / "journals"
    evidence = journals / "episodes" / "e00000001"
    evidence.mkdir(parents=True)
    journal_path = evidence / "journal.json"
    current = initialize_journal(
        journal_path,
        episode=1,
        base_commit="a" * 40,
        branch="work",
        tool_modules=(),
    )
    legacy_report = {"status": "pivot", "summary": "Legacy terminal report."}
    current.update(
        state="pivot",
        outcome={
            "summary": "Legacy terminal report.",
            "next_directions": [],
            "selected_experiment_id": None,
            "blocker": None,
            "gateway_record_ids": [],
        },
        candidate_commit=None,
        finalized_at="2026-11-01T00:00:00+00:00",
        submitted_report=legacy_report,
    )
    private_write(journal_path, current)
    service = SupervisorJournalService(
        workspace=workspace,
        campaign_root=journals,
        evidence_root=evidence,
        research_root=tmp_path / "research",
    )

    replayed = service.execute({
        "operation": "episode_report",
        "request": legacy_report,
    })

    assert replayed["status"] == "accepted"
    assert json.loads(
        (workspace / ".atrex_long_horizon/handoff.json").read_text()
    ) == {"status": "pivot"}


def test_progress_is_projected_even_when_research_modules_are_disabled(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    journals = tmp_path / "scope" / "journals"
    evidence = journals / "episodes" / "e00000001"
    evidence.mkdir(parents=True)
    initialize_journal(
        evidence / "journal.json",
        episode=1,
        base_commit="a" * 40,
        branch="work",
        tool_modules=(),
    )
    service = SupervisorJournalService(
        workspace=workspace,
        campaign_root=journals,
        evidence_root=evidence,
        research_root=tmp_path / "research",
    )
    record_id, _ = _measurement(
        MeasurementStore(tmp_path / "scope" / "measurements"),
        b"def kernel():\n    return 1\n",
        "unrecorded-mode",
    )

    service.publish_research_files()

    progress = (workspace / "PROGRESS.md").read_text()
    assert "# Current state" in progress
    assert "No eligible full-Evaluate history yet" in progress
    assert "## Directions" not in progress
    assert "directions/<status>" not in progress
    assert "experiments/<experiment_id>" not in progress
    assert "**Hypothesis:**" not in progress
    assert (workspace / "records").is_dir()
    assert (workspace / "records" / f"{record_id}.json").is_file()
    assert not (workspace / "directions").exists()
    assert not (workspace / "experiments").exists()


def test_measurements_checkpoint_working_and_best_lineage_commits(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    journals = tmp_path / "scope" / "journals"
    evidence = journals / "episodes" / "e00000001"
    evidence.mkdir(parents=True)
    initialize_journal(
        evidence / "journal.json",
        episode=1,
        base_commit="a" * 40,
        branch="work",
        tool_modules=(),
    )
    service = SupervisorJournalService(
        workspace=workspace,
        campaign_root=journals,
        evidence_root=evidence,
        research_root=tmp_path / "research",
        origin={"trajectory": 1, "episode": 1},
    )
    first, slower, diagnostic = "1" * 40, "2" * 40, "3" * 40
    service.observe_measurement({
        "operation": "evaluate",
        "status": "completed",
        "kernel_commit_id": first,
        "result": {
            "correct": True,
            "mode": "full",
            "input_scope": "contract",
            "latency_us_geomean": 10.0,
        },
    })
    service.observe_measurement({
        "operation": "evaluate",
        "status": "completed",
        "kernel_commit_id": slower,
        "result": {
            "correct": True,
            "mode": "full",
            "input_scope": "contract",
            "latency_us_geomean": 12.0,
        },
    })
    service.observe_measurement({
        "operation": "profile",
        "status": "failed",
        "kernel_commit_id": diagnostic,
        "result": {"error": {"error_class": "timeout"}},
    })
    # ABBA has two identities and must not replace the single working HEAD.
    service.observe_measurement({
        "operation": "evaluate",
        "status": "completed",
        "candidate_kernel_commit_id": "4" * 40,
        "baseline_kernel_commit_id": first,
        "result": {"correct": True, "mode": "full", "input_scope": "contract"},
    })

    state = service.research.read()["lineages"]["T1"]
    assert state["working_kernel_id"] == diagnostic
    assert state["last_correct_kernel_id"] == slower
    assert state["lineage_best_kernel_id"] == first
    assert state["lineage_best_latency_us"] == 10.0
    service.publish_research_files(synchronize=False)
    progress = (workspace / "PROGRESS.md").read_text()
    assert f"**Last measured working Kernel commit ID:** `{diagnostic}`" in progress
    assert f"**Best verified lineage Kernel commit ID:** `{first}`" in progress


def test_progress_projection_mentions_only_enabled_experiment_module(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    journals = tmp_path / "scope" / "journals"
    evidence = journals / "episodes" / "e00000001"
    evidence.mkdir(parents=True)
    initialize_journal(
        evidence / "journal.json",
        episode=1,
        base_commit="a" * 40,
        branch="work",
        tool_modules=("experiments",),
    )
    service = SupervisorJournalService(
        workspace=workspace,
        campaign_root=journals,
        evidence_root=evidence,
        research_root=tmp_path / "research",
    )

    service.publish_research_files()

    progress = (workspace / "PROGRESS.md").read_text()
    assert "experiments/<experiment_id>/experiment.json" in progress
    assert "## Directions" not in progress
    assert "directions/<status>" not in progress
    assert "**Hypothesis:**" not in progress


@pytest.mark.parametrize(
    ("modules", "required", "unavailable"),
    [
        (
            ("directions",),
            ('"action": "propose"', "derived_from_direction_ids", "gateway_record_ids"),
            "record-experiment",
        ),
        (
            ("experiments",),
            ('"interpretation"', "selected_experiment_id", "wiki_usage_status"),
            "update-direction",
        ),
    ],
)
def test_partial_research_modes_install_complete_module_specific_reference(
    tmp_path, modules, required, unavailable
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    from aka.light.workspace.assets import link_runtime

    link_runtime(workspace, research_records=True, research_modules=modules)
    journal = (
        workspace / "skills/runtime-records/references/journal.md"
    ).read_text()
    for marker in required:
        assert marker in journal
    assert unavailable not in journal


@pytest.mark.parametrize("direction_id", [None, "direction_" + "2" * 32])
def test_compact_experiment_publishes_legacy_handoff(tmp_path, direction_id):
    workspace = tmp_path / "workspace"
    (workspace / ".atrex_long_horizon").mkdir(parents=True)
    journals = tmp_path / "journals"
    evidence = journals / "episodes" / "e00000001"
    evidence.mkdir(parents=True)
    current = initialize_journal(
        evidence / "journal.json",
        episode=1,
        base_commit="a" * 40,
        branch="episode",
        tool_modules=(
            ("experiments",)
            if direction_id is None
            else ("directions", "experiments")
        ),
    )
    experiment_id = "experiment_" + "1" * 32
    experiment = {
        "experiment_id": experiment_id,
        "sequence": 1,
        "recorded_at": "2026-11-01T00:00:00+00:00",
        "name": "compact experiment",
        "intent": "Measure the proposed Kernel change.",
        "gateway_record_ids": [],
        "interpretation": "The measured result supports the change.",
    }
    if direction_id is not None:
        experiment["direction_id"] = direction_id
    current.update(
        state="candidate_ready",
        experiments=[experiment],
        outcome={
            "summary": "candidate ready",
            "selected_experiment_id": experiment_id,
        },
        candidate_commit="b" * 40,
    )
    service = SupervisorJournalService(
        workspace=workspace,
        campaign_root=journals,
        evidence_root=evidence,
    )

    service._publish_legacy_report(current)

    projection = json.loads(
        (workspace / ".atrex_long_horizon/journal.json").read_text()
    )
    assert projection["experiments"][0]["result"] == (
        "The measured result supports the change."
    )
    assert json.loads(
        (workspace / ".atrex_long_horizon/handoff.json").read_text()
    ) == {"status": "candidate_ready", "candidate_commit": "b" * 40}


def test_legacy_journal_conflict_returns_supported_operator_recovery(tmp_path):
    workspace = tmp_path / "workspace"
    legacy_root = workspace / ".atrex_long_horizon"
    legacy_root.mkdir(parents=True)
    (legacy_root / "journal.json").write_text(
        json.dumps({"state": "candidate_ready", "experiments": [{"id": "legacy"}]}),
        encoding="utf-8",
    )
    journals = tmp_path / "journals"
    evidence = journals / "episodes" / "e00000001"
    evidence.mkdir(parents=True)
    initialize_journal(
        evidence / "journal.json",
        episode=1,
        base_commit="a" * 40,
        branch="episode",
    )
    service = SupervisorJournalService(
        workspace=workspace,
        campaign_root=journals,
        evidence_root=evidence,
    )

    with pytest.raises(AgentRequestError) as rejected:
        service._check_legacy_journal()
    action = rejected.value.response["error"]["next_action"]
    assert "compatible legacy Runtime" in action
    assert "<JOURNAL_CLI>" not in action


def test_publish_if_changed_skips_identical_projection_and_heals_changes(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    assert publish_if_changed(workspace, "records/value.json", b"one") is True
    first = (workspace / "records/value.json").stat().st_mtime_ns
    assert publish_if_changed(workspace, "records/value.json", b"one") is False
    assert (workspace / "records/value.json").stat().st_mtime_ns == first
    assert publish_if_changed(workspace, "records/value.json", b"two") is True
    assert (workspace / "records/value.json").read_bytes() == b"two"


def test_direction_projection_cleanup_preserves_current_status_file(tmp_path):
    workspace = tmp_path / "workspace"
    direction_id = "direction_" + "a" * 32
    current = workspace / "directions" / "in_progress" / f"{direction_id}.json"
    stale = workspace / "directions" / "proposed" / f"{direction_id}.json"
    current.parent.mkdir(parents=True)
    stale.parent.mkdir(parents=True)
    current.write_text("current", encoding="utf-8")
    stale.write_text("stale", encoding="utf-8")

    _remove_stale_direction_projection(
        workspace,
        direction_id,
        current_status="in_progress",
    )

    assert current.read_text(encoding="utf-8") == "current"
    assert not stale.exists()


def test_existing_trajectory_session_resumes_on_first_invocation(tmp_path):
    resume_flags = []
    opened_session_ids = []

    class Native:
        supports_resume = True

        def __init__(self):
            self.environment = {"ATREX_TELEMETRY_ATTEMPT_ID": "invocation"}

        def invoke(self, _prompt, **kwargs):
            resume_flags.append(kwargs.get("resume"))
            return NativeInvocation(
                exit_status=0,
                timed_out=False,
                session_id="trajectory-session",
                stdout="done",
                stderr="",
                terminal_usage=TokenUsage(7, 0, 0, 0, 7, "exact"),
                events=(),
                capabilities=AgentRuntimeCapabilities(False, False, False),
                observation_errors=(),
                resume_usage_qualified=False,
            )

        def close(self):
            pass

    class Agent:
        def open_session(self, _runtime_id, _workspace, **kwargs):
            opened_session_ids.append(kwargs.get("session_id"))
            return Native()

    result = LongSessionRunner(
        agent=Agent(),
        agent_cli="claude",
        usage_policy=lambda turn: turn.terminal_usage.total_tokens or 0,
    ).run(
        tmp_path,
        "Continue the next Episode.",
        episode_timeout_seconds=60,
        session_id="trajectory-session",
        resume_session=True,
        initial_purpose="trajectory_continuation",
    )

    assert result.session_id == "trajectory-session"
    assert opened_session_ids == ["trajectory-session"]
    assert resume_flags == [True]
    assert [row.purpose for row in result.invocations] == ["trajectory_continuation"]


def _compaction_connection_loss(session_id):
    return json.dumps({
        "type": "assistant",
        "session_id": session_id,
        "parent_tool_use_id": None,
        "isApiErrorMessage": True,
        "message": {
            "content": [{
                "type": "text",
                "text": (
                    "Prompt is too long · automatic compaction failed: "
                    "API Error: Connection lost mid-response. "
                    "The response above may be incomplete."
                ),
            }],
        },
    })


class _SequenceNative:
    supports_resume = True

    def __init__(self, agent):
        self.agent = agent
        self.environment = {"ATREX_TELEMETRY_ATTEMPT_ID": "invocation"}

    def invoke(self, prompt, **kwargs):
        self.agent.prompts.append(prompt)
        self.agent.resume_flags.append(bool(kwargs.get("resume")))
        return self.agent.invocations.pop(0)

    def close(self):
        pass


class _SequenceAgent:
    def __init__(self, invocations):
        self.invocations = list(invocations)
        self.prompts = []
        self.resume_flags = []
        self.opened_sessions = []

    def open_session(self, _runtime_id, _workspace, **kwargs):
        self.opened_sessions.append(kwargs.get("session_id"))
        return _SequenceNative(self)


def _invocation(exit_status, session_id, tokens, stdout="done"):
    return NativeInvocation(
        exit_status=exit_status,
        timed_out=False,
        session_id=session_id,
        stdout=stdout,
        stderr="",
        terminal_usage=TokenUsage(tokens, 0, 0, 0, tokens, "exact"),
        events=(),
        capabilities=AgentRuntimeCapabilities(False, False, False),
        observation_errors=(),
        resume_usage_qualified=False,
    )


def test_compaction_connection_loss_resumes_same_claude_session(
    tmp_path, monkeypatch
):
    agent = _SequenceAgent([
        _invocation(
            1,
            "session-1",
            11,
            _compaction_connection_loss("session-1"),
        ),
        _invocation(0, "session-1", 7),
    ])
    handoffs = iter([None, EpisodeHandoff(status="pivot")])
    monkeypatch.setattr(
        "aka.light.workflow.session.read_handoff", lambda _path: next(handoffs)
    )

    result = LongSessionRunner(
        agent=agent,
        agent_cli="claude",
        usage_policy=lambda turn: turn.terminal_usage.total_tokens or 0,
    ).run(
        tmp_path,
        "Optimize.",
        episode_timeout_seconds=60,
        handoff_path=tmp_path / "handoff.json",
        handoff_resumes=2,
        completion_check=lambda _handoff: "",
    )

    assert result.exit_status == 0
    assert result.handoff == EpisodeHandoff(status="pivot")
    assert result.resume_count == 1
    assert agent.resume_flags == [False, True]
    assert "transient Prompt is too long" in agent.prompts[1]
    assert [row.purpose for row in result.invocations] == [
        "optimization", "handoff_resume",
    ]


def test_repeated_compaction_loss_uses_one_fresh_recovery_session(
    tmp_path, monkeypatch
):
    failure = _compaction_connection_loss("session-1")
    agent = _SequenceAgent([
        _invocation(1, "session-1", 11, failure),
        _invocation(1, "session-1", 13, failure),
        _invocation(0, "session-2", 7),
    ])
    handoffs = iter([None, None, EpisodeHandoff(status="pivot")])
    monkeypatch.setattr(
        "aka.light.workflow.session.read_handoff", lambda _path: next(handoffs)
    )

    result = LongSessionRunner(
        agent=agent,
        agent_cli="claude",
        usage_policy=lambda turn: turn.terminal_usage.total_tokens or 0,
    ).run(
        tmp_path,
        "Optimize.",
        episode_timeout_seconds=60,
        handoff_path=tmp_path / "handoff.json",
        handoff_resumes=1,
        completion_check=lambda _handoff: "",
    )

    assert result.exit_status == 0
    assert result.handoff == EpisodeHandoff(status="pivot")
    assert result.resume_count == 1
    assert agent.resume_flags == [False, True, False]
    assert len(agent.opened_sessions) == 2
    assert "Recover the SAME Episode" in agent.prompts[2]
    assert "do not reset or repeat completed measurements" in agent.prompts[2]
    assert [row.purpose for row in result.invocations] == [
        "optimization", "handoff_resume", "compaction_recovery",
    ]


def test_stable_session_home_root_survives_episode_workspace_changes(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "aka.light.agent.sandbox.sandbox_executable", lambda _environment: "/usr/bin/bwrap"
    )
    first = tmp_path / "episodes" / "e0001" / "workspace"
    second = tmp_path / "episodes" / "e0002" / "workspace"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    home_root = tmp_path / "trajectory-session-homes"
    environment = {
        "HOME": str(tmp_path / "operator-home"),
        SESSION_HOME_ROOT_ENV: str(home_root),
    }

    first_environment = prepare_agent_environment(
        first, dict(environment), "trajectory-session"
    )
    second_environment = prepare_agent_environment(
        second, dict(environment), "trajectory-session"
    )
    other_environment = prepare_agent_environment(
        second, dict(environment), "different-session"
    )

    assert first_environment["HOME"] == second_environment["HOME"]
    assert second_environment["HOME"] != other_environment["HOME"]
    assert Path(first_environment["HOME"]).is_relative_to(home_root.resolve())


def test_supervisor_state_persists_trajectory_session():
    state = SupervisorState(
        session_id="trajectory-session", session_backend="claude"
    )
    restored = SupervisorState.from_dict(state.as_dict())
    assert restored.session_id == "trajectory-session"
    assert restored.session_backend == "claude"


def _finalized_journal(path: Path, *, episode: int, suffix: str, record_id: str) -> tuple[str, str]:
    direction_id = "direction_" + suffix * 32
    experiment_id = "experiment_" + suffix * 32
    initialize_journal(
        path,
        episode=episode,
        base_commit="a" * 40,
        branch="episode",
        memory_version=1,
    )
    value = load_journal(path)
    value.update(state="pivot", finalized_at="2026-10-30T00:00:00+00:00")
    value["direction_events"] = [
        {
            "direction_event_id": "directionevent_" + suffix * 32,
            "direction_id": direction_id,
            "action": "propose",
            "name": f"direction-{suffix}",
            "change_scope": "major_redesign",
            "hypothesis": "A falsifiable shared hypothesis",
            "plan": ["implement", "measure"],
            "stop_conditions": [],
            "recorded_at": "2026-10-30T00:00:00+00:00",
            "supporting_experiment_ids": [],
        },
        {
            "direction_event_id": "directionevent_" + ("c" if suffix != "c" else "d") * 32,
            "direction_id": direction_id,
            "action": "start",
            "analysis": "Started",
            "recorded_at": "2026-10-30T00:01:00+00:00",
            "supporting_experiment_ids": [],
            "hypothesis_status": None,
        },
        {
            "direction_event_id": "directionevent_" + ("e" if suffix != "e" else "f") * 32,
            "direction_id": direction_id,
            "action": "defer",
            "analysis": "Deferred after measurement",
            "recorded_at": "2026-10-30T00:02:00+00:00",
            "supporting_experiment_ids": [experiment_id],
            "hypothesis_status": "unresolved",
        },
    ]
    value["experiments"] = [
        {
            "experiment_id": experiment_id,
            "sequence": 1,
            "recorded_at": "2026-10-30T00:01:30+00:00",
            "name": f"experiment-{suffix}",
            "hypothesis": "Measure the candidate",
            "change": "Changed the schedule",
            "gateway_record_ids": [record_id],
            "analysis": "The measurement is reusable evidence",
            "direction_id": direction_id,
        }
    ]
    private_write(path, value)
    return direction_id, experiment_id


def _runtime_projection(
    tmp_path: Path,
    *,
    tool_modules: tuple[str, ...],
) -> tuple[Path, dict[str, object], str]:
    private_path = tmp_path / "private.json"
    public_path = tmp_path / "journal.json"
    candidate = "b" * 40
    private = initialize_journal(
        private_path,
        episode=1,
        base_commit="a" * 40,
        branch="episode-1",
        tool_modules=tool_modules,
    )
    private.update(
        state="candidate_ready",
        outcome={
            "summary": "Measured candidate is ready.",
            "next_directions": [],
            "gateway_record_ids": ["gateway-" + "1" * 32],
        },
        candidate_commit=candidate,
        candidate_kernel_id="c" * 40,
        finalized_at="2026-10-31T00:00:00+00:00",
    )
    private_write(private_path, private)
    public = dict(
        private,
        schema_version=1,
        runtime_journal_projection=True,
    )
    public["candidate_kernel_commit_id"] = public.pop("candidate_kernel_id")
    public.pop("runtime_managed")
    public_path.write_text(json.dumps(public), encoding="utf-8")
    return public_path, private, candidate


def test_direction_only_runtime_projection_accepts_candidate_without_experiment(tmp_path):
    public_path, private, candidate = _runtime_projection(
        tmp_path,
        tool_modules=("directions",),
    )

    assert validate_terminal(
        public_path,
        expected_episode=1,
        base_commit="a" * 40,
        branch="episode-1",
        state="candidate_ready",
        candidate_commit=candidate,
        runtime_journal_loader=lambda: private,
    ) == ""


def test_experiment_runtime_projection_still_requires_candidate_experiment(tmp_path):
    public_path, private, candidate = _runtime_projection(
        tmp_path,
        tool_modules=("experiments",),
    )

    assert validate_terminal(
        public_path,
        expected_episode=1,
        base_commit="a" * 40,
        branch="episode-1",
        state="candidate_ready",
        candidate_commit=candidate,
        runtime_journal_loader=lambda: private,
    ) == "published Episode handoff has no structured Experiments"


def test_imported_measurement_is_readable_but_not_a_local_task_cache(tmp_path):
    source = MeasurementStore(tmp_path / "source")
    destination = MeasurementStore(tmp_path / "destination")
    record_id, kernel_id = _measurement(source, b"def kernel():\n    return 1\n", "source")

    imported = destination.import_record(source, record_id)

    assert imported == source.read(record_id)
    assert destination.read_kernel(kernel_id) == source.read_kernel(kernel_id)
    assert destination.kernel_records(kernel_id)[0]["gateway_record_id"] == record_id
    assert not (destination.root / "tasks").exists()


def test_same_source_under_distinct_git_commits_has_distinct_task_identity(tmp_path):
    store = MeasurementStore(tmp_path / "measurements")
    source = b"def kernel():\n    return 1\n"
    records = []
    for kernel_id in ("1" * 40, "2" * 40):
        identity = store.kernel(source, kernel_id)
        request = {
            "operation": "evaluate",
            "options": {},
            "inputs": {"kernel.py": hashlib.sha256(source).hexdigest()},
            "kernel_ids": {"candidate": kernel_id},
        }
        with store.reserve(request, {"candidate": identity}) as task:
            records.append(
                task.finish(
                    {"exit_code": 0, "stdout": "", "stderr": ""},
                    cacheable=True,
                )["gateway_record_id"]
            )
    assert records[0] != records[1]


def test_shared_research_is_visible_without_agent_facing_origin_fields(tmp_path):
    workspace = tmp_path / "workspace"
    (workspace / "scratch").mkdir(parents=True)
    journals = tmp_path / "journals"
    shared_direction = "direction_" + "1" * 32
    shared_experiment = "experiment_" + "2" * 32
    private_write(
        shared_research_path(journals),
        {
            "schema_version": 1,
            "revision": "revision-1",
            "through_epoch": 1,
            "directions": {
                shared_direction: {
                    "direction_id": shared_direction,
                    "name": "shared direction",
                    "change_scope": "major_redesign",
                    "hypothesis": "shared hypothesis",
                    "plan": ["test it"],
                    "stop_conditions": [],
                    "status": "deferred",
                    "analysis": "shared analysis",
                    "supporting_experiment_ids": [],
                    "associated_experiment_ids": [shared_experiment],
                    "hypothesis_status": "unresolved",
                }
            },
            "experiments": [
                {
                    "experiment_id": shared_experiment,
                    "sequence": 1,
                    "name": "shared experiment",
                    "hypothesis": "shared hypothesis",
                    "change": "shared change",
                    "gateway_record_ids": [],
                    "analysis": "shared result",
                    "direction_id": shared_direction,
                }
            ],
        },
    )
    current = journals / "episodes" / "e00000002" / "journal.json"
    initialize_journal(
        current,
        episode=2,
        base_commit="b" * 40,
        branch="episode",
        memory_version=1,
    )
    service = SupervisorJournalService(
        workspace=workspace,
        campaign_root=journals,
        evidence_root=current.parent,
    )

    service.publish_research_files()
    loaded = json.loads(
        (
            workspace / "directions" / "deferred" / f"{shared_direction}.json"
        ).read_text(encoding="utf-8")
    )
    experiment = json.loads(
        (workspace / "experiments" / shared_experiment / "experiment.json").read_text(
            encoding="utf-8"
        )
    )

    assert loaded["direction_id"] == shared_direction
    assert experiment["experiment_id"] == shared_experiment
    assert "origin" not in loaded and "shared" not in loaded
    assert "origin" not in experiment and "shared" not in experiment
    with pytest.raises(AgentRequestError) as rejected:
        service.execute(
            {
                "operation": "direction_update",
                "request": {
                    "action": "start",
                    "direction_id": shared_direction,
                    "analysis": "try to mutate shared state",
                },
            }
        )
    assert rejected.value.response["error"]["code"] == "shared_direction_read_only"
    with pytest.raises(AgentRequestError) as experiment_rejected:
        service.execute(
            {
                "operation": "experiment_record",
                "request": {
                    "name": "local measurement on shared direction",
                    "hypothesis": "must be rejected before evidence lookup",
                    "change": "none",
                    "gateway_record_ids": [],
                    "analysis": "none",
                    "direction_id": shared_direction,
                },
            }
        )
    assert experiment_rejected.value.response["error"]["code"] == "shared_direction_read_only"

    proposed = service.execute(
        {
            "operation": "direction_update",
            "request": {
                "action": "propose",
                "name": "local continuation",
                "change_scope": "major_redesign",
                "hypothesis": "refine the shared direction",
                "applicability": "The shared Direction's target regimes.",
                "expected_benefit": "Retain the peer gain while refining its bottleneck.",
                "plan": ["implement locally"],
                "relationship": "refinement",
                "derived_from_direction_ids": [shared_direction],
                "derived_from_experiment_ids": [],
            },
        }
    )
    assert proposed["direction_id"] != shared_direction


def test_search_epoch_seals_other_trajectory_research_and_measurements(tmp_path):
    driver = EpisodeSearchDriver.__new__(EpisodeSearchDriver)
    driver.root = tmp_path / "search"
    driver.children = {}
    identities = {}
    records = {}
    kernels = {}
    for number, suffix in ((1, "1"), (2, "2")):
        workspace = tmp_path / f"trajectory-{number}"
        workspace.mkdir()
        child = SimpleNamespace(workspace=workspace)
        driver.children[number] = child
        runtime_root = supervisor_campaign_root(workspace)
        store = MeasurementStore(runtime_root / "measurements")
        record_id, kernel_id = _measurement(
            store, f"def kernel_{number}():\n    return {number}\n".encode(), f"t{number}"
        )
        journal = runtime_root / "journals" / "episodes" / "e00000001" / "journal.json"
        identities[number] = _finalized_journal(
            journal, episode=1, suffix=suffix, record_id=record_id
        )
        research = ResearchStore(runtime_root / "research")
        research.observe_direction(
            {
                "direction_id": identities[number][0],
                "name": f"direction-{suffix}",
                "change_scope": "major_redesign",
                "status": "deferred",
                "plan": ["implement", "measure"],
            },
            origin={"epoch": 1, "trajectory": number, "episode": 1},
        )
        if number == 2:
            research.prioritize(
                identities[number][0],
                reason="continue the highest-impact redesign first",
                origin={"epoch": 1, "trajectory": number, "episode": 1},
            )
            research.progress(
                identities[number][0],
                0,
                origin={"epoch": 1, "trajectory": number, "episode": 1},
            )
        records[number], kernels[number] = record_id, kernel_id

    jobs = [
        {"epoch": 1, "trajectory": 1, "episode": 1, "status": "completed"},
        {"epoch": 1, "trajectory": 2, "episode": 1, "status": "completed"},
    ]
    driver.seal_epoch(1, jobs)
    driver.seal_epoch(1, jobs)

    for number in (1, 2):
        other = 2 if number == 1 else 1
        runtime_root = supervisor_campaign_root(driver.children[number].workspace)
        shared = json.loads(shared_research_path(runtime_root / "journals").read_text())
        assert set(shared["directions"]) == {identities[other][0]}
        assert shared["directions"][identities[other][0]]["proposed_by"] == f"T{other}"
        assert shared["research_queue"] == [identities[2][0], identities[1][0]]
        assert {row["experiment_id"] for row in shared["experiments"]} == {identities[other][1]}
        assert "origin" not in shared["directions"][identities[other][0]]
        if other == 2:
            assert shared["directions"][identities[other][0]]["completed_plan_steps"] == [0]
        store = MeasurementStore(runtime_root / "measurements")
        assert set(store.record_ids()) == set(records.values())
        assert store.read_kernel(kernels[other]).startswith(b"def kernel_")

    receipt = json.loads((driver.root / "epochs" / "e00000001-shared.json").read_text())
    assert set(receipt["gateway_record_ids"]) == set(records.values())
    assert driver.epoch_sealed(1)


def test_pooled_search_seals_before_publishing(tmp_path):
    calls = []

    class Driver:
        def identity(self):
            return "binding"

        def bootstrap(self):
            return {"latency_us_geomean": 10.0}

        def initialize(self, seed, trajectories):
            calls.append(("initialize", trajectories))

        def prepare_epoch(self, epoch, seed):
            calls.append(("prepare", epoch))

        def run_episode(self, epoch, trajectory, episode):
            return {
                "epoch": epoch,
                "trajectory": trajectory,
                "episode": episode,
                "retained_kernel": None,
                "tokens": 1,
            }

        def seal_epoch(self, epoch, jobs):
            calls.append(("seal", epoch))

        def epoch_sealed(self, epoch):
            return ("seal", epoch) in calls

        def publish_epoch(self, epoch, winner):
            calls.append(("publish", epoch))

        def tokens(self):
            return 1

    result = PooledSearch(
        tmp_path / "search",
        SearchOptions(epochs=1, trajectories=1, episodes_per_trajectory=1, max_parallel_episodes=1),
        Driver(),
    ).run()

    assert result == "search: completed Epoch budget"
    assert calls.index(("seal", 1)) < calls.index(("publish", 1))


def test_search_synchronization_rejects_unknown_mode():
    with pytest.raises(ValueError, match="synchronous or asynchronous"):
        SearchOptions(synchronization="eventually")


def test_search_role_specialization_is_optional_and_defaults_disabled():
    assert SearchOptions().role_specialization is False
    assert SearchOptions(role_specialization=True).role_specialization is True
    with pytest.raises(TypeError, match="role_specialization must be a boolean"):
        SearchOptions(role_specialization=1)


@pytest.mark.parametrize("enabled", [True, False])
def test_episode_search_role_specialization_prompt_switch(tmp_path, enabled):
    base = SimpleNamespace(
        workspace=tmp_path,
        platform="L20D",
        arch="sm_103",
        notes="none",
        research_records=False,
        research_modules=(),
        search_coordinate={
            "epoch": 1,
            "trajectory": 1,
            "episode": 1,
            "role_specialization": enabled,
        },
        module_options=SimpleNamespace(goal_episodes=False),
        specify_dsl=True,
        framework="Cuda",
        dsl_context="- DSL: `Cuda`",
        plugin_directive=lambda stage: "",
        tool_runtime=SimpleNamespace(pause_agent_during_requests=True),
    )
    prompt = LongHorizonCampaign(base)._prompt(
        episode=1,
        version=1,
        worktree=CandidateHandle(1, "a" * 40, "work", tmp_path),
        conversion_pending=False,
    )
    assert "Search location: Epoch 1, Trajectory 1, Episode 1" in prompt
    assert ("T1 role: bounded exploitation" in prompt) is enabled


@pytest.mark.parametrize(
    ("synchronization", "expected"),
    [
        ("synchronous", "at synchronous Epoch barriers"),
        ("asynchronous", "after each terminal Episode publication"),
    ],
)
def test_episode_search_prompt_describes_actual_publication_boundary(
    tmp_path, synchronization, expected
):
    base = SimpleNamespace(
        workspace=tmp_path,
        platform="L20D",
        arch="sm_103a",
        notes="none",
        research_records=False,
        research_modules=(),
        search_coordinate={
            "epoch": 1,
            "trajectory": 1,
            "episode": 1,
            "synchronization": synchronization,
            "role_specialization": False,
        },
        module_options=SimpleNamespace(goal_episodes=False),
        specify_dsl=True,
        framework="Cuda",
        dsl_context="- DSL: `Cuda`",
        plugin_directive=lambda stage: "",
        tool_runtime=SimpleNamespace(pause_agent_during_requests=True),
    )
    prompt = LongHorizonCampaign(base)._prompt(
        episode=1,
        version=1,
        worktree=CandidateHandle(1, "a" * 40, "work", tmp_path),
        conversion_pending=False,
    )
    assert expected in prompt


def test_asynchronous_search_advances_without_waiting_for_slow_peer(tmp_path):
    seed = {"latency_us_geomean": 10.0, "name": "seed"}
    first_trajectory_advanced = Event()
    slow_peer_released = Event()
    lock = Lock()
    calls = []
    token_count = 0

    class Driver:
        def identity(self):
            return "binding"

        def bootstrap(self):
            return seed

        def initialize(self, initial, trajectories):
            assert initial == seed and trajectories == 2

        def prepare_trajectory(
            self,
            epoch,
            trajectory,
            episode,
            working_kernel,
            official_kernel,
            shared_publication,
            peer_kernels,
        ):
            assert working_kernel == seed
            assert official_kernel == seed
            assert type(shared_publication) is int
            assert set(peer_kernels) == {"1", "2"}
            with lock:
                calls.append(("prepare", trajectory, episode))

        def run_episode(self, epoch, trajectory, episode):
            nonlocal token_count
            if (trajectory, episode) == (2, 1):
                assert first_trajectory_advanced.wait(2)
                slow_peer_released.set()
            elif (trajectory, episode) == (1, 2):
                first_trajectory_advanced.set()
            with lock:
                calls.append(("run", trajectory, episode))
                token_count += 1
            return {
                "epoch": epoch,
                "trajectory": trajectory,
                "episode": episode,
                "retained_kernel": None,
                "tokens": 1,
                "session_id": f"session-t{trajectory}",
            }

        def seal_epoch(self, publication, jobs):
            assert jobs
            assert all(job["status"] == "completed" for job in jobs)
            with lock:
                calls.append(("seal", publication))

        def publish_epoch(self, publication, winner):
            assert winner == seed
            with lock:
                calls.append(("publish", publication))

        def tokens(self):
            return token_count

        def interrupt(self):
            slow_peer_released.set()

    root = tmp_path / "search"
    result = PooledSearch(
        root,
        SearchOptions(
            epochs=2,
            trajectories=2,
            episodes_per_trajectory=1,
            max_parallel_episodes=2,
            synchronization="asynchronous",
        ),
        Driver(),
    ).run()

    assert result == "search: completed asynchronous Episode budget"
    assert slow_peer_released.is_set()
    assert calls.index(("run", 1, 2)) < calls.index(("run", 2, 1))
    state = read_state(root / "state.json")
    assert state["lineages"]["1"]["completed_episodes"] == 2
    assert state["lineages"]["2"]["completed_episodes"] == 2
    assert state["completed_epochs"] == 2
    assert state["asynchronous"]["latest_publication"] == 4


def test_wiki_query_missing_input_file_is_a_structured_agent_error(tmp_path):
    runtime = SupervisorRuntime.__new__(SupervisorRuntime)
    runtime.config = SimpleNamespace(wiki_enabled=True)
    runtime.wiki_provider = SimpleNamespace(cli_root=tmp_path / "wiki")
    capability = SimpleNamespace(workspace=tmp_path)

    with pytest.raises(AgentRequestError) as raised:
        runtime._wiki_command(
            capability,
            "query_nl",
            ["--file", "wiki-question.txt"],
            tmp_path / "staged",
            {},
        )

    response = raised.value.response
    assert response["error"]["code"] == "file_not_found"
    assert response["error"]["requested_path"] == "wiki-question.txt"
    assert response["issues"] == [
        {
            "path": "--file",
            "code": "file_not_found",
            "message": "Wiki query input file does not exist: wiki-question.txt",
        }
    ]
    assert "pass the query text directly" in response["error"]["next_action"]


def _source(text: str) -> bytes:
    policy, files = default_tree({"kernel.py": text.encode()})
    return pack(files, policy)


def _install(workspace: Path, text: str) -> bytes:
    source = _source(text)
    install(workspace, source)
    return source


def _controller_repository(workspace: Path, text: str) -> Path:
    _install(workspace, text)
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    subprocess.run(
        ["git", "config", "user.name", "Supervisor"], cwd=workspace, check=True
    )
    subprocess.run(
        ["git", "config", "user.email", "supervisor@localhost"],
        cwd=workspace,
        check=True,
    )
    subprocess.run(["git", "add", "."], cwd=workspace, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "baseline"], cwd=workspace, check=True
    )
    return workspace


def test_research_store_rebuilds_queue_progress_and_adoption(tmp_path):
    store = ResearchStore(tmp_path / "research")
    first = "direction_" + "1" * 32
    second = "direction_" + "2" * 32
    for direction_id, name in ((first, "local"), (second, "redesign")):
        store.observe_direction(
            {
                "direction_id": direction_id,
                "name": name,
                "change_scope": "major_redesign" if direction_id == second else "local",
                "status": "proposed",
                "plan": ["probe", "measure"],
            },
            origin={"trajectory": 1},
        )
    store.prioritize(second, before_direction_id=first, reason="higher impact")
    store.progress(second, 0, origin={"trajectory": 1})
    store.observe_direction(
        {
            "direction_id": second,
            "name": "redesign",
            "change_scope": "major_redesign",
            "status": "in_progress",
            "owner": "T1",
            "plan": ["probe", "measure"],
        },
        origin={"trajectory": 1},
    )
    store.observe_direction(
        {
            "direction_id": second,
            "name": "redesign",
            "change_scope": "major_redesign",
            "status": "deferred",
            "owner": "T1",
            "plan": ["probe", "measure"],
        },
        origin={"trajectory": 1},
    )
    # Reopening may produce exactly the same visible in-progress projection as
    # an earlier lease.  Historical deduplication must not leave it deferred.
    store.observe_direction(
        {
            "direction_id": second,
            "name": "redesign",
            "change_scope": "major_redesign",
            "status": "in_progress",
            "owner": "T1",
            "plan": ["probe", "measure"],
        },
        origin={"trajectory": 1},
    )
    store.adopt_kernel(
        "3" * 40,
        source_ref="refs/atrex/shared/T2/e0001/head",
        reason="continue the peer implementation",
        origin={"trajectory": 1},
    )
    store.synchronize_queue([first, second], origin={"epoch": 2})
    store.prioritize(second, before_direction_id=first, reason="local temporary reorder")
    store.synchronize_queue([first, second], origin={"epoch": 3})

    rebuilt = ResearchStore(tmp_path / "research").read()
    assert rebuilt["queue"] == [first, second]
    assert rebuilt["directions"][second]["completed_plan_steps"] == [0]
    assert rebuilt["lineages"]["T1"]["working_kernel_id"] == "3" * 40
    board = store.board(trajectory=1)
    assert [item["direction_id"] for item in board["open_queue"]] == [first, second]
    assert board["open_queue"][1]["status"] == "in_progress(T1)"
    assert board["open_queue"][1]["next_step"] == "measure"
    assert board["lineages"]["T1"]["working_kernel_commit_id"] == "3" * 40
    assert "working_kernel_id" not in board["lineages"]["T1"]


def test_research_store_terminal_projection_excludes_newer_live_episode(tmp_path):
    store = ResearchStore(tmp_path / "research")
    direction_id = "direction_" + "4" * 32
    store.observe_direction(
        {
            "direction_id": direction_id,
            "name": "terminal direction",
            "change_scope": "major_redesign",
            "status": "in_progress",
            "plan": ["probe", "measure"],
        },
        origin={"trajectory": 1, "episode": 1},
    )
    store.progress(direction_id, 0, origin={"trajectory": 1, "episode": 2})

    terminal = store.read_through_episode(1)

    assert terminal["directions"][direction_id]["completed_plan_steps"] == []
    assert terminal["directions"][direction_id]["next_step"] == "probe"
    assert len(store.events_through_episode(1)) == 1
    assert store.read()["directions"][direction_id]["completed_plan_steps"] == [0]


def test_research_store_tracks_multiple_active_directions_per_lineage(tmp_path):
    store = ResearchStore(tmp_path / "research")
    first = "direction_" + "a" * 32
    second = "direction_" + "b" * 32
    for direction_id in (first, second):
        store.observe_direction(
            {
                "direction_id": direction_id,
                "name": direction_id,
                "status": "in_progress",
                "owner": "T1",
                "plan": ["probe"],
            },
            origin={"trajectory": 1},
        )

    state = store.read()
    assert state["lineages"]["T1"]["active_direction_ids"] == [first, second]
    assert state["lineages"]["T1"]["active_direction_id"] == first

    store.observe_direction(
        {
            "direction_id": first,
            "name": first,
            "status": "completed",
            "owner": "T1",
            "plan": ["probe"],
        },
        origin={"trajectory": 1},
    )
    state = store.read()
    assert state["lineages"]["T1"]["active_direction_ids"] == [second]
    assert state["lineages"]["T1"]["active_direction_id"] == second


def test_research_store_uses_snapshot_tail_and_skips_unchanged_lineage(tmp_path, monkeypatch):
    store = ResearchStore(tmp_path / "research")
    values = {"working_kernel_id": "a" * 40}
    first = store.checkpoint_lineage(values, origin={"trajectory": 1, "episode": 1})
    second = store.checkpoint_lineage(values, origin={"trajectory": 1, "episode": 1})
    assert second["sequence"] == first["sequence"]

    # A valid compact snapshot serves ordinary reads without replaying every
    # historical event. Tail validation still happens inside read().
    monkeypatch.setattr(store, "_events", lambda: (_ for _ in ()).throw(
        AssertionError("complete event replay should not run")
    ))
    assert store.read()["lineages"]["T1"]["working_kernel_id"] == "a" * 40


def test_research_store_rotates_bounded_segments_and_rebuilds(tmp_path, monkeypatch):
    import aka.light.research.store as research_store

    monkeypatch.setattr(research_store, "MAX_EVENT_SEGMENT_BYTES", 512)
    store = ResearchStore(tmp_path / "research")
    for index in range(12):
        store.checkpoint_lineage(
            {"working_kernel_id": f"{index:040x}"},
            origin={"trajectory": 1, "episode": index + 1},
        )
    assert list((tmp_path / "research").glob("events-*.jsonl"))

    # Deleting the convenience snapshot exercises recovery across rotated
    # segments and the active tail without losing sequence history.
    (tmp_path / "research" / "snapshot.json").unlink()
    rebuilt = ResearchStore(tmp_path / "research").read()
    assert rebuilt["sequence"] == 12
    assert rebuilt["lineages"]["T1"]["working_kernel_id"] == f"{11:040x}"


@pytest.mark.parametrize(
    ("modules", "included", "excluded"),
    [
        (("directions",), "directions/<status>/*.json", "experiments/*/experiment.json"),
        (("experiments",), "experiments/*/experiment.json", "directions/<status>/*.json"),
        ((), "records/<gateway_record_id>.json", "experiments/*/experiment.json"),
    ],
)
def test_journal_repair_hint_mentions_only_enabled_modules(modules, included, excluded):
    hint = _journal_evidence_hint(modules)
    assert included in hint
    assert excluded not in hint


def test_public_shapes_are_canonical_first_five_and_tamper_evident(tmp_path):
    shapes = {
        str(index): {"init_kwargs": {"rank": index}, "input_kwargs": {"size": index * 2}}
        for index in (10, 2, 1, 8, 4, 3, 6)
    }
    private = tmp_path / "shapes.json"
    private.write_text(json.dumps(shapes), encoding="utf-8")
    payload = public_shapes_payload(private, count=5)
    assert list(payload["shapes"]) == ["1", "2", "3", "4", "6"]
    assert payload["selection"] == {
        "policy": "canonical_prefix", "count": 5, "total_shape_count": 7
    }
    public = tmp_path / "public_shapes.json"
    public.write_text(json.dumps(payload), encoding="utf-8")
    assert validate_public_shapes(
        public, private_shapes_path=private, count=5
    ) == payload
    payload["shapes"]["1"]["input_kwargs"]["size"] = 999
    public.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="exact deterministic"):
        validate_public_shapes(public, private_shapes_path=private, count=5)


def test_public_shapes_support_none_prefix_and_all(tmp_path):
    shapes = {
        str(index): {"init_kwargs": {"rank": index}, "input_kwargs": {"size": index * 2}}
        for index in (10, 2, 1, 8, 4, 3, 6)
    }
    private = tmp_path / "shapes.json"
    private.write_text(json.dumps(shapes), encoding="utf-8")

    assert public_shapes_payload(private)["shapes"] == shapes
    hidden = public_shapes_payload(private, count=0)
    assert hidden["shapes"] == {}
    assert hidden["selection"]["count"] == 0

    prefix = public_shapes_payload(private, count=2)
    assert list(prefix["shapes"]) == ["1", "2"]

    complete = public_shapes_payload(private, count="all")
    assert list(complete["shapes"]) == ["1", "2", "3", "4", "6", "8", "10"]
    assert complete["selection"] == {
        "policy": "canonical_prefix", "count": 7, "total_shape_count": 7
    }
    public = tmp_path / "public_shapes.json"
    public.write_text(json.dumps(complete), encoding="utf-8")
    assert validate_public_shapes(
        public, private_shapes_path=private, count="all"
    ) == complete
    with pytest.raises(ValueError, match="exact deterministic"):
        validate_public_shapes(public, private_shapes_path=private, count=2)


@pytest.mark.parametrize("value", [-1, True, "five", None])
def test_public_shapes_reject_invalid_selection(value, tmp_path):
    private = tmp_path / "shapes.json"
    private.write_text(
        json.dumps({"1": {"init_kwargs": {}, "input_kwargs": {}}}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="non-negative integer or 'all'"):
        public_shapes_payload(private, count=value)


def test_agent_git_persists_work_and_installs_runtime_source_refs(tmp_path):
    workspace = tmp_path / "workspace"
    source = _install(workspace, "def run():\n    return 1\n")
    bundle = tmp_path / "state" / "agent.bundle"
    repo = AgentGit(workspace, bundle)
    repo.prepare()
    first = repo.head()
    assert first
    (workspace / "impl" / "helper.py").write_text("VALUE = 2\n", encoding="utf-8")
    persisted = repo.persist()
    assert persisted != first
    runtime_commit = repo.install_runtime_ref(
        "refs/tags/atrex/incumbent", source, message="Runtime: incumbent kernel-test"
    )
    assert subprocess.run(
        ["git", "rev-parse", "refs/tags/atrex/incumbent"], cwd=workspace,
        text=True, capture_output=True, check=True,
    ).stdout.strip() == runtime_commit

    restored = tmp_path / "restored"
    restored_source = _install(restored, "def run():\n    return 1\n")
    assert restored_source == source
    resumed = AgentGit(restored, bundle)
    resumed.prepare()
    assert subprocess.run(
        ["git", "log", "--all", "--format=%s"], cwd=restored,
        text=True, capture_output=True, check=True,
    ).stdout.count("Runtime: preserve Episode WIP checkpoint") == 1
    assert (restored / "impl" / "helper.py").read_text() == "VALUE = 2\n"


def test_agent_git_head_is_kernel_id_only_for_clean_exact_source(tmp_path):
    workspace = tmp_path / "workspace"
    source = _install(workspace, "def run():\n    return 1\n")
    repository = AgentGit(workspace, tmp_path / "agent.bundle")
    repository.prepare()

    first = repository.kernel_commit(source)
    assert len(first) in {40, 64}

    (workspace / "directions" / "proposed").mkdir(parents=True)
    (workspace / "directions" / "proposed" / ("direction_" + "1" * 32 + ".json")).write_text(
        "{}\n", encoding="utf-8"
    )
    experiment = workspace / "experiments" / ("experiment_" + "2" * 32)
    experiment.mkdir(parents=True)
    (experiment / "experiment.json").write_text("{}\n", encoding="utf-8")
    (workspace / "PROGRESS.md").write_text("# Runtime projection\n", encoding="utf-8")
    assert repository.kernel_commit(source) == first

    (workspace / "kernel.py").write_text(
        "def run():\n    return 2\n", encoding="utf-8"
    )
    with pytest.raises(DirtyKernelWorktree) as dirty:
        repository.kernel_commit()
    assert "kernel.py" in dirty.value.paths

    subprocess.run(["git", "add", "kernel.py"], cwd=workspace, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "candidate"], cwd=workspace, check=True
    )
    second = repository.kernel_commit()
    assert second != first
    assert repository.kernel_commit(snapshot(workspace)) == second


def test_supervisor_seals_exact_agent_commit_without_recommit(tmp_path):
    controller = _controller_repository(
        tmp_path / "controller", "def run():\n    return 1\n"
    )
    base_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=controller,
        text=True, capture_output=True, check=True,
    ).stdout.strip()
    worktree = EpisodeWorktree.create(
        controller, 1, base_commit, tmp_path / "worktrees"
    )
    agent_workspace = tmp_path / "agent"
    _install(agent_workspace, "def run():\n    return 1\n")
    agent_git = AgentGit(agent_workspace, tmp_path / "agent.bundle")
    agent_git.prepare()
    (agent_workspace / "kernel.py").write_text(
        "def run():\n    return 2\n", encoding="utf-8"
    )
    subprocess.run(["git", "add", "kernel.py"], cwd=agent_workspace, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "agent candidate"],
        cwd=agent_workspace,
        check=True,
    )
    agent_commit = agent_git.kernel_commit()
    candidate_source = snapshot(agent_workspace)

    try:
        sealed = agent_git.seal_into(worktree.path, agent_commit, candidate_source)
        assert sealed == agent_commit
        assert subprocess.run(
            ["git", "rev-parse", f"refs/atrex/accepted/{agent_commit}"],
            cwd=worktree.path,
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip() == agent_commit
        assert subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=worktree.path,
            text=True, capture_output=True, check=True,
        ).stdout.strip() == base_commit
        violation, paths = worktree.validate_candidate(agent_commit)
        assert violation == ""
        assert paths == ["kernel.py"]
        assert snapshot(worktree.path) == candidate_source

        promotion = promote_candidate(
            controller,
            base_commit=base_commit,
            candidate_commit=agent_commit,
            episode=1,
            evidence={
                "episode": 1,
                "version": 1,
                "base_commit": base_commit,
                "episode_branch": worktree.branch,
                "accepted": True,
            },
            memory_version=1,
            memory_record={"version": "v1"},
        )
        assert promotion != agent_commit
        from aka.light.workspace.sources import from_git

        assert from_git(controller, promotion) == candidate_source
        assert subprocess.run(
            ["git", "rev-parse", f"refs/atrex/accepted/{agent_commit}"],
            cwd=controller,
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip() == agent_commit
    finally:
        worktree.remove(controller)


def test_agent_workspace_is_stable_across_episode_controller_worktrees(tmp_path):
    first_controller = _controller_repository(
        tmp_path / "controller-1", "def run():\n    return 1\n"
    )
    second_controller = tmp_path / "controller-2"
    subprocess.run(
        ["git", "clone", "-q", str(first_controller), str(second_controller)],
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Supervisor"],
        cwd=second_controller,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "supervisor@localhost"],
        cwd=second_controller,
        check=True,
    )
    (second_controller / "kernel.py").write_text(
        "def run():\n    return 2\n", encoding="utf-8"
    )
    subprocess.run(["git", "add", "kernel.py"], cwd=second_controller, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "new incumbent"],
        cwd=second_controller,
        check=True,
    )
    runtime_root = tmp_path / "runtime"
    agent_root = runtime_root / "agent-workspace"

    first = EpisodeWorkspace(
        first_controller,
        tmp_path / "evidence-1",
        adaptive_root=runtime_root / "adaptive-state",
        episode=1,
        persistent_root=agent_root,
    )
    assert first.prepare() == agent_root
    assert "records/<gateway_record_id>.json" in (
        agent_root / "WORKSPACE.md"
    ).read_text(encoding="utf-8")
    subprocess.run(
        ["git", "switch", "-c", "experiment/persistent"],
        cwd=agent_root,
        check=True,
        text=True,
        capture_output=True,
    )
    (agent_root / "kernel.py").write_text(
        "def run():\n    return 7\n", encoding="utf-8"
    )
    (agent_root / "scratch" / "probe.txt").write_text(
        "keep across episodes\n", encoding="utf-8"
    )
    first.publish()

    second = EpisodeWorkspace(
        second_controller,
        tmp_path / "evidence-2",
        adaptive_root=runtime_root / "adaptive-state",
        episode=2,
        persistent_root=agent_root,
    )
    assert second.prepare() == agent_root
    assert (agent_root / "kernel.py").read_text(encoding="utf-8").endswith(
        "return 7\n"
    )
    assert (agent_root / "scratch" / "probe.txt").read_text(encoding="utf-8") == (
        "keep across episodes\n"
    )
    assert subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=agent_root,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip() == "experiment/persistent"
    assert json.loads(
        (runtime_root / "agent-workspace-state.json").read_text(encoding="utf-8")
    )["workspace_mode"] == "persistent"


def test_runtime_rebinds_stable_agent_workspace_to_next_episode_journal(tmp_path):
    campaign = _controller_repository(
        tmp_path / "campaign", "def run():\n    return 1\n"
    )
    first_controller = _controller_repository(
        tmp_path / "controller-1", "def run():\n    return 1\n"
    )
    second_controller = tmp_path / "controller-2"
    subprocess.run(
        ["git", "clone", "-q", str(first_controller), str(second_controller)],
        check=True,
    )
    runtime = SupervisorRuntime(
        RuntimeConfig(hardware="L20D", workspace=campaign, wiki_enabled=False),
        workspace_provider=ManagedCandidateWorkspace(),
    )
    try:
        first = runtime.register_episode(
            first_controller,
            episode=1,
            base_commit=subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=first_controller,
                text=True, capture_output=True, check=True,
            ).stdout.strip(),
            branch="episode-1",
            memory_version=1,
            supervisor_git=True,
        )
        second = runtime.register_episode(
            second_controller,
            episode=2,
            base_commit=subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=second_controller,
                text=True, capture_output=True, check=True,
            ).stdout.strip(),
            branch="episode-2",
            memory_version=2,
            supervisor_git=True,
        )
        assert second == first
        assert runtime.journals[second].service.path == runtime.episode_journal_path(2)
        assert runtime.journals[first_controller.resolve()].service.path == (
            runtime.episode_journal_path(1)
        )
    finally:
        runtime.close()


def test_agent_git_restores_unmerged_branches_and_active_checkout(tmp_path):
    workspace = tmp_path / "workspace"
    source = _install(workspace, "def run():\n    return 1\n")
    bundle = tmp_path / "state" / "agent.bundle"
    repo = AgentGit(workspace, bundle)
    repo.prepare()
    subprocess.run(
        ["git", "switch", "-c", "experiment/ksplit4"], cwd=workspace, check=True,
        text=True, capture_output=True,
    )
    (workspace / "impl" / "ksplit.py").write_text(
        "KSPLIT = 4\n", encoding="utf-8"
    )
    repo.persist()

    restored = tmp_path / "restored"
    restored_source = _install(restored, "def run():\n    return 1\n")
    assert restored_source == source
    resumed = AgentGit(restored, bundle)
    resumed.prepare()

    assert subprocess.run(
        ["git", "branch", "--show-current"], cwd=restored,
        text=True, capture_output=True, check=True,
    ).stdout.strip() == "experiment/ksplit4"
    assert subprocess.run(
        ["git", "show-ref", "--verify", "refs/heads/work"], cwd=restored,
        text=True, capture_output=True, check=False,
    ).returncode == 0
    assert subprocess.run(
        ["git", "show-ref", "--verify", "refs/heads/experiment/ksplit4"], cwd=restored,
        text=True, capture_output=True, check=True,
    ).returncode == 0
    assert (restored / "impl" / "ksplit.py").read_text() == "KSPLIT = 4\n"


def test_epoch_git_exchange_filters_paths_and_preserves_peer_ref(tmp_path):
    source_workspace = tmp_path / "source"
    _install(source_workspace, "def run():\n    return 1\n")
    source = AgentGit(source_workspace, tmp_path / "source.bundle")
    source.prepare()
    (source_workspace / "impl" / "peer.py").write_text("KSPLIT = 2\n", encoding="utf-8")
    (source_workspace / "scratch").mkdir()
    (source_workspace / "scratch" / "secret.txt").write_text("private\n", encoding="utf-8")
    source.persist()

    export = tmp_path / "peer.bundle"
    receipt = export_epoch(
        source_workspace, export, trajectory=2, epoch=1, audit_path=tmp_path / "peer.json"
    )
    assert receipt["commit_count"] >= 1
    assert b"scratch/secret.txt" not in export.read_bytes()

    destination = tmp_path / "destination"
    _install(destination, "def run():\n    return 0\n")
    local = AgentGit(destination, tmp_path / "destination.bundle")
    local.prepare()
    local.persist()
    imported = import_exports(
        tmp_path / "destination.bundle",
        [export],
        notes={
            "refs/atrex/shared/T2/e0001/head": {
                "measurement_status": "measured", "kernel_id": "4" * 40
            }
        },
        audit_path=tmp_path / "import.json",
    )
    assert imported["shared_refs"] == ["refs/atrex/shared/T2/e0001/head"]
    assert imported["commit_mappings"] == [
        {
            "export": "peer.bundle",
            "source_ref": "refs/atrex/shared/T2/e0001/head",
            "original_agent_commit": receipt["original_agent_commit"],
            "imported_commit": imported["commit_mappings"][0]["imported_commit"],
        }
    ]
    assert imported["commit_mappings"][0]["imported_commit"]
    assert imported["commit_mappings"][0]["imported_commit"] == receipt["original_agent_commit"]

    # A trajectory-stable repository imports refs added to its recovery bundle
    # in place; it does not need a newly projected Episode workspace.
    local.prepare()
    assert subprocess.run(
        ["git", "show-ref", "--verify", "refs/atrex/shared/T2/e0001/head"],
        cwd=destination,
        text=True,
        capture_output=True,
        check=True,
    ).returncode == 0

    inspect = tmp_path / "inspect"
    inspect.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=inspect, check=True)
    subprocess.run(
        ["git", "fetch", str(tmp_path / "destination.bundle"),
         "+refs/atrex/*:refs/atrex/*", "+refs/notes/*:refs/notes/*"],
        cwd=inspect, check=True, capture_output=True,
    )
    tree = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", "refs/atrex/shared/T2/e0001/head"],
        cwd=inspect, text=True, capture_output=True, check=True,
    ).stdout.splitlines()
    assert tree == [
        ".atrex_source.json",
        ".gitignore",
        "impl/__init__.py",
        "impl/peer.py",
        "kernel.py",
    ]
    messages = subprocess.run(
        ["git", "log", "--format=%s", "refs/atrex/shared/T2/e0001/head"],
        cwd=inspect, text=True, capture_output=True, check=True,
    ).stdout.splitlines()
    original_messages = subprocess.run(
        ["git", "log", "--format=%s", receipt["original_agent_commit"]],
        cwd=source_workspace,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.splitlines()
    assert messages == original_messages


def test_peer_in_progress_direction_does_not_block_local_lease(tmp_path):
    workspace = tmp_path / "workspace"
    (workspace / "scratch").mkdir(parents=True)
    journals = tmp_path / "journals"
    peer = "direction_" + "1" * 32
    private_write(
        shared_research_path(journals),
        {
            "schema_version": 1,
            "revision": "r1",
            "through_epoch": 1,
            "directions": {
                peer: {
                    "direction_id": peer,
                    "name": "peer major redesign",
                    "change_scope": "major_redesign",
                    "hypothesis": "peer hypothesis",
                    "plan": ["continue"],
                    "stop_conditions": [],
                    "status": "in_progress",
                    "owner": "T2",
                    "supporting_experiment_ids": [],
                    "associated_experiment_ids": [],
                }
            },
            "experiments": [],
        },
    )
    journal = journals / "episodes" / "e00000002" / "journal.json"
    initialize_journal(journal, episode=2, base_commit="a" * 40, branch="episode")
    service = SupervisorJournalService(
        workspace=workspace,
        campaign_root=journals,
        evidence_root=journal.parent,
        research_root=tmp_path / "research",
        origin={"epoch": 2, "trajectory": 1, "episode": 2},
    )
    local = service.execute({
        "operation": "direction_update",
        "request": {
            "action": "propose", "name": "local probe", "change_scope": "local",
            "hypothesis": "local hypothesis",
            "applicability": "The local trajectory's target workload.",
            "expected_benefit": "Resolve the local performance uncertainty.",
            "plan": ["measure"],
        },
    })["direction_id"]
    assert service.execute({
        "operation": "direction_update",
        "request": {"action": "start", "direction_id": local, "analysis": "independent lease"},
    })["status"] == "recorded"


def test_epoch_projection_replays_local_active_direction_beside_peer_active_direction(
    tmp_path,
):
    journals = tmp_path / "journals"
    peer = "direction_" + "1" * 32
    local = "direction_" + "2" * 32
    private_write(
        shared_research_path(journals),
        {
            "schema_version": 1,
            "revision": "epoch-1",
            "through_epoch": 1,
            "directions": {
                peer: {
                    "direction_id": peer,
                    "name": "peer redesign",
                    "change_scope": "major_redesign",
                    "hypothesis": "peer hypothesis",
                    "plan": ["continue peer work"],
                    "stop_conditions": [],
                    "status": "in_progress",
                    "owner": "T2",
                    "supporting_experiment_ids": [],
                    "associated_experiment_ids": [],
                    "hypothesis_status": "unresolved",
                }
            },
            "experiments": [],
            "research_queue": [peer],
        },
    )

    previous_path = journals / "episodes" / "e00000001" / "journal.json"
    previous = initialize_journal(
        previous_path,
        episode=1,
        base_commit="a" * 40,
        branch="episode-1",
        tool_modules=("directions",),
    )
    previous.update(state="pivot", finalized_at="2026-11-01T00:00:00+00:00")
    previous["direction_events"] = [
        {
            "direction_event_id": "directionevent_" + "3" * 32,
            "direction_id": local,
            "action": "propose",
            "name": "local redesign",
            "change_scope": "major_redesign",
            "hypothesis": "local hypothesis",
            "plan": ["continue local work"],
            "stop_conditions": [],
            "recorded_at": "2026-11-01T00:00:00+00:00",
            "supporting_experiment_ids": [],
        },
        {
            "direction_event_id": "directionevent_" + "4" * 32,
            "direction_id": local,
            "action": "start",
            "analysis": "Keep the redesign active across Episodes.",
            "recorded_at": "2026-11-01T00:01:00+00:00",
            "supporting_experiment_ids": [],
            "hypothesis_status": None,
        },
    ]
    private_write(previous_path, previous)

    current_path = journals / "episodes" / "e00000002" / "journal.json"
    current = initialize_journal(
        current_path,
        episode=2,
        base_commit="b" * 40,
        branch="episode-2",
        tool_modules=("directions",),
    )
    current.update(state="pivot", finalized_at="2026-11-01T01:00:00+00:00")
    private_write(current_path, current)

    projection = research_projection(current_path, journals)

    assert projection["directions"][peer]["status"] == "in_progress"
    assert projection["directions"][peer]["owner"] == "T2"
    assert projection["directions"][local]["status"] == "in_progress"


def test_kernel_adoption_requires_exact_materialized_source(tmp_path):
    workspace = tmp_path / "workspace"
    source = _install(workspace, "def run():\n    return 1\n")
    journals = tmp_path / "journals"
    journal = journals / "episodes" / "e00000001" / "journal.json"
    initialize_journal(journal, episode=1, base_commit="a" * 40, branch="episode")
    agent_git = AgentGit(workspace, tmp_path / "agent.bundle")
    agent_git.prepare()
    identity = MeasurementStore(tmp_path / "measurements").kernel(
        source, agent_git.head()
    )
    service = SupervisorJournalService(
        workspace=workspace,
        campaign_root=journals,
        evidence_root=journal.parent,
        research_root=tmp_path / "research",
        origin={"trajectory": 1, "episode": 1},
    )
    request = {
        "operation": "kernel_adopt",
        "request": {
            "kernel_commit_id": identity["kernel_id"],
            "source_ref": "refs/atrex/shared/T2/e0001/head",
            "reason": "continue peer work",
        },
    }
    assert service.execute(request) == {
        "status": "recorded",
        "kernel_commit_id": identity["kernel_id"],
    }
    (workspace / "kernel.py").write_text("def run():\n    return 2\n", encoding="utf-8")
    with pytest.raises(AgentRequestError) as mismatch:
        service.execute(request)
    assert mismatch.value.response["error"]["code"] == "kernel_adoption_source_mismatch"


def test_pooled_search_keeps_each_lineage_instead_of_broadcasting_winner(tmp_path):
    seed = {"latency_us_geomean": 10.0, "name": "seed"}

    class Driver:
        def identity(self):
            return "binding"

        def bootstrap(self):
            return seed

        def initialize(self, initial, trajectories):
            assert initial == seed and trajectories == 2

        def prepare_epoch(self, epoch, seeds):
            assert seeds == {"1": seed, "2": seed}

        def run_episode(self, epoch, trajectory, episode):
            candidate = ({"latency_us_geomean": 5.0, "name": "t1"}
                         if trajectory == 1 else None)
            return {"epoch": epoch, "trajectory": trajectory, "episode": episode,
                    "retained_kernel": candidate, "tokens": 1,
                    "session_id": f"session-t{trajectory}"}

        def seal_epoch(self, epoch, jobs):
            pass

        def epoch_sealed(self, epoch):
            return True

        def publish_epoch(self, epoch, winner):
            assert winner["name"] == "t1"

        def tokens(self):
            return 2

    root = tmp_path / "search"
    result = PooledSearch(
        root,
        SearchOptions(epochs=1, trajectories=2, episodes_per_trajectory=1,
                      max_parallel_episodes=2),
        Driver(),
    ).run()
    assert result == "search: completed Epoch budget"
    state = read_state(root / "state.json")
    assert state["global_best"]["name"] == "t1"
    assert state["lineages"]["1"]["working_kernel"]["name"] == "t1"
    assert state["lineages"]["2"]["working_kernel"]["name"] == "seed"
    assert state["lineages"]["1"]["session_id"] == "session-t1"
    assert state["lineages"]["2"]["session_id"] == "session-t2"


def test_pooled_search_migrates_the_old_shared_seed_checkpoint(tmp_path):
    seed = {"latency_us_geomean": 10.0, "name": "legacy-seed"}
    options = SearchOptions(
        epochs=2,
        trajectories=2,
        episodes_per_trajectory=1,
        max_parallel_episodes=2,
        role_specialization=True,
    )

    class Driver:
        def identity(self):
            return "binding"

        def initialize(self, initial, trajectories):
            assert initial == seed and trajectories == 2

    root = tmp_path / "search"
    root.mkdir()
    (root / "state.json").write_text(json.dumps({
        "schema_version": 1,
        "options": {
            "epochs": 2,
            "trajectories": 2,
            "episodes_per_trajectory": 1,
            "max_parallel_episodes": 2,
        },
        "binding": "binding",
        "token_budget": 0,
        "bootstrap": seed,
        "seed": seed,
        "epoch": 2,
        "active": None,
        "completed_epochs": 1,
        "stop_reason": "stopped",
    }), encoding="utf-8")

    assert PooledSearch(root, options, Driver()).run() == "stopped"
    migrated = read_state(root / "state.json")
    assert migrated["schema_version"] == 2
    assert migrated["official_incumbent"] == seed
    assert migrated["lineages"]["1"]["working_kernel"] == seed
    assert migrated["lineages"]["2"]["working_kernel"] == seed
    assert migrated["options"]["synchronization"] == "synchronous"
    assert migrated["options"]["role_specialization"] is True
