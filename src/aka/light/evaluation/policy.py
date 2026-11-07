"""Light's measurement defaults, with Legacy-aligned timing and warmup counts.

Keep these local to Light: selecting another AKA composition does not change
its evaluator or acceptance policy. Explicit request/operator overrides still
participate in the measurement identity.
"""

from aka.contracts.evaluation import (
    DEFAULT_CANDIDATE_TIMEOUT,
    DEFAULT_PERFORMANCE_TIMEOUT,
    DEFAULT_SHAPE_BATCH_SIZE,
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
# Light creates one independent Eval job for each outer batch.  The job's
# projected reference treats those IDs as its complete Shape set instead of a
# Shape selector.  Do not set Agate's ``shape_shards`` option: omitting it
# enables automatic Ray Data execution with the admitted worker budget, while
# an explicit value is a hard maximum that can be rejected when it exceeds
# current capacity.
SHAPE_BATCH_SIZE = DEFAULT_SHAPE_BATCH_SIZE
BATCH_WORKERS = 16
MEASUREMENT_CONTRACT = "runtime-native-eval-v1"
ACCEPTANCE_METRIC = "geomean-latency-reduction-v1"

# Product defaults must be real supported clock bins, not marketing boost
# frequencies.  L20N was probed through the production Agate environment on
# 2026-11-07: requesting 2377 MHz is realized as 2370 MHz by the production
# worker, and the evaluator verifies the realized clock exactly.  Use that
# realizable bin by default. Operators may override this per Campaign when
# another sustainable bin is desired.
DEFAULT_GPU_CLOCK_MHZ_BY_HARDWARE = {
    "L20N": 2370,
    "RTXPRO5000": 2370,
}


def validate_abba_repeats(repeats: int) -> None:
    if type(repeats) is not int or not 2 <= repeats <= 16 or repeats % 2:
        raise ValueError("--comparison-repeats must be even, in 2..16 (measurements per side)")


def resolved_gpu_clock_mhz(
    hardware: str,
    configured: int | None = None,
) -> int | None:
    """Return the explicit or product-default graphics clock for Eval.

    A missing product default does not disable clock locking: every Eval still
    carries ``lock_clocks=true``.  It only leaves the precise bin to Agate until
    the operator supplies ``--gpu-clock-mhz`` or this table gains a verified
    default for that product.
    """
    if configured is not None:
        if type(configured) is not int or configured <= 0:
            raise ValueError("GPU clock MHz must be a positive integer")
        return configured
    product = "".join(
        character
        for character in str(hardware).strip().upper()
        if character.isalnum()
    )
    if "L20N" in product:
        product = "L20N"
    elif "RTXPRO5000" in product:
        product = "RTXPRO5000"
    return DEFAULT_GPU_CLOCK_MHZ_BY_HARDWARE.get(product)


def runner_overrides(
    step_timeout: int = CANDIDATE_TIMEOUT,
    *,
    gpu_clock_mhz: int | None = None,
) -> dict:
    """Return Runtime-owned Bench limits shared by V0 and every candidate."""
    if type(step_timeout) is not int or step_timeout <= 0:
        raise ValueError("Evaluation step timeout must be a positive integer")
    overrides = {
        "warmup_iters": WARMUP_ITERS,
        "candidate_timeout_s": step_timeout,
        "perf_timeout_s": step_timeout,
        "skip_kernel_attribution": True,
        # Clock locking is best-effort.  Preserve the requested target and
        # measurement trace, but never reject otherwise valid evaluation
        # evidence because a worker realizes or samples a nearby clock bin.
        "clock_lock_fail_on_deviation": False,
    }
    if gpu_clock_mhz is not None:
        if type(gpu_clock_mhz) is not int or gpu_clock_mhz <= 0:
            raise ValueError("GPU clock MHz must be a positive integer")
        overrides["gpu_clock_mhz"] = gpu_clock_mhz
    return overrides


def measurement_defaults(
    hardware: str = "",
    gpu_clock_mhz: int | None = None,
) -> dict:
    clock = resolved_gpu_clock_mhz(hardware, gpu_clock_mhz)
    return {"contract": MEASUREMENT_CONTRACT, "bench_iters": BENCH_ITERS,
            "correctness_cases": CORRECTNESS_CASES, "lock_clocks": True,
            "runner_overrides": runner_overrides(gpu_clock_mhz=clock)}
