"""Shape selection for Light's native Eval requests.

Atrex-Bench owns the immutable reference bundle.  Light shards only the list of
opaque Shape IDs carried in ``runner_overrides.shape_ids``; it never rewrites
``reference.shapes``, metadata, or roofline data for an individual shard.
"""

from __future__ import annotations

from typing import Any


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


def shape_batch_request(
    request: dict[str, Any], shape_ids: list[str]
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
    return batched
