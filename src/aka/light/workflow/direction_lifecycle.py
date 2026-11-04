"""Direction transitions shared by request validation and persisted history replay."""

from __future__ import annotations

from aka.light.experiment.identifiers import DIRECTION_ID_RE

DIRECTION_STATUS = {
    "propose": "proposed",
    "start": "in_progress",
    "complete": "completed",
    "abandon": "abandoned",
    "block": "blocked",
    "defer": "deferred",
}
DIRECTION_REVISION_ACTION = "revise"
DIRECTION_ACTIONS = frozenset({*DIRECTION_STATUS, DIRECTION_REVISION_ACTION})
CLOSURES = {"complete", "abandon", "block", "defer"}
CLOSED_STATUSES = {"completed", "abandoned", "blocked", "deferred"}


def advance_direction(
    statuses: dict[str, str],
    direction_id: str,
    action: str,
) -> None:
    """Apply one legal Direction event."""
    if not isinstance(direction_id, str) or DIRECTION_ID_RE.fullmatch(direction_id) is None:
        raise ValueError("Direction ID has an invalid format")
    if not isinstance(action, str) or action not in DIRECTION_ACTIONS:
        raise ValueError(
            "Direction action must be propose, revise, start, complete, abandon, block, or defer"
        )
    status = statuses.get(direction_id)
    if action == "propose":
        if status is not None:
            raise ValueError(f"Direction {direction_id} was already proposed")
    elif status is None:
        raise ValueError(f"Direction ID is outside visible history: {direction_id}")
    elif action == DIRECTION_REVISION_ACTION:
        if status not in {"proposed", "in_progress"}:
            raise ValueError(
                f"Direction cannot revise from status {status}; start it before revising"
            )
        return
    elif action == "start":
        if status not in {"proposed", *CLOSED_STATUSES}:
            raise ValueError(f"Direction cannot start from status {status}")
    elif status not in {"in_progress", *CLOSED_STATUSES}:
        raise ValueError(f"Direction cannot {action} from status {status}")
    statuses[direction_id] = DIRECTION_STATUS[action]
