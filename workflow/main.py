#!/usr/bin/env python3
"""Default executable evolve-and-pool Workflow for one complete Epoch."""

from __future__ import annotations

from runtime import EpochPool, EpochRuntime, serve


def run_epoch(epoch: EpochRuntime) -> None:
    epoch_number = int(epoch.context["epoch_number"])
    first_epoch_same_agent = bool(epoch.context["first_epoch_same_agent"])
    max_challengers = int(epoch.limits["max_challengers"])
    total_attempts = int(epoch.limits["optimizer_attempts"])
    default_trajectories = int(epoch.limits["default_trajectories"])
    state_policy = str(epoch.limits["default_runtime_state_policy"])

    challengers: list[int] = []
    for ordinal in range(1, max_challengers + 1):
        if epoch_number == 1 and first_epoch_same_agent:
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
        trajectories = default_trajectories
        if branch_attempts % trajectories:
            trajectories = 1
        return epoch.create_pool(
            branch=name,
            trajectories=trajectories,
            rounds=branch_attempts // trajectories,
            runtime_state_policy=state_policy,
        )

    branch_names = ["active", *(f"challenger-{ordinal}" for ordinal in challengers)]
    pools = [branch(name, ordinal) for ordinal, name in enumerate(branch_names)]
    epoch.run_pools(pools)
    epoch.complete()


if __name__ == "__main__":
    raise SystemExit(serve(run_epoch))
