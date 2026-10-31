"""Append-only research state shared by Light Episodes.

Measurement Records and Kernel Artifacts remain authoritative for execution
facts.  This store owns only research intent, queue order, trajectory leases,
and the compact projection used to start an Episode.
"""

from __future__ import annotations

import fcntl
import json
import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aka.legacy.task.persistence import durable_write_json
from aka.light.agent.home import open_private_directory
from aka.light.agent.session_tail import read_regular_bytes

SCHEMA_VERSION = 1
MAX_EVENTS_BYTES = 64 * 1024 * 1024
MAX_BOARD_DIRECTIONS = 12
MAX_BOARD_EXPERIMENTS = 8
OPEN_STATUSES = {"proposed", "in_progress", "deferred", "blocked"}
CLOSED_STATUSES = {"completed", "abandoned"}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _origin(value: Mapping[str, object] | None) -> dict[str, int]:
    value = value or {}
    result: dict[str, int] = {}
    for name in ("epoch", "trajectory", "episode"):
        item = value.get(name)
        if type(item) is int and item > 0:
            result[name] = item
    result.setdefault("trajectory", 1)
    return result


def _empty() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "sequence": 0,
        "directions": {},
        "experiments": {},
        "queue": [],
        "lineages": {},
        "promotions": [],
    }


def _lineage_key(origin: Mapping[str, object] | None) -> str:
    return f"T{_origin(origin)['trajectory']}"


def _derive_next_step(direction: Mapping[str, object]) -> str | None:
    plan = direction.get("plan")
    completed = set(direction.get("completed_plan_steps") or [])
    if not isinstance(plan, list):
        return None
    for index, step in enumerate(plan):
        if index not in completed and isinstance(step, str) and step.strip():
            return step
    return None


def _apply(state: dict[str, Any], event: Mapping[str, object]) -> None:
    kind = event["type"]
    payload = event.get("payload")
    payload = dict(payload) if isinstance(payload, Mapping) else {}
    origin = event.get("origin")
    origin = dict(origin) if isinstance(origin, Mapping) else {}
    if kind == "direction.observed":
        direction = payload.get("direction")
        if not isinstance(direction, Mapping):
            raise ValueError("direction.observed requires a direction object")
        item = deepcopy(dict(direction))
        direction_id = item.get("direction_id")
        if not isinstance(direction_id, str) or not direction_id:
            raise ValueError("observed Direction has no ID")
        previous = state["directions"].get(direction_id, {})
        completed = previous.get("completed_plan_steps", [])
        item.setdefault("completed_plan_steps", completed)
        previous_owner = previous.get("owner")
        if item.get("status") == "in_progress" and not item.get("owner") and previous_owner:
            item["owner"] = previous_owner
        item["origin"] = _origin(item.get("origin") if isinstance(item.get("origin"), Mapping) else origin)
        item["next_step"] = _derive_next_step(item)
        state["directions"][direction_id] = item
        if item.get("status") in OPEN_STATUSES and direction_id not in state["queue"]:
            state["queue"].append(direction_id)
        if item.get("status") in CLOSED_STATUSES and direction_id in state["queue"]:
            state["queue"].remove(direction_id)
        owner = item.get("owner") or previous_owner
        if isinstance(owner, str) and owner:
            state["lineages"].setdefault(owner, {})["active_direction_id"] = (
                direction_id if item.get("status") == "in_progress" else None
            )
    elif kind == "direction.progressed":
        direction_id = payload.get("direction_id")
        direction = state["directions"].get(direction_id)
        if direction is None:
            raise ValueError("Direction progress references an unknown Direction")
        index = payload.get("plan_step")
        if type(index) is not int or index < 0 or index >= len(direction.get("plan", [])):
            raise ValueError("Direction progress has an invalid plan_step")
        completed = set(direction.get("completed_plan_steps", []))
        completed.add(index)
        direction["completed_plan_steps"] = sorted(completed)
        direction["next_step"] = _derive_next_step(direction)
    elif kind == "direction.reordered":
        direction_id = payload.get("direction_id")
        if direction_id not in state["queue"]:
            raise ValueError("Only an open queued Direction can be reordered")
        before = payload.get("before_direction_id")
        after = payload.get("after_direction_id")
        if before is not None and after is not None:
            raise ValueError("Direction reorder accepts before or after, not both")
        queue = [item for item in state["queue"] if item != direction_id]
        anchor = before if before is not None else after
        if anchor is None:
            queue.append(direction_id)
        else:
            if anchor not in queue:
                raise ValueError("Direction reorder anchor is outside the open queue")
            offset = queue.index(anchor) + (1 if after is not None else 0)
            queue.insert(offset, direction_id)
        state["queue"] = queue
    elif kind == "queue.synchronized":
        requested = payload.get("direction_ids")
        if not isinstance(requested, list) or len(set(requested)) != len(requested):
            raise ValueError("Shared Direction queue must contain unique IDs")
        unknown = [item for item in requested if item not in state["directions"]]
        if unknown:
            raise ValueError("Shared Direction queue references an unknown Direction")
        open_ids = [
            item for item in requested
            if state["directions"][item].get("status") in OPEN_STATUSES
        ]
        for item in state["queue"]:
            if item not in open_ids and state["directions"].get(item, {}).get("status") in OPEN_STATUSES:
                open_ids.append(item)
        state["queue"] = open_ids
    elif kind == "experiment.observed":
        experiment = payload.get("experiment")
        if not isinstance(experiment, Mapping):
            raise ValueError("experiment.observed requires an experiment object")
        item = deepcopy(dict(experiment))
        experiment_id = item.get("experiment_id")
        if not isinstance(experiment_id, str) or not experiment_id:
            raise ValueError("observed Experiment has no ID")
        item["origin"] = _origin(item.get("origin") if isinstance(item.get("origin"), Mapping) else origin)
        previous = state["experiments"].get(experiment_id)
        if previous is not None:
            previous_value = {key: value for key, value in previous.items() if key != "origin"}
            item_value = {key: value for key, value in item.items() if key != "origin"}
            if previous_value != item_value:
                raise ValueError("Experiment identity was reused for different content")
            item = previous
        state["experiments"][experiment_id] = item
    elif kind == "lineage.checkpointed":
        lineage = _lineage_key(origin)
        current = state["lineages"].setdefault(lineage, {})
        for key in (
            "working_kernel_id", "lineage_best_kernel_id", "wip_checkpoint",
            "last_correct_kernel_id", "active_direction_id", "session_id",
        ):
            if key in payload:
                current[key] = deepcopy(payload[key])
    elif kind == "kernel.adopted":
        lineage = _lineage_key(origin)
        current = state["lineages"].setdefault(lineage, {})
        current["working_kernel_id"] = payload.get("kernel_id")
        current["adopted_from"] = deepcopy(payload.get("source"))
    elif kind in {"promotion.accepted", "promotion.rejected"}:
        state["promotions"].append({
            "type": kind,
            "payload": deepcopy(payload),
            "origin": _origin(origin),
            "sequence": event["sequence"],
        })
        state["promotions"] = state["promotions"][-64:]
    else:
        raise ValueError(f"unsupported research event type: {kind}")
    state["sequence"] = event["sequence"]


