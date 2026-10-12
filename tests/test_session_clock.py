"""Backend timeouts follow Runtime's active clock, including during a long tool wait."""

import json
import sys
import time
from pathlib import Path

import pytest

from backends.process import run_bounded
from session_clock import SESSION_CLOCK_ENV, active_time


def test_clock_is_read_only_and_subtracts_live_and_completed_waits(tmp_path: Path) -> None:
    path = tmp_path / "clock.json"
    env = {SESSION_CLOCK_ENV: str(path)}
    assert active_time(123, environment={}) == 123
    path.write_text(json.dumps({"schema_version": 1, "wait_seconds": 15, "paused_since": 105}))
    before = path.read_bytes()
    assert active_time(110, environment=env) == 90
    assert active_time(500, environment=env) == 90
    assert path.read_bytes() == before
    path.write_text(json.dumps({"schema_version": 1, "wait_seconds": 410, "paused_since": None}))
    assert active_time(510, environment=env) == 100


def test_backend_can_wait_longer_than_active_budget_but_still_limits_agent_work(
    tmp_path: Path,
) -> None:
    path = tmp_path / "clock.json"
    env = {SESSION_CLOCK_ENV: str(path)}
    path.write_text(json.dumps({
        "schema_version": 1, "wait_seconds": 0, "paused_since": time.monotonic(),
    }))
    result = run_bounded(
        [sys.executable, "-c", "import time; time.sleep(1.3); print('finished')"],
        tmp_path, timeout=1, env=env,
    )
    assert not result.timed_out and result.returncode == 0
    path.write_text(json.dumps({"schema_version": 1, "wait_seconds": 0, "paused_since": None}))
    result = run_bounded(
        [sys.executable, "-c", "import time; time.sleep(10)"], tmp_path, timeout=1, env=env,
    )
    assert result.timed_out


@pytest.mark.parametrize("value", [None, {}, {"schema_version": 1, "wait_seconds": -1}])
def test_invalid_bound_clock_is_not_silently_ignored(tmp_path: Path, value: object) -> None:
    path = tmp_path / "clock.json"
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="session clock"):
        active_time(100, environment={SESSION_CLOCK_ENV: str(path)})
