"""Shape selection for Light's native Eval requests.

Atrex-Bench owns the immutable reference bundle.  Light shards only the list of
opaque Shape IDs carried in ``runner_overrides.shape_ids``; it never rewrites
``reference.shapes``, metadata, or roofline data for an individual shard.
"""

from __future__ import annotations

from typing import Any

from aka.contracts.evaluation import MAX_TYPED_GATEWAY_JOB_TIMEOUT


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


def shape_request_timeout(per_shape_timeout: int, shape_count: int) -> int:
    """Return the execution allocation for one concrete Shape request.

    ``--timeout`` is a per-Shape budget. Atrex-Bench evaluates the selected
    Shapes sequentially inside one Eval job, so a shard containing N Shapes
    needs N times that budget.
    """
    if type(per_shape_timeout) is not int or per_shape_timeout <= 0:
        raise ValueError("Per-Shape timeout must be a positive integer")
    if type(shape_count) is not int or shape_count <= 0:
        raise ValueError("Shape count must be a positive integer")
    return per_shape_timeout * shape_count


def effective_shape_batch_size(
    per_shape_timeout: int,
    requested_batch_size: int,
    queue_wait_grace: int,
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
    per_shape = shape_request_timeout(per_shape_timeout, 1)
    available = MAX_TYPED_GATEWAY_JOB_TIMEOUT - queue_wait_grace
    if per_shape > available:
        raise ValueError(
            "One Shape exceeds Agate's 10800-second typed-job limit: "
            f"per-Shape timeout {per_shape_timeout}s + queue grace "
            f"{queue_wait_grace}s. Reduce the per-Shape timeout or queue grace."
        )
    return min(requested_batch_size, available // per_shape)


def shape_batch_request(
    request: dict[str, Any],
    shape_ids: list[str],
    *,
    timeout_s: int | None = None,
) -> dict[str, Any]:
    """Select one shard without copying or mutating the reference bundle."""
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
    batched = dict(request)
    overrides = dict(request.get("runner_overrides") or {})
    overrides["shape_ids"] = list(shape_ids)
    batched["runner_overrides"] = overrides
    if timeout_s is not None:
        if type(timeout_s) is not int or timeout_s <= 0:
            raise ValueError("Shape batch timeout must be a positive integer")
        options = dict(request.get("options") or {})
        options["timeout_s"] = timeout_s
        batched["options"] = options
    return batched
