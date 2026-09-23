"""Agent-facing SDK for orchestrating exactly one Runtime-owned Epoch."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any


class WorkflowRuntimeError(RuntimeError):
    """A trusted Runtime service rejected one Workflow operation."""


@dataclass(frozen=True, slots=True)
class AgentStateRef:
    """Opaque immutable Agent State selected by Workflow code."""

    _source_attempt_id: str | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class _Trajectory:
    """Stable handle for one Runtime-isolated optimization sequence."""

    branch: str
    ordinal: int
    attempt_capacity: int
    kernel_agent_revision_id: str
    initial_state: AgentStateRef


@dataclass(frozen=True, slots=True)
class _AttemptLaunch:
    """One explicit, replay-safe logical Attempt requested by Workflow code."""

    trajectory: _Trajectory
    ordinal: int
    input_state: AgentStateRef
    input_kernel_revision_id: str | None = None


class _WorkflowClient:
    """Private wire client; Agent Workflow code should use :class:`EpochRuntime`."""

    def __init__(self) -> None:
        line = sys.stdin.readline()
        if not line:
            raise WorkflowRuntimeError("Runtime did not provide Workflow context")
        value = json.loads(line)
        if (
            not isinstance(value, dict)
            or value.get("schema_version") != 1
            or value.get("operation") != "run_epoch"
        ):
            raise WorkflowRuntimeError("unsupported Workflow context")
        context = value.get("context")
        limits = value.get("limits")
        if not isinstance(context, dict) or not isinstance(limits, dict):
            raise WorkflowRuntimeError("Workflow context is incomplete")
        self.context: dict[str, Any] = context
        self.limits: dict[str, Any] = limits
        self._next_request = 1

    def replicate_active(self, challenger_ordinal: int) -> str:
        result = self._call("replicate_active", {"challenger_ordinal": challenger_ordinal})
        return self._required_string(result, "kernel_agent_revision_id")

    def evolve_agent(self, challenger_ordinal: int) -> str | None:
        result = self._call("evolve_agent", {"challenger_ordinal": challenger_ordinal})
        revision = result.get("kernel_agent_revision_id")
        if revision is None:
            return None
        if not isinstance(revision, str):
            raise WorkflowRuntimeError("evolve_agent returned an invalid Agent identity")
        return revision

    def create_trajectory(
        self,
        *,
        branch: str,
        ordinal: int,
        trajectory_count: int,
        attempt_capacity: int,
    ) -> _Trajectory:
        """Register one handle within an immutable Branch capacity plan."""
        result = self._call(
            "create_trajectory",
            {
                "branch": branch,
                "trajectory_ordinal": ordinal,
                "trajectory_count": trajectory_count,
                "attempt_capacity": attempt_capacity,
            },
        )
        return _Trajectory(
            branch=self._required_string(result, "branch"),
            ordinal=self._required_int(result, "trajectory_ordinal"),
            attempt_capacity=self._required_int(result, "attempt_capacity"),
            kernel_agent_revision_id=self._required_string(result, "kernel_agent_revision_id"),
            initial_state=AgentStateRef(),
        )

    def run_attempts_parallel(
        self,
        launches: Sequence[_AttemptLaunch],
    ) -> list[dict[str, Any]]:
        """Run one explicit Attempt per Trajectory concurrently."""
        if not launches:
            raise WorkflowRuntimeError("parallel Attempt batch cannot be empty")
        result = self._call(
            "run_attempts_parallel",
            {
                "launches": [
                    {
                        "branch": launch.trajectory.branch,
                        "trajectory_ordinal": launch.trajectory.ordinal,
                        "attempt_ordinal": launch.ordinal,
                        "input_kernel_revision_id": launch.input_kernel_revision_id,
                        "input_state_from_attempt_id": launch.input_state._source_attempt_id,
                    }
                    for launch in launches
                ]
            },
        )
        attempts = result.get("attempts")
        if not isinstance(attempts, list) or not all(
            isinstance(attempt, dict) for attempt in attempts
        ):
            raise WorkflowRuntimeError("Runtime returned invalid Attempt outcomes")
        return attempts

    def select_best_kernel(self) -> str:
        result = self._call("select_best_kernel", {})
        return self._required_string(result, "kernel_revision_id")

    def compare_agents(self) -> str:
        result = self._call("compare_agents", {})
        return self._required_string(result, "kernel_agent_revision_id")

    def complete_epoch(
        self,
        *,
        kernel_revision_id: str,
        kernel_agent_revision_id: str,
    ) -> dict[str, Any]:
        return self._call(
            "complete_epoch",
            {
                "kernel_revision_id": kernel_revision_id,
                "kernel_agent_revision_id": kernel_agent_revision_id,
            },
        )

    def _call(self, operation: str, arguments: dict[str, Any]) -> dict[str, Any]:
        request_id = f"workflow-call-{self._next_request}"
        self._next_request += 1
        sys.stdout.write(
            json.dumps(
                {
                    "request_id": request_id,
                    "operation": operation,
                    "arguments": arguments,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        )
        sys.stdout.flush()
        line = sys.stdin.readline()
        if not line:
            raise WorkflowRuntimeError(f"Runtime closed the Workflow channel during {operation}")
        response = json.loads(line)
        if not isinstance(response, dict) or response.get("request_id") != request_id:
            raise WorkflowRuntimeError("Runtime returned a mismatched Workflow response")
        if response.get("ok") is not True:
            error = response.get("error")
            if isinstance(error, dict):
                kind = error.get("type", "WorkflowOperationError")
                message = error.get("message", "Runtime rejected Workflow operation")
                raise WorkflowRuntimeError(f"{kind}: {message}")
            raise WorkflowRuntimeError("Runtime rejected Workflow operation")
        result = response.get("result")
        if not isinstance(result, dict):
            raise WorkflowRuntimeError("Runtime returned a non-object Workflow result")
        return result

    @staticmethod
    def _required_string(result: dict[str, Any], name: str) -> str:
        value = result.get(name)
        if not isinstance(value, str) or not value:
            raise WorkflowRuntimeError(f"Runtime omitted {name}")
        return value

    @staticmethod
    def _required_int(result: dict[str, Any], name: str) -> int:
        value = result.get(name)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise WorkflowRuntimeError(f"Runtime omitted valid {name}")
        return value


@dataclass(frozen=True, slots=True)
class EpochPool:
    """One Branch-local pool advanced in synchronized rounds within this Epoch."""

    branch: str
    trajectory_count: int
    rounds: int
    _trajectories: tuple[_Trajectory, ...] = field(repr=False)


@dataclass(slots=True)
class EpochRound:
    """Trusted outcomes for one logical round and routing choices for the next one."""

    number: int
    _results: dict[EpochPool, tuple[dict[str, Any], ...]]
    _kernel_routes: dict[tuple[EpochPool, int], str] = field(default_factory=dict, repr=False)
    _state_routes: dict[tuple[EpochPool, int], AgentStateRef] = field(
        default_factory=dict,
        repr=False,
    )

    def outcomes(self, pool: EpochPool) -> tuple[Mapping[str, Any], ...]:
        """Return trusted outcomes for this Pool in Trajectory order."""
        try:
            return self._results[pool]
        except KeyError as error:
            raise WorkflowRuntimeError("Pool did not participate in this round") from error

    def best_accepted_kernel(self, *pools: EpochPool) -> str | None:
        """Return the lowest-latency accepted Kernel in the selected Pools."""
        selected = pools or tuple(self._results)
        accepted: list[dict[str, Any]] = []
        for pool in selected:
            for outcome in self._results.get(pool, ()):
                latency = outcome.get("latency_us")
                revision = outcome.get("trajectory_kernel_revision_id")
                if (
                    outcome.get("accepted") is True
                    and isinstance(latency, (int, float))
                    and not isinstance(latency, bool)
                    and isinstance(revision, str)
                    and revision
                ):
                    accepted.append(outcome)
        if not accepted:
            return None
        best = min(accepted, key=lambda item: float(item["latency_us"]))
        return str(best["trajectory_kernel_revision_id"])

    def route_kernel(
        self,
        pool: EpochPool,
        *,
        trajectory_ordinal: int,
        kernel_revision_id: str,
    ) -> None:
        """Use one accepted same-Epoch Kernel as one Trajectory's next-round input."""
        self._require_future_round(pool)
        if trajectory_ordinal <= 0 or trajectory_ordinal > pool.trajectory_count:
            raise WorkflowRuntimeError("Kernel route names an unknown Trajectory")
        if not kernel_revision_id:
            raise WorkflowRuntimeError("Kernel route requires a revision identity")
        self._kernel_routes[(pool, trajectory_ordinal)] = kernel_revision_id

    def route_state(
        self,
        pool: EpochPool,
        *,
        trajectory_ordinal: int,
        state: AgentStateRef,
    ) -> None:
        """Use one explicit immutable State as one Trajectory's next-round input."""
        self._require_future_round(pool)
        if trajectory_ordinal <= 0 or trajectory_ordinal > pool.trajectory_count:
            raise WorkflowRuntimeError("State route names an unknown Trajectory")
        if not isinstance(state, AgentStateRef) or state._source_attempt_id is None:
            raise WorkflowRuntimeError("State route requires a completed Attempt output State")
        self._state_routes[(pool, trajectory_ordinal)] = state

    def _require_future_round(self, pool: EpochPool) -> None:
        if pool not in self._results:
            raise WorkflowRuntimeError("Pool did not participate in this round")
        if self.number >= pool.rounds:
            raise WorkflowRuntimeError("Pool has no later round to route")


