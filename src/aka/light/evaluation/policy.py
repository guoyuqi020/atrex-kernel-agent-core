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
# Budget each Shape independently. Candidate initialization/correctness retains
# the production-safe 600-second allowance; steady-state measurement is 300
# seconds. Operators may still override the per-Shape allocation; each concrete
# Eval request scales it by the number of selected Shapes in that shard.
CANDIDATE_TIMEOUT = DEFAULT_CANDIDATE_TIMEOUT
PERFORMANCE_TIMEOUT = DEFAULT_PERFORMANCE_TIMEOUT
SHAPE_BATCH_SIZE = 5
BATCH_WORKERS = 16
MEASUREMENT_CONTRACT = "runtime-native-eval-v1"
ACCEPTANCE_METRIC = "geomean-latency-reduction-v1"


def validate_abba_repeats(repeats: int) -> None:
    if type(repeats) is not int or not 2 <= repeats <= 16 or repeats % 2:
        raise ValueError("--comparison-repeats must be even, in 2..16 (measurements per side)")


def runner_overrides(*, performance_timeout_s: int | None = None) -> dict:
    """Return Runtime-owned Bench limits for one Eval request.

    Ordinary candidate exploration retains the shorter steady-state timeout so
    a broken Kernel returns promptly. V0 is trusted reference code rather than
    an Agent candidate, so its caller may grant the complete per-Shape request
    allocation instead of misclassifying a slow PyTorch baseline as a bad
    Kernel.
    """
    performance_timeout = (
        PERFORMANCE_TIMEOUT
        if performance_timeout_s is None
        else performance_timeout_s
    )
    if type(performance_timeout) is not int or performance_timeout <= 0:
        raise ValueError("Performance timeout must be a positive integer")
    return {"warmup_iters": WARMUP_ITERS, "candidate_timeout_s": CANDIDATE_TIMEOUT,
            "perf_timeout_s": performance_timeout, "skip_kernel_attribution": True}


def measurement_defaults() -> dict:
    return {"contract": MEASUREMENT_CONTRACT, "bench_iters": BENCH_ITERS,
            "correctness_cases": CORRECTNESS_CASES, "lock_clocks": True,
            "runner_overrides": runner_overrides()}
