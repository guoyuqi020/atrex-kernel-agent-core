from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from aka.contracts.agent import AgentRuntimeCapabilities, NativeInvocation, TokenUsage
from aka.legacy.task.problem.layout import public_shapes_payload, validate_public_shapes
from aka.light.agent.home import SESSION_HOME_ROOT_ENV, prepare_agent_environment
from aka.light.experiment.store import MeasurementStore
from aka.light.research import ResearchStore
from aka.light.runtime.paths import supervisor_campaign_root
from aka.light.runtime.request_errors import AgentRequestError
from aka.light.runtime.server import SupervisorRuntime
from aka.light.workflow.journal_projection import validate_terminal
from aka.light.workflow.journal_service import (
    SupervisorJournalService,
    initialize_journal,
    load_journal,
    private_write,
    shared_research_path,
)
from aka.light.workflow.models import SupervisorState
from aka.light.workflow.search import PooledSearch, SearchOptions, read_state
from aka.light.workflow.search_driver import EpisodeSearchDriver
from aka.light.workflow.session import LongSessionRunner
from aka.light.workspace.agent_git import AgentGit
from aka.light.workspace.shared_history import export_epoch, import_exports
from aka.light.workspace.sources import default_tree, install, pack


def _measurement(store: MeasurementStore, source: bytes, marker: str) -> tuple[str, str]:
    identity = store.kernel(source)
    request = {
        "operation": "evaluate",
        "options": {},
        "inputs": {"kernel.py": hashlib.sha256(source).hexdigest()},
        "marker": marker,
    }
    response = {"exit_code": 0, "stdout": "", "stderr": ""}
    with store.reserve(request, {"candidate": identity}) as task:
        record = task.finish(response, cacheable=True)
    return record["gateway_record_id"], identity["kernel_id"]


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
        finalized_at="2026-10-31T00:00:00+00:00",
    )
    private_write(private_path, private)
    public = dict(
        private,
        schema_version=1,
        runtime_journal_projection=True,
    )
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
    ) == "episode journal has no structured experiments"


def test_imported_measurement_is_readable_but_not_a_local_task_cache(tmp_path):
    source = MeasurementStore(tmp_path / "source")
    destination = MeasurementStore(tmp_path / "destination")
    record_id, kernel_id = _measurement(source, b"def kernel():\n    return 1\n", "source")

    imported = destination.import_record(source, record_id)

    assert imported == source.read(record_id)
    assert destination.read_kernel(kernel_id) == source.read_kernel(kernel_id)
    assert destination.kernel_records(kernel_id)[0]["gateway_record_id"] == record_id
    assert not (destination.root / "tasks").exists()


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

    loaded = service.execute({"operation": "direction_load", "direction_id": shared_direction})
    experiment = service.execute(
        {"operation": "experiment_load", "experiment_id": shared_experiment}
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
        "kernel-" + "3" * 32,
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
    assert rebuilt["lineages"]["T1"]["working_kernel_id"] == "kernel-" + "3" * 32
    board = store.board(trajectory=1)
    assert [item["direction_id"] for item in board["open_queue"]] == [first, second]
    assert board["open_queue"][1]["status"] == "in_progress(T1)"
    assert board["open_queue"][1]["next_step"] == "measure"


def test_public_shapes_are_canonical_first_five_and_tamper_evident(tmp_path):
    shapes = {
        str(index): {"init_kwargs": {"rank": index}, "input_kwargs": {"size": index * 2}}
        for index in (10, 2, 1, 8, 4, 3, 6)
    }
    private = tmp_path / "shapes.json"
    private.write_text(json.dumps(shapes), encoding="utf-8")
    payload = public_shapes_payload(private)
    assert list(payload["shapes"]) == ["1", "2", "3", "4", "6"]
    assert payload["selection"] == {
        "policy": "canonical_prefix", "count": 5, "total_shape_count": 7
    }
    public = tmp_path / "public_shapes.json"
    public.write_text(json.dumps(payload), encoding="utf-8")
    assert validate_public_shapes(public, private_shapes_path=private) == payload
    payload["shapes"]["1"]["input_kwargs"]["size"] = 999
    public.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="exact deterministic"):
        validate_public_shapes(public, private_shapes_path=private)