class EpochRuntime:
    """Public, single-Epoch orchestration surface for Agent-owned Workflow code."""

    def __init__(self, client: _WorkflowClient | None = None) -> None:
        self._client = client or _WorkflowClient()
        self.context: Mapping[str, Any] = dict(self._client.context)
        self.limits: Mapping[str, Any] = dict(self._client.limits)
        self._pools: list[EpochPool] = []
        self._ran_pools = False
        self._completed = False

    def replicate_active(self, challenger_ordinal: int) -> str:
        return self._client.replicate_active(challenger_ordinal)

    def evolve_agent(self, challenger_ordinal: int) -> str | None:
        return self._client.evolve_agent(challenger_ordinal)

    def create_pool(
        self,
        *,
        branch: str,
        trajectories: int,
        rounds: int,
    ) -> EpochPool:
        """Create one Branch Pool without exposing Attempt identities or ordinals."""
        if self._completed or self._ran_pools:
            raise WorkflowRuntimeError("Pools must all be created before Epoch execution starts")
        if not branch or trajectories <= 0 or rounds <= 0:
            raise WorkflowRuntimeError("Pool requires a Branch and positive topology")
        if any(pool.branch == branch for pool in self._pools):
            raise WorkflowRuntimeError(f"Branch {branch!r} already has a Pool")
        handles = tuple(
            self._client.create_trajectory(
                branch=branch,
                ordinal=ordinal,
                trajectory_count=trajectories,
                attempt_capacity=rounds,
            )
            for ordinal in range(1, trajectories + 1)
        )
        pool = EpochPool(
            branch=branch,
            trajectory_count=trajectories,
            rounds=rounds,
            _trajectories=handles,
        )
        self._pools.append(pool)
        return pool

    def run_pools(
        self,
        pools: Sequence[EpochPool],
        *,
        after_round: Callable[[EpochRound], None] | None = None,
    ) -> tuple[EpochRound, ...]:
        """Advance Pools round by round; Runtime executes each round concurrently."""
        selected = tuple(pools)
        if self._completed:
            raise WorkflowRuntimeError("Epoch is already complete")
        if self._ran_pools:
            raise WorkflowRuntimeError("Epoch Pools have already run")
        if not selected:
            raise WorkflowRuntimeError("run_pools requires at least one Pool")
        if len(set(selected)) != len(selected) or any(pool not in self._pools for pool in selected):
            raise WorkflowRuntimeError("run_pools received an unknown or duplicate Pool")
        if set(selected) != set(self._pools):
            raise WorkflowRuntimeError("run_pools must include every Pool created for this Epoch")
        planned = sum(pool.trajectory_count * pool.rounds for pool in selected)
        capacity = int(self.limits["optimizer_attempts"])
        if planned != capacity:
            raise WorkflowRuntimeError(
                f"Epoch Pools plan {planned} Attempts but must allocate the exact "
                f"Runtime budget of {capacity}"
            )
        self._ran_pools = True

        kernel_routes: dict[tuple[EpochPool, int], str] = {}
        state_routes: dict[tuple[EpochPool, int], AgentStateRef] = {}
        completed_rounds: list[EpochRound] = []
        for round_number in range(1, max(pool.rounds for pool in selected) + 1):
            participants = tuple(pool for pool in selected if round_number <= pool.rounds)
            launches: list[_AttemptLaunch] = []
            owners: list[EpochPool] = []
            for pool in participants:
                for trajectory in pool._trajectories:
                    owners.append(pool)
                    launches.append(
                        _AttemptLaunch(
                            trajectory=trajectory,
                            ordinal=round_number,
                            input_state=state_routes.get(
                                (pool, trajectory.ordinal),
                                trajectory.initial_state,
                            ),
                            input_kernel_revision_id=kernel_routes.get(
                                (pool, trajectory.ordinal)
                            ),
                        )
                    )
            raw = self._client.run_attempts_parallel(launches)
            grouped: dict[EpochPool, list[dict[str, Any]]] = {pool: [] for pool in participants}
            for pool, outcome in zip(owners, raw, strict=True):
                projected = dict(outcome)
                source_attempt = projected.pop("output_state_from_attempt_id", None)
                if source_attempt is None:
                    projected["output_state"] = None
                elif isinstance(source_attempt, str) and source_attempt:
                    projected["output_state"] = AgentStateRef(source_attempt)
                else:
                    raise WorkflowRuntimeError(
                        "Runtime returned an invalid output Agent State reference"
                    )
                grouped[pool].append(projected)
            current = EpochRound(
                number=round_number,
                _results={pool: tuple(values) for pool, values in grouped.items()},
            )
            if after_round is not None:
                after_round(current)
            kernel_routes = dict(current._kernel_routes)
            state_routes = dict(current._state_routes)
            completed_rounds.append(current)
        return tuple(completed_rounds)

    def complete(self) -> Mapping[str, Any]:
        """Ask Runtime to select the trusted Kernel and Agent and commit this Epoch."""
        if self._completed:
            raise WorkflowRuntimeError("Epoch is already complete")
        if not self._ran_pools:
            raise WorkflowRuntimeError("Epoch cannot complete before its Pools run")
        kernel = self._client.select_best_kernel()
        agent = self._client.compare_agents()
        result = self._client.complete_epoch(
            kernel_revision_id=kernel,
            kernel_agent_revision_id=agent,
        )
        self._completed = True
        return result


def serve(run_epoch: Callable[[EpochRuntime], None]) -> int:
    """Run one Agent-defined Epoch policy over the trusted Workflow channel."""
    epoch = EpochRuntime()
    run_epoch(epoch)
    if not epoch._completed:
        raise WorkflowRuntimeError("run_epoch returned without completing the Epoch")
    return 0


__all__ = [
    "AgentStateRef",
    "EpochPool",
    "EpochRound",
    "EpochRuntime",
    "WorkflowRuntimeError",
    "serve",
]
