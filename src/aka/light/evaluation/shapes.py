"""Shape selection for Light's native Eval requests.

Light's outer shards are independent Agate Eval jobs.  Each job receives a
reference view containing only that shard's Shapes, so those Shapes are the
job's complete workload rather than a ``runner_overrides.shape_ids`` subset.
That boundary keeps the request eligible for Agate's automatic Ray Data
execution while preserving the original opaque Shape IDs for result merging.
"""

from __future__ import annotations

from typing import Any

from aka.contracts.evaluation import (
    DEFAULT_PERFORMANCE_TIMEOUT,
    MAX_TYPED_GATEWAY_JOB_TIMEOUT,
)


def _sort_key(shape_id: str) -> tuple[int, object]:
    return (0, int(shape_id)) if shape_id.isdigit() else (1, shape_id)


def request_shape_ids(request: dict[str, Any]) -> list[str]:
    """Return the effective Shape selection in canonical reference order."""
    shapes = request["reference"]["shapes"]
    all_shape_ids = sorted((str(shape_id) for shape_id in shapes), key=_sort_key)
    overrides = request.get("runner_overrides")
    selection = overrides.get("shape_ids") if isinstance(overrides, dict) else None
    if selection is None:
        return all_shape_ids
    if (
        not isinstance(selection, list)
        or not selection
        or any(not isinstance(shape_id, str) or not shape_id for shape_id in selection)
    ):
        raise ValueError("runner_overrides.shape_ids must be a non-empty list of strings")
    if len(set(selection)) != len(selection):
        raise ValueError("runner_overrides.shape_ids contains duplicate IDs")
    unknown = set(selection).difference(all_shape_ids)
    if unknown:
        raise ValueError(
            "runner_overrides.shape_ids contains unknown IDs: "
            + ", ".join(sorted(unknown, key=_sort_key))
        )
    selected = set(selection)
    return [shape_id for shape_id in all_shape_ids if shape_id in selected]


def batch_shape_ids(shape_ids: list[str], batch_size: int) -> list[list[str]]:
    if type(batch_size) is not int or batch_size < 1:
        raise ValueError("Shape batch size must be a positive integer")
    return [
        shape_ids[offset : offset + batch_size]
        for offset in range(0, len(shape_ids), batch_size)
    ]


def shape_request_timeout(
    base_timeout: int,
    shape_count: int,
    *,
    performance_timeout: int = DEFAULT_PERFORMANCE_TIMEOUT,
) -> int:
    """Return the allocation for one concrete multi-Shape request.

    ``base_timeout`` already covers the request's fixed work and one Shape's
    performance phase.  Atrex-Bench measures the selected Shapes sequentially,
    so only the performance phase is repeated for each additional Shape.  Do
    not multiply candidate initialization, correctness, queueing, or cleanup.
    """
    if type(base_timeout) is not int or base_timeout <= 0:
        raise ValueError("Base timeout must be a positive integer")
    if type(shape_count) is not int or shape_count <= 0:
        raise ValueError("Shape count must be a positive integer")
    if type(performance_timeout) is not int or performance_timeout <= 0:
        raise ValueError("Performance timeout must be a positive integer")
    return base_timeout + performance_timeout * (shape_count - 1)


def effective_shape_batch_size(
    base_timeout: int,
    requested_batch_size: int,
    queue_wait_grace: int,
    *,
    performance_timeout: int = DEFAULT_PERFORMANCE_TIMEOUT,
) -> int:
    """Fit Shape shards under Agate's typed-job deadline.

    The configured batch size is an upper bound. If its Shape-scaled deadline
    would exceed the service limit, make smaller requests instead of silently
    shortening their execution budget.
    """
    if type(requested_batch_size) is not int or requested_batch_size <= 0:
        raise ValueError("Shape batch size must be a positive integer")
    if type(queue_wait_grace) is not int or queue_wait_grace < 0:
        raise ValueError("Queue wait grace must be a non-negative integer")
    available = MAX_TYPED_GATEWAY_JOB_TIMEOUT - queue_wait_grace
    if base_timeout > available:
        raise ValueError(
            "One Shape exceeds Agate's 10800-second typed-job limit: "
            f"base timeout {base_timeout}s + queue grace "
            f"{queue_wait_grace}s. Reduce the per-Shape timeout or queue grace."
        )
    if type(performance_timeout) is not int or performance_timeout <= 0:
        raise ValueError("Performance timeout must be a positive integer")
    max_shapes = 1 + (available - base_timeout) // performance_timeout
    return min(requested_batch_size, max_shapes)


def shape_batch_request(
    request: dict[str, Any],
    shape_ids: list[str],
    *,
    timeout_s: int | None = None,
) -> dict[str, Any]:
    """Build one independent Ray-eligible Eval job for a Shape shard.

    The caller's request remains immutable.  The returned request owns a
    reference view in which ``shape_ids`` are the complete Shape set, removes
    any Shape selector, and disables single-device clock locking.  Omitting
    ``options.shape_shards`` deliberately selects Agate's automatic Ray Data
    worker budget unless the caller explicitly supplied a hard worker cap.
    """
    available = set(request_shape_ids(request))
    if not shape_ids:
        raise ValueError("Shape batch must not be empty")
    if len(set(shape_ids)) != len(shape_ids):
        raise ValueError("Shape batch contains duplicate IDs")
    unknown = set(shape_ids).difference(available)
    if unknown:
        raise ValueError(
            "Shape batch contains IDs outside the request selection: "
            + ", ".join(sorted(unknown, key=_sort_key))
        )
    selected = set(shape_ids)
    reference = dict(request["reference"])
    reference["shapes"] = {
        str(shape_id): value
        for shape_id, value in request["reference"]["shapes"].items()
        if str(shape_id) in selected
    }
    for field, indexes in (("metadata", ("shapes", "cases")),
                           ("roofline", ("shapes",))):
        source = request["reference"].get(field)
        if not isinstance(source, dict):
            continue
        projected = dict(source)
        for index in indexes:
            values = source.get(index)
            if isinstance(values, dict):
                projected[index] = {
                    str(shape_id): value
                    for shape_id, value in values.items()
                    if str(shape_id) in selected
                }
        if field == "metadata" and "num_shapes" in projected:
            projected["num_shapes"] = len(shape_ids)
        reference[field] = projected

    batched = dict(request)
    batched["reference"] = reference
    overrides = dict(request.get("runner_overrides") or {})
    overrides.pop("shape_ids", None)
    batched["runner_overrides"] = overrides
    batched["lock_clocks"] = False
    if timeout_s is not None:
        if type(timeout_s) is not int or timeout_s <= 0:
            raise ValueError("Shape batch timeout must be a positive integer")
        options = dict(request.get("options") or {})
        options["timeout_s"] = timeout_s
        batched["options"] = options
    return batched
