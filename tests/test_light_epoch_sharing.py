from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from aka.light.experiment.store import MeasurementStore
from aka.light.runtime.paths import supervisor_campaign_root
from aka.light.runtime.request_errors import AgentRequestError
from aka.light.workflow.journal_service import (
    SupervisorJournalService,
    initialize_journal,
    load_journal,
    private_write,
    shared_research_path,
)
from aka.light.workflow.search import PooledSearch, SearchOptions
from aka.light.workflow.search_driver import EpisodeSearchDriver


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
        assert {row["experiment_id"] for row in shared["experiments"]} == {identities[other][1]}
        assert "origin" not in shared["directions"][identities[other][0]]
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
