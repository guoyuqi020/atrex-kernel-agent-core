#!/usr/bin/env python3
"""Default executable evolve-and-pool Workflow for one complete Epoch."""

from __future__ import annotations

from runtime import AgentStateRef, EpochPool, EpochRound, EpochRuntime, serve


def run_epoch(epoch: EpochRuntime) -> None:
    epoch_number = int(epoch.context["epoch_number"])
    max_challengers = int(epoch.limits["max_challengers"])
    total_attempts = int(epoch.limits["optimizer_attempts"])

    challengers: list[int] = []
    for ordinal in range(1, max_challengers + 1):
        if epoch_number == 1:
            epoch.replicate_active(ordinal)
            challengers.append(ordinal)
            continue
        if epoch.evolve_agent(ordinal) is None:
            break
        challengers.append(ordinal)

    branch_count = 1 + len(challengers)
    attempts_per_branch, extra_attempts = divmod(total_attempts, branch_count)
    if attempts_per_branch <= 0:
        raise ValueError("Optimizer Attempt budget cannot cover every Workflow Branch")

    def branch(name: str, ordinal: int) -> EpochPool:
        branch_attempts = attempts_per_branch + int(ordinal < extra_attempts)
        return epoch.create_pool(
            branch=name,
            trajectories=1,
            rounds=branch_attempts,
        )

    branch_names = ["active", *(f"challenger-{ordinal}" for ordinal in challengers)]
    pools = [branch(name, ordinal) for ordinal, name in enumerate(branch_names)]

    def carry_each_trajectory_state(current: EpochRound) -> None:
        for pool in pools:
            if current.number >= pool.rounds:
                continue
            for outcome in current.outcomes(pool):
                ordinal = int(outcome["trajectory_ordinal"])
                current.route_kernel(
                    pool,
                    trajectory_ordinal=ordinal,
                    kernel_revision_id=str(outcome["trajectory_kernel_revision_id"]),
                )
                state = outcome["output_state"]
                if not isinstance(state, AgentStateRef):
                    raise TypeError("Attempt outcome omitted its Agent State")
                current.route_state(
                    pool,
                    trajectory_ordinal=ordinal,
                    state=state,
                )

    epoch.run_pools(pools, after_round=carry_each_trajectory_state)
    epoch.complete()


if __name__ == "__main__":
    raise SystemExit(serve(run_epoch))