class ResearchStore:
    """Crash-recoverable event log with a rebuildable compact projection."""

    def __init__(self, root: Path | str):
        self.root = Path(root)
        self.events_path = self.root / "events.jsonl"
        self.snapshot_path = self.root / "snapshot.json"
        self.lock_path = self.root / "lock"
        with open_private_directory(self.root):
            pass

    @contextmanager
    def _locked(self) -> Iterator[None]:
        with open_private_directory(self.root) as directory:
            fd = os.open("lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=directory)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                yield
            finally:
                os.close(fd)

    def _events(self) -> list[dict[str, Any]]:
        try:
            raw = read_regular_bytes(self.events_path, limit=MAX_EVENTS_BYTES + 1)
        except FileNotFoundError:
            return []
        if len(raw) > MAX_EVENTS_BYTES:
            raise ValueError("Research event log exceeds 64 MiB")
        rows = []
        for number, line in enumerate(raw.splitlines(), 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"Research event log line {number} is invalid") from error
            if not isinstance(value, dict) or value.get("sequence") != len(rows) + 1:
                raise ValueError("Research event sequence is not contiguous")
            rows.append(value)
        return rows

    def rebuild(self) -> dict[str, Any]:
        state = _empty()
        for event in self._events():
            _apply(state, event)
        return state

    def read(self) -> dict[str, Any]:
        with self._locked():
            state = self.rebuild()
            durable_write_json(self.snapshot_path, state)
            return deepcopy(state)

    def events(self) -> list[dict[str, Any]]:
        with self._locked():
            return deepcopy(self._events())

    def append(
        self,
        event_type: str,
        payload: Mapping[str, object],
        *,
        origin: Mapping[str, object] | None = None,
        dedupe_key: str | None = None,
        skip_if_projection_unchanged: str | None = None,
    ) -> dict[str, Any]:
        with self._locked():
            events = self._events()
            if dedupe_key is not None:
                for event in reversed(events):
                    if event.get("dedupe_key") == dedupe_key:
                        state = _empty()
                        for row in events:
                            _apply(state, row)
                        return state
            event = {
                "sequence": len(events) + 1,
                "type": event_type,
                "recorded_at": _now(),
                "origin": _origin(origin),
                "payload": deepcopy(dict(payload)),
            }
            if dedupe_key is not None:
                event["dedupe_key"] = dedupe_key
            state = _empty()
            for row in events:
                _apply(state, row)
            projected = deepcopy(state)
            _apply(projected, event)
            if (
                skip_if_projection_unchanged is not None
                and projected.get(skip_if_projection_unchanged)
                == state.get(skip_if_projection_unchanged)
            ):
                return deepcopy(state)
            state = projected
            encoded = (json.dumps(event, ensure_ascii=False, allow_nan=False, sort_keys=True) + "\n").encode()
            if self.events_path.exists() and self.events_path.stat().st_size + len(encoded) > MAX_EVENTS_BYTES:
                raise ValueError("Research event log exceeds 64 MiB")
            with self.events_path.open("ab") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            durable_write_json(self.snapshot_path, state)
            return deepcopy(state)

    def observe_direction(
        self, direction: Mapping[str, object], *, origin: Mapping[str, object] | None = None
    ) -> dict[str, Any]:
        direction_id = direction.get("direction_id")
        if not isinstance(direction_id, str):
            raise ValueError("Direction has no ID")
        return self.append(
            "direction.observed",
            {"direction": direction},
            origin=origin,
            # Direction state is mutable: the same in-progress projection may
            # legitimately recur after a defer/reopen cycle.  Deduplicate only
            # against the current projection, never against historical events.
            skip_if_projection_unchanged="directions",
        )

    def observe_experiment(
        self, experiment: Mapping[str, object], *, origin: Mapping[str, object] | None = None
    ) -> dict[str, Any]:
        experiment_id = experiment.get("experiment_id")
        if not isinstance(experiment_id, str):
            raise ValueError("Experiment has no ID")
        return self.append(
            "experiment.observed",
            {"experiment": experiment},
            origin=origin,
            skip_if_projection_unchanged="experiments",
        )

    def prioritize(
        self,
        direction_id: str,
        *,
        before_direction_id: str | None = None,
        after_direction_id: str | None = None,
        reason: str,
        origin: Mapping[str, object] | None = None,
    ) -> dict[str, Any]:
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("Direction reorder requires a non-empty reason")
        return self.append(
            "direction.reordered",
            {
                "direction_id": direction_id,
                "before_direction_id": before_direction_id,
                "after_direction_id": after_direction_id,
                "reason": reason.strip(),
            },
            origin=origin,
        )

    def progress(
        self,
        direction_id: str,
        plan_step: int,
        *,
        origin: Mapping[str, object] | None = None,
    ) -> dict[str, Any]:
        return self.append(
            "direction.progressed",
            {"direction_id": direction_id, "plan_step": plan_step},
            origin=origin,
        )

    def checkpoint_lineage(
        self, values: Mapping[str, object], *, origin: Mapping[str, object] | None = None
    ) -> dict[str, Any]:
        return self.append("lineage.checkpointed", values, origin=origin)

    def synchronize_queue(
        self,
        direction_ids: list[str],
        *,
        origin: Mapping[str, object] | None = None,
    ) -> dict[str, Any]:
        return self.append(
            "queue.synchronized",
            {"direction_ids": direction_ids},
            origin=origin,
            # The same shared order may need to be applied again after a local
            # reorder.  Global event-key deduplication would incorrectly keep
            # that later local order, so deduplicate only when the resulting
            # queue projection is already current.
            skip_if_projection_unchanged="queue",
        )

    def adopt_kernel(
        self,
        kernel_id: str,
        *,
        source_ref: str,
        reason: str,
        origin: Mapping[str, object] | None = None,
    ) -> dict[str, Any]:
        if not isinstance(kernel_id, str) or not kernel_id:
            raise ValueError("Kernel adoption requires a Kernel ID")
        if not isinstance(source_ref, str) or not source_ref.strip():
            raise ValueError("Kernel adoption requires the local or shared Git ref used")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("Kernel adoption requires a non-empty reason")
        return self.append(
            "kernel.adopted",
            {
                "kernel_id": kernel_id,
                "source": {"git_ref": source_ref.strip()},
                "reason": reason.strip(),
            },
            origin=origin,
        )

    def board(self, *, trajectory: int = 1) -> dict[str, Any]:
        state = self.read()
        queue = []
        for rank, direction_id in enumerate(state["queue"], 1):
            item = state["directions"].get(direction_id)
            if not isinstance(item, dict):
                continue
            owner = item.get("owner")
            status = item.get("status")
            queue.append({
                "rank": rank,
                "direction_id": direction_id,
                "name": item.get("name"),
                "change_scope": item.get("change_scope", "local"),
                "status": f"in_progress({owner})" if status == "in_progress" and owner else status,
                "next_step": item.get("next_step"),
            })
            if len(queue) >= MAX_BOARD_DIRECTIONS:
                break
        experiments = list(state["experiments"].values())[-MAX_BOARD_EXPERIMENTS:]
        recent = [
            {
                key: item.get(key)
                for key in ("experiment_id", "direction_id", "name", "interpretation", "after_kernel_id")
                if item.get(key) is not None
            }
            for item in experiments
        ]
        closed = [
            {
                key: item.get(key)
                for key in ("direction_id", "name", "status", "reopen_condition")
                if item.get(key) is not None
            }
            for item in state["directions"].values()
            if item.get("status") in CLOSED_STATUSES
        ][-MAX_BOARD_DIRECTIONS:]
        promotions = state["promotions"]
        incumbent = next(
            (
                item["payload"]
                for item in reversed(promotions)
                if item["type"] == "promotion.accepted"
            ),
            None,
        )
        return {
            "schema_version": "atrex.research_board.v1",
            "revision": state["sequence"],
            "incumbent": incumbent,
            "lineages": deepcopy(state["lineages"]),
            "current_lineage": f"T{trajectory}",
            "open_queue": queue,
            "recent_experiments": recent,
            "closed_directions": closed,
        }
