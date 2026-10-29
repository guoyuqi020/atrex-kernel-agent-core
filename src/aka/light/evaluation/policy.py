"""Light's measurement defaults, with Legacy-aligned timing and warmup counts.

Keep these local to Light: selecting another AKA composition does not change
its evaluator or acceptance policy. Explicit request/operator overrides still
participate in the measurement identity.
"""

from aka.contracts.evaluation import (
    DEFAULT_CANDIDATE_TIMEOUT,
    DEFAULT_PERFORMANCE_TIMEOUT,
)


# One base case plus four additional deterministic input cases per Shape.
CORRECTNESS_CASES = 5
BENCH_ITERS = 20
WARMUP_ITERS = 5
# The Runtime-owned step timeout supplies both aliases at request construction.
# A multi-Shape request keeps fixed work single and adds only the sequential
# performance budget for each extra Shape.
CANDIDATE_TIMEOUT = DEFAULT_CANDIDATE_TIMEOUT
PERFORMANCE_TIMEOUT = DEFAULT_PERFORMANCE_TIMEOUT
SHAPE_BATCH_SIZE = 5
BATCH_WORKERS = 16
MEASUREMENT_CONTRACT = "runtime-native-eval-v1"
ACCEPTANCE_METRIC = "geomean-latency-reduction-v1"


def validate_abba_repeats(repeats: int) -> None:
    if type(repeats) is not int or not 2 <= repeats <= 16 or repeats % 2:
        raise ValueError("--comparison-repeats must be even, in 2..16 (measurements per side)")


def runner_overrides(
    step_timeout: int = CANDIDATE_TIMEOUT,
) -> dict:
    """Return Runtime-owned Bench limits shared by V0 and every candidate."""
    if type(step_timeout) is not int or step_timeout <= 0:
        raise ValueError("Evaluation step timeout must be a positive integer")
    return {"warmup_iters": WARMUP_ITERS, "candidate_timeout_s": step_timeout,
            "perf_timeout_s": step_timeout, "skip_kernel_attribution": True}


def measurement_defaults() -> dict:
    return {"contract": MEASUREMENT_CONTRACT, "bench_iters": BENCH_ITERS,
            "correctness_cases": CORRECTNESS_CASES, "lock_clocks": True,
            "runner_overrides": runner_overrides()}
