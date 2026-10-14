"""Read the active-time clock owned by Runtime, never update its accounting."""

from __future__ import annotations

import json
import math
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

SESSION_CLOCK_ENV = "ATREX_SESSION_CLOCK_PATH"


def _state(environment: Mapping[str, str] | None) -> dict[str, Any] | None:
    path = (os.environ if environment is None else environment).get(SESSION_CLOCK_ENV)
    if not path:
        return None
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValueError("Invalid Runtime session clock")
    waited = value.get("wait_seconds")
    paused = value.get("paused_since")
    if (
        not isinstance(waited, (int, float))
        or isinstance(waited, bool)
        or not math.isfinite(waited)
        or waited < 0
        or (paused is not None and (
            not isinstance(paused, (int, float)) or isinstance(paused, bool)
            or not math.isfinite(paused) or paused < 0
        ))
    ):
        raise ValueError("Invalid Runtime session clock counters")
    return value


def runtime_waiting(*, environment: Mapping[str, str] | None = None) -> bool:
    """An HTTP client exit does not terminate a request still owned by Runtime."""
    value = _state(environment)
    return value is not None and value.get("paused_since") is not None


def active_time(now: float, *, environment: Mapping[str, str] | None = None) -> float:
    """Keep legacy launches on wall time; subtract trusted Runtime waits when bound."""
    value = _state(environment)
    if value is None:
        return now
    paused = value.get("paused_since")
    return float((now if paused is None else min(now, paused)) - value["wait_seconds"])
