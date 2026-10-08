from __future__ import annotations

import os
import signal
import sys
import threading
import time
from pathlib import Path

import pytest

from backends import process


@pytest.mark.parametrize("closed_stdio", [False, True])
def test_parent_exit_stops_owned_background_writer(tmp_path: Path, closed_stdio: bool) -> None:
    marker = tmp_path / "orphan-write"
    child = (
        "import time; from pathlib import Path; time.sleep(0.8); "
        f"Path({str(marker)!r}).write_text('should never run')"
    )
    redirect = ", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL" if closed_stdio else ""
    parent = (
        "import subprocess,sys; "
        f"subprocess.Popen([sys.executable, '-c', {child!r}]{redirect}); "
        "print('parent failed', flush=True); sys.exit(1)"
    )
    result = process.run_bounded([sys.executable, "-c", parent], tmp_path, 5)
    time.sleep(0.9)
    assert not marker.exists()
    assert result.returncode == 1
    assert result.stdout == "parent failed\n"
    assert not result.output_overflow
    if not (Path("/proc") / "self/stat").exists():
        assert not result.process_scope_complete
        assert "could not be verified quiescent" in " ".join(result.policy_diagnostics)


@pytest.mark.skipif(
    not Path("/proc/self/stat").is_file(), reason="requires Linux process identities"
)
@pytest.mark.parametrize("detached_session", [False, True])
def test_linux_stops_owned_independent_process_group(
    tmp_path: Path, detached_session: bool
) -> None:
    marker = tmp_path / "detached-write"
    child = (
        "import time; from pathlib import Path; time.sleep(1.5); "
        f"Path({str(marker)!r}).write_text('should never run')"
    )
    grouping = "start_new_session=True" if detached_session else "process_group=0"
    parent = (
        "import subprocess,sys,time; "
        f"subprocess.Popen([sys.executable, '-c', {child!r}], "
        f"stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, {grouping}); "
        "time.sleep(0.3); sys.exit(1)"
    )
    result = process.run_bounded([sys.executable, "-c", parent], tmp_path, 5)
    time.sleep(1.6)
    assert not marker.exists()
    assert result.returncode == 1
    assert result.process_scope_complete
    assert result.policy_diagnostics == ()


def _state(
    pid: int, *, parent: int = 1, group: int, session: int, started: str = "1", zombie: bool = False
) -> process._ProcessState:
    return process._ProcessState(pid, parent, group, session, started, zombie)


def test_process_scope_remembers_reparented_detached_child_without_reusing_pid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    states = {
        100: _state(100, group=100, session=100),
        101: _state(101, parent=100, group=101, session=101),
    }
    monkeypatch.setattr(process, "_linux_processes", lambda: dict(states))
    scope = process._ProcessScope(100)
    assert scope.live_groups() == {100, 101}
    states[100] = _state(100, group=100, session=100, zombie=True)
    states[101] = _state(101, parent=1, group=101, session=101)
    assert scope.live_groups() == {101}
    states[101] = _state(101, parent=1, group=101, session=101, started="recycled")
    signals: list[tuple[set[int], signal.Signals]] = []
    monkeypatch.setattr(
        process, "signal_process_groups", lambda groups, sig: signals.append((groups, sig))
    )
    scope.signal(signal.SIGKILL)
    assert signals == [({100}, signal.SIGKILL)]


def test_linux_scope_catches_same_session_group_after_parent_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        process,
        "_linux_processes",
        lambda: {
            100: _state(100, group=100, session=100, zombie=True),
            102: _state(102, parent=1, group=102, session=100),
        },
    )
    assert process._ProcessScope(100).live_groups() == {102}


def test_unfinished_capture_cannot_claim_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_is_alive = threading.Thread.is_alive
    monkeypatch.setattr(
        threading.Thread,
        "is_alive",
        lambda thread: thread.name.startswith("stdout-reader-") or real_is_alive(thread),
    )
    result = process.run_bounded([sys.executable, "-c", "print('done')"], tmp_path, 5)
    assert result.output_overflow
    assert result.returncode == 126
    assert "output readers remained active" in " ".join(result.policy_diagnostics)


def test_proc_stat_keeps_start_time_and_ignores_spaces_in_comm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proc = tmp_path / "proc"
    (proc / "self").mkdir(parents=True)
    (proc / "self/stat").write_text("present")
    (proc / "321").mkdir()
    # Fields after comm: state, ppid, pgrp, session, ... starttime (index 19).
    fields = ["S", "100", "321", "100", *(["0"] * 15), "998877"]
    (proc / "321/stat").write_text("321 (name with ) space) " + " ".join(fields))
    real_path = Path
    monkeypatch.setattr(process, "Path", lambda path: proc if path == "/proc" else real_path(path))
    assert process._linux_processes() == {
        321: _state(321, parent=100, group=321, session=100, started="998877")
    }


def test_observer_exception_cleans_background_children(tmp_path: Path) -> None:
    marker = tmp_path / "exception-orphan-write"
    child = (
        "import time; from pathlib import Path; time.sleep(0.8); "
        f"Path({str(marker)!r}).write_text('should never run')"
    )
    parent = (
        "import subprocess,sys,time; "
        f"subprocess.Popen([sys.executable, '-c', {child!r}], "
        "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); time.sleep(10)"
    )

    class Observer:
        def __init__(self) -> None:
            self.polls = 0

        def on_stdout_line(self, line: str) -> bool:
            return False

        def on_stderr_line(self, line: str) -> bool:
            return False

        def poll(self) -> bool:
            self.polls += 1
            if self.polls == 4:
                raise RuntimeError("observer failed")
            return False

    with pytest.raises(RuntimeError, match="observer failed"):
        process.run_bounded([sys.executable, "-c", parent], tmp_path, 5, observer=Observer())
    time.sleep(0.9)
    assert not marker.exists()


def test_root_is_not_reaped_before_scope_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed_exit: list[bool] = []
    real_stop = process._ProcessScope.stop

    def stop(scope: process._ProcessScope) -> bool:
        waitid = getattr(os, "waitid")
        status = waitid(
            os.P_PID,
            scope.root_pid,
            os.WEXITED | os.WNOHANG | os.WNOWAIT,
        )
        observed_exit.append(status is not None)
        return real_stop(scope)

    monkeypatch.setattr(process._ProcessScope, "stop", stop)
    process.run_bounded([sys.executable, "-c", "pass"], tmp_path, 5)
    assert observed_exit == [True]