def test_agent_git_persists_work_and_installs_runtime_source_refs(tmp_path):
    workspace = tmp_path / "workspace"
    source = _install(workspace, "def run():\n    return 1\n")
    bundle = tmp_path / "state" / "agent.bundle"
    repo = AgentGit(workspace, bundle)
    repo.prepare()
    first = repo.head()
    assert first
    (workspace / "implementation" / "helper.py").write_text("VALUE = 2\n", encoding="utf-8")
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
    assert (restored / "implementation" / "helper.py").read_text() == "VALUE = 2\n"


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
    (workspace / "implementation" / "ksplit.py").write_text(
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
    assert (restored / "implementation" / "ksplit.py").read_text() == "KSPLIT = 4\n"


def test_epoch_git_exchange_filters_paths_and_preserves_peer_ref(tmp_path):
    source_workspace = tmp_path / "source"
    _install(source_workspace, "def run():\n    return 1\n")
    source = AgentGit(source_workspace, tmp_path / "source.bundle")
    source.prepare()
    (source_workspace / "implementation" / "peer.py").write_text("KSPLIT = 2\n", encoding="utf-8")
    (source_workspace / "scratch").mkdir()
    (source_workspace / "scratch" / "secret.txt").write_text("private\n", encoding="utf-8")
    source.persist()

    export = tmp_path / "peer.fast-export"
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
                "measurement_status": "measured", "kernel_id": "kernel-" + "4" * 32
            }
        },
        audit_path=tmp_path / "import.json",
    )
    assert imported["shared_refs"] == ["refs/atrex/shared/T2/e0001/head"]
    assert imported["commit_mappings"] == [
        {
            "export": "peer.fast-export",
            "source_ref": "refs/atrex/shared/T2/e0001/head",
            "original_agent_commit": receipt["original_agent_commit"],
            "imported_commit": imported["commit_mappings"][0]["imported_commit"],
        }
    ]
    assert imported["commit_mappings"][0]["imported_commit"]
    assert imported["commit_mappings"][0]["imported_commit"] != receipt["original_agent_commit"]

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
    assert tree == ["implementation/__init__.py", "implementation/peer.py", "kernel.py"]
    messages = subprocess.run(
        ["git", "log", "--format=%s", "refs/atrex/shared/T2/e0001/head"],
        cwd=inspect, text=True, capture_output=True, check=True,
    ).stdout.splitlines()
    assert messages
    assert set(messages) == {"Agent source checkpoint (message withheld by Runtime)"}


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
            "hypothesis": "local hypothesis", "plan": ["measure"],
        },
    })["direction_id"]
    assert service.execute({
        "operation": "direction_update",
        "request": {"action": "start", "direction_id": local, "analysis": "independent lease"},
    })["status"] == "recorded"


def test_kernel_adoption_requires_exact_materialized_source(tmp_path):
    workspace = tmp_path / "workspace"
    source = _install(workspace, "def run():\n    return 1\n")
    journals = tmp_path / "journals"
    journal = journals / "episodes" / "e00000001" / "journal.json"
    initialize_journal(journal, episode=1, base_commit="a" * 40, branch="episode")
    identity = MeasurementStore(tmp_path / "measurements").kernel(source)
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
            "kernel_id": identity["kernel_id"],
            "source_ref": "refs/atrex/shared/T2/e0001/head",
            "reason": "continue peer work",
        },
    }
    assert service.execute(request) == {"status": "recorded", "kernel_id": identity["kernel_id"]}
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
        epochs=2, trajectories=2, episodes_per_trajectory=1, max_parallel_episodes=2
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
