"""Reuse the existing serial Episode engine in isolated trajectory repositories.

Each trajectory keeps its own writable Journal, measurement task/retry scope and
adaptive state.  Completed Epochs publish immutable research, measurements and
their Kernel source closure alongside the accepted Kernel.  These are private
controller repositories, not Agent-visible Git workspaces.
"""
from __future__ import annotations

import copy
from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
import tempfile
from threading import Event

import aka.light.workflow.context as main_adapter
from aka.light.workflow.episode import LongHorizonCampaign
from aka.light.workspace.git import git_blob, git_head
from aka.legacy.task.persistence import durable_write_json, durable_replace
from aka.light.workspace.versions import version_record_path
from .search import latency, read_state
from .search_git import commit_files, git
from aka.light.workspace.sources import from_git, unpack, snapshot as source_snapshot
from aka.light.experiment.store import MeasurementStore, digest as evidence_digest, private_write
from aka.light.runtime.paths import supervisor_campaign_root
from aka.light.workflow.journal_service import (
    load_journal,
    research_projection,
    shared_research_path,
)
from aka.light.workspace.shared_history import export_epoch, import_exports
from aka.light.research import ResearchStore


class EpisodeSearchDriver:
    def __init__(self, campaign, root: Path):
        self.campaign, self.root = campaign, root
        self.children = {}
        self.cancel_event = Event()

    def identity(self):
        # Store only a digest of settings (endpoint addresses remain private).
        values = {"request": asdict(self.campaign.request),
                  "workflow": asdict(self.campaign.module_options),
                  "agent": asdict(self.campaign.agent.options),
                  "inherit_state": self.campaign.candidate_workspace.inherit_state,
                  "research_records": self.campaign.research_records,
                  "research_modules": list(self.campaign.research_modules),
                  "wiki": self.campaign.tool_runtime.wiki,
                  "pause_agent_during_requests": (
                      self.campaign.tool_runtime.pause_agent_during_requests
                  ),
                  "repetitions": self.campaign.tool_runtime.measurement_repetitions,
                  "evaluation": {name: getattr(self.campaign, name) for name in (
                      "sandbox_hardware", "sandbox_profile", "sandbox_url", "sandbox_ssh",
                      "sandbox_timeout", "verify_repeats", "evaluation_step_timeout", "min_improvement_pct")}}
        return hashlib.sha256(json.dumps(values, sort_keys=True, default=str).encode()).hexdigest()

    def bootstrap(self):
        main_adapter.prepare_campaign(self.campaign)
        # Do not reinterpret a previously serial campaign as pooled search.
        state_file = self.campaign.workspace / ".atrex_long_horizon/state.json"
        if state_file.exists() and read_state(state_file).get("episodes", 0):
            raise ValueError("Start pooled search in a new Campaign; serial Episode checkpoints are not migrated")
        active = self.campaign.workspace / ".atrex_long_horizon/active_episode.json"
        if active.exists():
            raise ValueError("Cannot enable pooled search with an active serial Episode")
        version = main_adapter.latest_version(self.campaign.workspace)
        record = main_adapter.read_memory(self.campaign.workspace, version)
        if not isinstance(record, dict) or record.get("quality_gate", {}).get("result") != "PASS":
            raise RuntimeError("Pooled search requires a verified Bootstrap/Framework baseline")
        return self._kernel(self.campaign.workspace, git_head(self.campaign.workspace), record, 0, 0)

    def _kernel(self, workspace, commit, record, trajectory, episode):
        source = from_git(workspace, commit)
        value = {"workspace": str(workspace), "commit": commit,
                 "sha256": hashlib.sha256(source).hexdigest(), "record": record,
                 "trajectory": trajectory, "episode": episode,
                 "latency_us_geomean": record["performance"]["latency_us_geomean"]}
        latency(value)
        # Keep selected historical candidates reachable after Episode worktree cleanup.
        git(workspace, "update-ref", f"refs/atrex-search/kernels/{value['sha256']}/{commit}", commit)
        return value

    def source(self, kernel):
        workspace = Path(kernel["workspace"])
        allowed = {self.campaign.workspace.resolve(), *(child.workspace.resolve() for child in self.children.values())}
        if workspace.resolve() not in allowed:
            raise ValueError("Search Kernel belongs to an unknown controller workspace")
        source = from_git(workspace, kernel["commit"])
        if hashlib.sha256(source).hexdigest() != kernel["sha256"]:
            raise RuntimeError("Search Kernel bytes differ from the sealed checkpoint")
        return source

    def _research(self, child, episode):
        root = supervisor_campaign_root(child.workspace)
        journal = root / "journals" / "episodes" / f"e{episode:08d}" / "journal.json"
        return research_projection(journal, root / "journals")

    @staticmethod
    def _local_research_origins(child, terminal_episode):
        root = supervisor_campaign_root(child.workspace) / "journals" / "episodes"
        result = {"directions": {}, "experiments": {}}
        for path in sorted(root.glob("e*/journal.json")):
            try:
                episode = int(path.parent.name[1:])
            except ValueError:
                continue
            if episode > terminal_episode:
                continue
            journal = load_journal(path)
            for event in journal["direction_events"]:
                if isinstance(event, dict) and event.get("action") == "propose":
                    result["directions"].setdefault(event.get("direction_id"), episode)
            for experiment in journal["experiments"]:
                if isinstance(experiment, dict):
                    result["experiments"].setdefault(experiment.get("experiment_id"), episode)
        return result

    @staticmethod
    def _merge_named(values, prior_origins, local_episodes, jobs, label):
        merged, origins = {}, copy.deepcopy(prior_origins)
        all_ids = sorted({identifier for _, rows in values.items() for identifier in rows})
        episodes = {job["trajectory"]: job["episode"] for job in jobs}
        for identifier in all_ids:
            candidates = {number: rows[identifier] for number, rows in values.items() if identifier in rows}
            origin = origins.get(identifier, {})
            owner = origin.get("trajectory")
            if owner in candidates:
                selected = candidates[owner]
            else:
                serialized = {json.dumps(item, sort_keys=True, allow_nan=False) for item in candidates.values()}
                if len(serialized) != 1:
                    raise RuntimeError(f"Conflicting shared {label} identity: {identifier}")
                owner = min(candidates)
                selected = candidates[owner]
                origins[identifier] = {
                    "epoch": jobs[0]["epoch"],
                    "trajectory": owner,
                    "episode": local_episodes.get(owner, {}).get(identifier, episodes[owner]),
                }
            merged[identifier] = copy.deepcopy(selected)
        return merged, origins

    def epoch_sealed(self, epoch):
        return (self.root / "epochs" / f"e{epoch:08d}-shared.json").is_file()

    def seal_epoch(self, epoch, jobs):
        """Publish cumulative, read-only research and measurement evidence."""
        if not jobs or any(job.get("status") != "completed" for job in jobs):
            raise RuntimeError("Cannot seal shared evidence before every Epoch Episode is terminal")
        terminal = {}
        for job in jobs:
            terminal[job["trajectory"]] = max(
                terminal.get(job["trajectory"], 0), job["episode"]
            )
        job_rows = [
            {"epoch": epoch, "trajectory": trajectory, "episode": episode}
            for trajectory, episode in sorted(terminal.items())
        ]
        projections = {
            number: self._research(self.children[number], terminal[number])
            for number in sorted(terminal)
        }
        local_origins = {
            number: self._local_research_origins(self.children[number], terminal[number])
            for number in sorted(terminal)
        }
        manifest_path = self.root / "shared" / "manifest.json"
        try:
            previous = read_state(manifest_path)
        except FileNotFoundError:
            previous = {"directions": {}, "experiments": {}, "records": {}}
        direction_rows = {
            number: projection["directions"] for number, projection in projections.items()
        }
        experiment_rows = {
            number: {
                item["experiment_id"]: item for item in projection["experiments"]
            }
            for number, projection in projections.items()
        }
        directions, direction_origins = self._merge_named(
            direction_rows,
            previous.get("directions", {}),
            {number: value["directions"] for number, value in local_origins.items()},
            job_rows,
            "Direction",
        )
        experiments, experiment_origins = self._merge_named(
            experiment_rows,
            previous.get("experiments", {}),
            {number: value["experiments"] for number, value in local_origins.items()},
            job_rows,
            "Experiment",
        )

        research_states = {
            number: ResearchStore(
                supervisor_campaign_root(self.children[number].workspace) / "research"
            ).read()
            for number in sorted(self.children)
        }
        research_events = {
            number: ResearchStore(
                supervisor_campaign_root(self.children[number].workspace) / "research"
            ).events()
            for number in sorted(self.children)
        }
        # A progress step is monotonic. Merge the union so a peer's completed
        # milestone cannot disappear when the Direction crosses an Epoch.
        for direction_id, direction in directions.items():
            completed = set()
            for state in research_states.values():
                item = state.get("directions", {}).get(direction_id, {})
                completed.update(item.get("completed_plan_steps", []))
            if completed:
                direction["completed_plan_steps"] = sorted(completed)

        latest_reorder = None
        for number, events in research_events.items():
            for event in events:
                if event.get("type") != "direction.reordered":
                    continue
                candidate = (str(event.get("recorded_at", "")), number)
                if latest_reorder is None or candidate > latest_reorder:
                    latest_reorder = candidate
        order_sources = sorted(research_states)
        if latest_reorder is not None:
            preferred = latest_reorder[1]
            order_sources = [preferred, *(item for item in order_sources if item != preferred)]
        research_queue = []
        for number in order_sources:
            for direction_id in research_states[number].get("queue", []):
                if direction_id in directions and direction_id not in research_queue:
                    research_queue.append(direction_id)
        for direction_id in sorted(directions):
            if (
                directions[direction_id].get("status") in {"proposed", "in_progress", "deferred", "blocked"}
                and direction_id not in research_queue
            ):
                research_queue.append(direction_id)

        central = MeasurementStore(self.root / "shared" / "measurements")
        record_origins = copy.deepcopy(previous.get("records", {}))
        new_record_ids = []
        for number, child in sorted(self.children.items()):
            source = MeasurementStore(supervisor_campaign_root(child.workspace) / "measurements")
            for record_id in source.record_ids():
                if record_id in record_origins:
                    continue
                central.import_record(source, record_id)
                record_origins[record_id] = {"epoch": epoch, "trajectory": number}
                new_record_ids.append(record_id)

        for experiment_id, experiment in experiments.items():
            record_ids = experiment.get("gateway_record_ids")
            if not isinstance(record_ids, list):
                raise RuntimeError(
                    f"Shared Experiment {experiment_id} has invalid Gateway Record references"
                )
            for record_id in record_ids:
                try:
                    central.read(record_id)
                except (OSError, ValueError, KeyError, TypeError) as error:
                    raise RuntimeError(
                        f"Shared Experiment {experiment_id} refers to unavailable evidence {record_id}"
                    ) from error

        research_value = {
            "directions": directions,
            "experiments": [experiments[key] for key in sorted(experiments)],
            "research_queue": research_queue,
        }
        revision = evidence_digest(research_value)
        for number, child in sorted(self.children.items()):
            destination_root = supervisor_campaign_root(child.workspace)
            destination = MeasurementStore(destination_root / "measurements")
            for record_id in new_record_ids:
                destination.import_record(central, record_id)
            visible_directions = {
                key: dict(
                    value,
                    **(
                        {"owner": f"T{direction_origins[key]['trajectory']}"}
                        if value.get("status") == "in_progress" else {}
                    ),
                )
                for key, value in directions.items()
                if direction_origins[key]["trajectory"] != number
            }
            visible_experiments = [
                experiments[key] for key in sorted(experiments)
                if experiment_origins[key]["trajectory"] != number
            ]
            shared = {
                "schema_version": 1,
                "revision": revision,
                "through_epoch": epoch,
                "directions": visible_directions,
                "experiments": visible_experiments,
                "research_queue": research_queue,
            }
            private_write(shared_research_path(destination_root / "journals"), shared)

        manifest = {
            "schema_version": 1,
            "through_epoch": epoch,
            "revision": revision,
            "directions": direction_origins,
            "experiments": experiment_origins,
            "records": record_origins,
        }
        private_write(manifest_path, manifest)
        receipt = {
            "schema_version": 1,
            "epoch": epoch,
            "revision": revision,
            "direction_ids": sorted(directions),
            "experiment_ids": sorted(experiments),
            "gateway_record_ids": central.record_ids(),
        }
        receipt_path = self.root / "epochs" / f"e{epoch:08d}-shared.json"
        try:
            existing = read_state(receipt_path)
        except FileNotFoundError:
            private_write(receipt_path, receipt)
        else:
            if existing != receipt:
                raise RuntimeError("Epoch shared-evidence receipt changed during recovery")

        for number, episode in sorted(terminal.items()):
            agent_workspace = (
                supervisor_campaign_root(self.children[number].workspace)
                / "journals" / "episodes" / f"e{episode:08d}" / "agent" / "workspace"
            )
            if not (agent_workspace / ".git").is_dir():
                # Pre-v2/migrated Episodes have no Agent-private repository.
                # Their immutable Kernel/Measurement closure remains shared;
                # start filtered history with the next v2 Episode.
                private_write(
                    self.root / "shared" / "git" / f"T{number}" / f"e{epoch:08d}.json",
                    {
                        "schema_version": 1,
                        "trajectory": number,
                        "epoch": epoch,
                        "status": "unavailable",
                        "reason": "legacy_episode_without_agent_git",
                    },
                )
                continue
            export_epoch(
                agent_workspace,
                self.root / "shared" / "git" / f"T{number}" / f"e{epoch:08d}.fast-export",
                trajectory=number,
                epoch=epoch,
                audit_path=self.root / "shared" / "git" / f"T{number}" / f"e{epoch:08d}.json",
            )

    def initialize(self, bootstrap, trajectories):
        for number in range(1, trajectories + 1):
            workspace = self.root / "trajectories" / f"t{number:04d}"
            marker = workspace / ".git/atrex-search-origin.json"
            expected = {"bootstrap": bootstrap["commit"], "trajectory": number,
                        "campaign": str(self.campaign.workspace)}
            if not workspace.exists():
                workspace.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                with tempfile.TemporaryDirectory(prefix=".preparing-", dir=workspace.parent) as temporary:
                    staged = Path(temporary) / "repository"
                    git(workspace.parent, "clone", "--quiet", "--no-hardlinks", "--no-checkout",
                        str(self.campaign.workspace), str(staged))
                    git(staged, "checkout", "--quiet", "--detach", bootstrap["commit"])
                    git(staged, "remote", "remove", "origin")
                    git(staged, "config", "user.name", "GPU Kernel Optimizer")
                    git(staged, "config", "user.email", "gpu-kernel-optimizer@local")
                    durable_write_json(staged / ".git/atrex-search-origin.json", expected)
                    durable_replace(staged, workspace)
            if read_state(marker) != expected:
                raise RuntimeError("Trajectory workspace does not match its frozen Bootstrap identity")
            child = copy.copy(self.campaign)
            child.request = replace(self.campaign.request, workspace=workspace)
            child._supervisor_runtime = None
            child._production_review_cache = {}
            child.workspace_suffix = f"trajectory-{number:04d}"
            child.target_util = 0.0  # Search budget, not a local utilization stop, owns admission.
            child.tokens_spent = 0
            child.search_coordinate = None
            child.search_cancel = self.cancel_event
            self.children[number] = child

    def _publish(self, workspace, epoch, kernel, label):
        source = self.source(kernel)
        if workspace.resolve() != Path(kernel["workspace"]).resolve():
            # Keep the provenance commit named in version facts locally readable,
            # without merging the other trajectory's tree or controller history.
            git(workspace, "fetch", "--quiet", "--no-tags", "--no-write-fetch-head",
                kernel["workspace"], kernel["commit"])
        git(workspace, "update-ref",
            f"refs/atrex-search/kernels/{kernel['sha256']}/{kernel['commit']}", kernel["commit"])
        receipt_path = self.root / "publications" / f"e{epoch:08d}-{label}.json"
        # Pin version and full publication content before the Git transaction so
        # a committed-but-unacknowledged publication does not allocate a new vN.
        try:
            plan = read_state(receipt_path)
        except FileNotFoundError:
            version = main_adapter.latest_version(workspace) + 1
            record = copy.deepcopy(kernel["record"])
            record.update(version=f"v{version}", git_commit_hash=kernel["commit"],
                          long_horizon={"status": "search_broadcast", "search_epoch": epoch,
                                        "trajectory": kernel["trajectory"], "episode": kernel["episode"]})
            _, next_files = unpack(source)
            _, previous_files = unpack(source_snapshot(workspace))
            plan = {"version": version, "sha256": kernel["sha256"], "record": record,
                    "removed_sources": sorted(previous_files.keys() - next_files.keys())}
            durable_write_json(receipt_path, plan)
        if plan["sha256"] != kernel["sha256"]:
            raise RuntimeError("Epoch broadcast winner changed during recovery")
        private_name = f".atrex_versions/v{plan['version']}.json"
        record = (json.dumps(plan["record"], sort_keys=True) + "\n").encode()
        _, files = unpack(source)
        files.update({name: None for name in plan.get("removed_sources", [])})
        files[private_name] = record
        if self.campaign.generate_memory:
            files[f"memory/v{plan['version']}.json"] = record
        return commit_files(workspace, receipt_path.with_suffix(".git.json"), files,
                            f"search epoch {epoch}: {label} Kernel {kernel['sha256'][:16]}")

    def prepare_epoch(self, epoch, kernels):
        search_state = read_state(self.root / "state.json")
        official = search_state.get("global_best")
        if not isinstance(official, dict):
            raise RuntimeError("Search checkpoint has no global best Kernel")
        for number, child in self.children.items():
            store = self._state(child)
            expected = (epoch - 1) * self.campaign.module_options.search.episodes_per_trajectory
            if store["episodes"] != expected or (child.workspace / ".atrex_long_horizon/active_episode.json").exists():
                raise RuntimeError("Cannot broadcast while a trajectory has an unfinished or unexpected Episode")
            expected_kernel = kernels.get(str(number))
            if not isinstance(expected_kernel, dict):
                raise RuntimeError("Search checkpoint has no working Kernel for a trajectory")
            # Persistent island semantics: each trajectory keeps its own HEAD,
            # WIP and lineage best. Epoch preparation validates identity but
            # never broadcasts another trajectory's winner into this checkout.
            current_source = source_snapshot(child.workspace)
            if hashlib.sha256(current_source).hexdigest() != expected_kernel["sha256"]:
                self._publish(
                    child.workspace,
                    epoch,
                    expected_kernel,
                    f"trajectory-{number:04d}-lineage-restore",
                )
            child.start_runtime()
            kernel_identity = child._supervisor_runtime.measurements.kernel(
                self.source(expected_kernel)
            )
            official_identity = child._supervisor_runtime.measurements.kernel(
                self.source(official)
            )
            child._supervisor_runtime.research.checkpoint_lineage(
                {
                    "working_kernel_id": kernel_identity["kernel_id"],
                    "lineage_best_kernel_id": kernel_identity["kernel_id"],
                    "last_correct_kernel_id": kernel_identity["kernel_id"],
                },
                origin={"epoch": epoch, "trajectory": number},
            )
            child._supervisor_runtime.research.append(
                "promotion.accepted",
                {
                    "kernel_id": official_identity["kernel_id"],
                    "latency_us_geomean": official["latency_us_geomean"],
                },
                origin={"epoch": epoch, "trajectory": number},
                dedupe_key=f"epoch-incumbent:{epoch}:{official['sha256']}",
            )
            if epoch > 1:
                exports = []
                exported_peers = []
                for peer in sorted(self.children):
                    if peer == number:
                        continue
                    root = self.root / "shared" / "git" / f"T{peer}"
                    export = root / f"e{epoch - 1:08d}.fast-export"
                    if export.is_file():
                        exports.append(export)
                        exported_peers.append(peer)
                        continue
                    receipt_path = root / f"e{epoch - 1:08d}.json"
                    try:
                        receipt = read_state(receipt_path)
                    except FileNotFoundError as error:
                        raise RuntimeError(
                            f"Epoch peer Git history has neither export nor audit receipt: {export}"
                        ) from error
                    if receipt.get("status") != "unavailable":
                        raise RuntimeError(f"Epoch peer Git export is missing: {export}")
                notes = {}
                for peer, kernel in kernels.items():
                    if int(peer) not in exported_peers:
                        continue
                    ref = f"refs/atrex/shared/T{peer}/e{epoch - 1:04d}/head"
                    notes[ref] = {
                        "measurement_status": "measured",
                        "kernel_sha256": kernel["sha256"],
                        "correctness": "pass",
                        "latency_us_geomean": kernel["latency_us_geomean"],
                    }
                runtime_root = supervisor_campaign_root(child.workspace)
                audit_path = (
                    runtime_root / "adaptive-state" / "git-imports" / f"e{epoch:08d}.json"
                )
                if exports:
                    import_exports(
                        runtime_root / "adaptive-state" / "agent-git.bundle",
                        exports,
                        notes=notes,
                        audit_path=audit_path,
                    )
                else:
                    private_write(audit_path, {
                        "schema_version": 1,
                        "imports": [],
                        "status": "unavailable",
                        "reason": "all_peer_episodes_predate_agent_git",
                    })

    def _state(self, child):
        path = child.workspace / ".atrex_long_horizon/state.json"
        try:
            state = read_state(path)
        except FileNotFoundError:
            # The very first Episode writes its active marker/brief before its
            # first usage checkpoint. That interrupted preparation is resumable;
            # lost accounting for terminal/later Episodes is not a fresh start.
            control = child.workspace / ".atrex_long_horizon"
            archives = control / "episodes"
            if archives.exists() and any(archives.iterdir()):
                active = read_state(control / "active_episode.json")
                if (active.get("episode") != 1 or any(archives.glob("*/attempt.json"))
                        or any(path.name != "e0001" for path in archives.iterdir())):
                    raise RuntimeError("Trajectory state is missing; restore it before resuming")
            return {"episodes": 0, "tokens": 0}
        for name in ("episodes", "tokens"):
            if type(state.get(name)) is not int or state[name] < 0:
                raise ValueError("Invalid trajectory Episode/usage checkpoint")
        return state

    def run_episode(self, epoch, trajectory, episode):
        child = self.children[trajectory]
        current = self._state(child)["episodes"]
        if current not in {episode - 1, episode}:
            raise RuntimeError("Trajectory Episode sequence differs from the Epoch schedule")
        child.search_coordinate = {"epoch": epoch, "trajectory": trajectory, "episode": episode}
        # An archived terminal Episode is not run again after coordinator restart.
        if current < episode or (child.workspace / ".atrex_long_horizon/active_episode.json").exists():
            child.start_runtime()
            from aka.light.workflow.session import LongSessionRunner
            runner = LongSessionRunner(agent=child.agent, agent_cli=child.agent_cli,
                usage_policy=lambda turn: turn.terminal_usage.total_tokens or 0)
            runner.cancel_event = self.cancel_event
            verifier = child.acceptance_verifier(
                execute=child.measure_for_acceptance, hardware=child.sandbox_hardware,
                profile=child.sandbox_profile, url=child.sandbox_url, ssh=child.sandbox_ssh,
                ssh_init=child.sandbox_ssh_init, health_command=child.sandbox_health_command,
                timeout=child.sandbox_timeout, repeats=child.verify_repeats,
                per_run_timeout=child.evaluation_step_timeout, min_improvement_pct=child.min_improvement_pct,
                private_reference_dir=child.private_reference_dir)
            LongHorizonCampaign(base_campaign=child, max_episodes=episode,
                                handoff_resumes=child.handoff_resumes, verifier=verifier,
                                session_runner=runner).run()
        if self._state(child)["episodes"] != episode:
            raise RuntimeError("Episode did not reach a durable terminal outcome")
        attempt = read_state(child.workspace / f".atrex_long_horizon/episodes/e{episode:04d}/attempt.json")
        if attempt.get("episode") != episode:
            raise RuntimeError("Episode archive identity mismatch")
        retained = None
        if attempt.get("accepted") is True:
            if attempt.get("verification", {}).get("gate") != "PASS":
                raise RuntimeError("Accepted Episode lacks a passing authoritative comparison")
            commit = attempt["promotion_commit"]
            record_path = version_record_path(child.workspace, attempt["version"])
            record = json.loads(git_blob(child.workspace, commit, record_path.relative_to(child.workspace).as_posix()))
            if record.get("quality_gate", {}).get("result") != "PASS":
                raise RuntimeError("Accepted Episode lacks a passing sealed version record")
            if (attempt.get("candidate_commit") and from_git(child.workspace, commit)
                    != from_git(child.workspace, attempt["candidate_commit"])):
                raise RuntimeError("Promoted search Kernel differs from the accepted Episode candidate")
            retained = self._kernel(child.workspace, commit, record, trajectory, episode)
        return {"epoch": epoch, "trajectory": trajectory, "episode": episode,
                "status": attempt["status"], "retained_kernel": retained,
                "archive": str(child.workspace / f".atrex_long_horizon/episodes/e{episode:04d}"),
                "tokens": attempt.get("tokens", 0)}

    def publish_epoch(self, epoch, kernel):
        self._publish(self.campaign.workspace, epoch, kernel, "campaign")

    def tokens(self):
        return sum(self._state(child)["tokens"] for child in self.children.values())

    def stop_reason(self, state):
        target = self.campaign.target_util
        if target > 0 and main_adapter.peak_util(state["global_best"]["record"]) >= target:
            return "success: target utilization reached at Epoch boundary"
        limit = self.campaign.max_stall
        if (limit and not self.campaign.module_options.goal_episodes
                and state["consecutive_without_improvement"] >= limit):
            return "stall: Epochs without a better retained Kernel"
        return ""

    def interrupt(self):
        # Stop further HTTP work. Running Sessions retain their normal bounded
        # process lifecycle and unfinished Episodes retain their recovery points.
        self.cancel_event.set()
        for child in self.children.values():
            child.close_runtime()

    def close(self):
        self.interrupt()
