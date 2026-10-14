"""Real process + socket + HTTP tests for the non-bypassable Runtime wait."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import signal
import sys
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from backends.process import run_bounded
from runtime_request_barrier import REQUEST_SOCKET_ENV, supervised_post
from runtime_tools import RuntimeServiceError, _post
from session_clock import SESSION_CLOCK_ENV


class Job:
    def __init__(self, response: dict[str, Any], status: int = 200) -> None:
        self.response = response
        self.status = status
        self.started = threading.Event()
        self.release = threading.Event()


@contextmanager
def server(jobs: dict[str, Job]) -> Iterator[str]:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = self.rfile.read(int(self.headers["Content-Length"]))
            assert self.headers["Authorization"] == "Bearer test-capability"
            job = jobs[json.loads(body)["job"]]
            job.started.set()
            if not job.release.wait(timeout=15):
                self.send_error(500)
                return
            payload = json.dumps(job.response).encode()
            self.send_response(job.status)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            with suppress(OSError):
                self.wfile.write(payload)

        def log_message(self, _format: str, *args: object) -> None:
            pass

    http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{http.server_port}"
    finally:
        for job in jobs.values():
            job.release.set()
        http.shutdown()
        http.server_close()
        thread.join(timeout=5)


def environment(tmp_path: Path) -> dict[str, str]:
    clock = tmp_path / "clock.json"
    clock.write_text(json.dumps({
        "schema_version": 1, "wait_seconds": 0, "paused_since": None,
    }))
    return {
        **os.environ, SESSION_CLOCK_ENV: str(clock),
        "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
    }


def await_file(path: Path) -> str:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if path.exists() and (value := path.read_text()):
            return value
        time.sleep(0.01)
    raise AssertionError(f"missing {path.name}")


@pytest.mark.parametrize("launch", [
    "foreground", "timeout", "background", "client_killed", "shell_timeout", "detached_subagent",
])
def test_agent_and_subagent_cannot_continue_before_http_finishes(
    tmp_path: Path, launch: str,
) -> None:
    if launch == "shell_timeout" and shutil.which("timeout") is None:
        pytest.skip("requires GNU timeout (tested on Linux)")
    if launch == "detached_subagent" and not Path("/proc/self/stat").is_file():
        pytest.skip("requires Linux process-tree ownership")
    job = Job({"status": "completed", "latency_us": 10,
               "data": "x" * (2 * 1024 * 1024) if launch == "foreground" else ""})
    marker, submarker = tmp_path / "agent-ticks", tmp_path / "subagent-ticks"
    cli_pid = tmp_path / "cli-pid"
    child = (
        "import time; from pathlib import Path; "
        f"p=Path({str(submarker)!r}); "
        "exec(\"while True:\\n p.write_text(str(time.monotonic())); time.sleep(.01)\")"
    )
    with server({"test": job}) as url, ThreadPoolExecutor() as pool:
        cli = (
            "import os,json; from pathlib import Path; from runtime_tools import _post; "
            f"Path({str(cli_pid)!r}).write_text(str(os.getpid())); "
            f"print(json.dumps(_post({url!r}, 'test-capability', '/v1/operations', "
            "{'job': 'test'})), flush=True)"
        )
        grouping = ", start_new_session=True" if launch == "detached_subagent" else ""
        tool_command = [sys.executable, "-c", cli]
        if launch == "shell_timeout":
            tool_command = ["/bin/sh", "-c", "timeout 0.3 " + shlex.join(tool_command)]
        parent = (
            "import subprocess,sys,time; from pathlib import Path; "
            f"subprocess.Popen([sys.executable, '-c', {child!r}]{grouping}); "
            f"p=subprocess.Popen({tool_command!r}); "
        )
        if launch in {"foreground", "shell_timeout"}:
            parent += "p.wait(); "
        elif launch == "timeout":
            parent += (
                "exec(\"try:\\n p.wait(timeout=.2)\\nexcept subprocess.TimeoutExpired:\\n "
                "p.kill(); p.wait(); print('EXIT=124', flush=True)\"); "
            )
        parent += (
            f"m=Path({str(marker)!r}); "
            "exec(\"for _ in range(100):\\n "
            "m.write_text(str(time.monotonic())); time.sleep(.01)\"); p.wait()"
        )
        future = pool.submit(run_bounded, [sys.executable, "-c", parent], tmp_path, 10,
                             environment(tmp_path))
        try:
            assert job.started.wait(timeout=5)
            if launch == "client_killed":
                os.kill(int(await_file(cli_pid)), signal.SIGKILL)
            before = marker.read_bytes() if marker.exists() else None
            sub_before = submarker.read_bytes() if submarker.exists() else None
            time.sleep(0.4)  # More than the shell's timeout; the HTTP Job is still running.
            assert not future.done()
            assert (marker.read_bytes() if marker.exists() else None) == before
            assert (submarker.read_bytes() if submarker.exists() else None) == sub_before
        finally:
            job.release.set()
        result = future.result(timeout=10)
        assert result.returncode == 0 and not result.timed_out
        assert marker.exists()  # The Provider resumed only after the terminal response.
        if launch in {"foreground", "background"}:
            assert '"latency_us": 10' in result.stdout
        if launch == "foreground":
            assert job.response["data"] in result.stdout


def test_overlapping_requests_wait_for_the_last_result_and_preserve_errors(tmp_path: Path) -> None:
    first = Job({"status": "completed", "data": "x" * (2 * 1024 * 1024)})
    second = Job({"error": "source_archive_invalid", "detail": "kernel.py: forbidden import"}, 422)
    socket_file, marker = tmp_path / "socket-path", tmp_path / "ticks"
    parent = (
        "import os,time; from pathlib import Path; "
        f"Path({str(socket_file)!r}).write_text(os.environ[{REQUEST_SOCKET_ENV!r}]); "
        f"m=Path({str(marker)!r}); "
        "exec(\"for _ in range(200):\\n "
        "m.write_text(str(time.monotonic())); time.sleep(.01)\")"
    )
    with server({"first": first, "second": second}) as url, ThreadPoolExecutor() as pool:
        agent = pool.submit(run_bounded, [sys.executable, "-c", parent], tmp_path, 10,
                            environment(tmp_path))
        socket_path = await_file(socket_file)
        calls = [pool.submit(supervised_post, socket_path, url, "test-capability",
                             "/v1/operations", {"job": name}) for name in ("first", "second")]
        try:
            assert first.started.wait(timeout=5) and second.started.wait(timeout=5)
            before = marker.read_bytes()
            first.release.set()
            assert len(calls[0].result(timeout=5)["data"]) == 2 * 1024 * 1024
            time.sleep(0.2)
            assert marker.read_bytes() == before and not agent.done()
            second.release.set()
            with pytest.raises(RuntimeServiceError) as caught:
                calls[1].result(timeout=5)
            assert caught.value.status_code == 422
            assert caught.value.payload == second.response
        finally:
            first.release.set()
            second.release.set()
        assert agent.result(timeout=10).returncode == 0


def test_shutdown_while_waiting_terminates_agent_without_resuming(tmp_path: Path) -> None:
    job = Job({"status": "completed"})
    stop = threading.Event()

    class Observer:
        def on_stdout_line(self, _line: str) -> bool:
            return False

        def on_stderr_line(self, _line: str) -> bool:
            return False

        def poll(self) -> bool:
            return stop.is_set()

    marker = tmp_path / "must-not-resume"
    with server({"test": job}) as url, ThreadPoolExecutor() as pool:
        cli = (
            "from runtime_tools import _post; "
            f"_post({url!r}, 'test-capability', '/v1/operations', {{'job': 'test'}})"
        )
        parent = (
            "import subprocess,sys; from pathlib import Path; "
            f"subprocess.run([sys.executable, '-c', {cli!r}]); "
            f"Path({str(marker)!r}).touch()"
        )
        future = pool.submit(run_bounded, [sys.executable, "-c", parent], tmp_path, 10,
                             environment(tmp_path), Observer())
        assert job.started.wait(timeout=5)
        stop.set()
        result = future.result(timeout=5)
        assert result.returncode != 0 and not marker.exists()


def test_bound_cli_fails_closed_without_a_live_supervisor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(SESSION_CLOCK_ENV, str(tmp_path / "clock.json"))
    monkeypatch.delenv(REQUEST_SOCKET_ENV, raising=False)
    with pytest.raises(RuntimeError, match="requires its live request supervisor"):
        _post("http://127.0.0.1:1", "test-capability", "/v1/operations", {})
    monkeypatch.setenv(REQUEST_SOCKET_ENV, str(tmp_path / "missing.sock"))
    with pytest.raises(OSError):
        _post("http://127.0.0.1:1", "test-capability", "/v1/operations", {})


def test_http_return_does_not_resume_while_runtime_still_owns_a_request(tmp_path: Path) -> None:
    job = Job({"error": "transport_interrupted"}, 503)
    marker = tmp_path / "resumed"
    env = environment(tmp_path)
    clock_path = Path(env[SESSION_CLOCK_ENV])

    def set_clock(paused: float | None) -> None:
        temporary = clock_path.with_suffix(".new")
        temporary.write_text(json.dumps({
            "schema_version": 1, "wait_seconds": 0, "paused_since": paused,
        }))
        temporary.replace(clock_path)

    with server({"test": job}) as url, ThreadPoolExecutor() as pool:
        cli = (
            "from runtime_tools import _post; "
            f"_post({url!r}, 'test-capability', '/v1/operations', {{'job': 'test'}})"
        )
        parent = (
            "import subprocess,sys; from pathlib import Path; "
            f"subprocess.run([sys.executable, '-c', {cli!r}]); "
            f"Path({str(marker)!r}).touch()"
        )
        future = pool.submit(run_bounded, [sys.executable, "-c", parent], tmp_path, 10, env)
        try:
            assert job.started.wait(timeout=5)
            set_clock(time.monotonic())
            job.release.set()
            time.sleep(0.4)
            assert not marker.exists() and not future.done()
        finally:
            job.release.set()
            set_clock(None)
        assert future.result(timeout=5).returncode == 0 and marker.exists()


def test_script_entrypoint_preserves_structured_http_errors(tmp_path: Path) -> None:
    job = Job({"error": "source_archive_invalid", "detail": "kernel.py: forbidden sys import"}, 422)
    job.release.set()
    script = Path(__file__).resolve().parents[1] / "src/runtime_tools.py"
    with server({"test": job}) as url:
        # run_path reproduces the distinct module identity of runtime_tools.py
        # executed as a script, rather than importing the CLI in the test process.
        cli = (
            "import runpy,sys; from types import SimpleNamespace; "
            f"entry=runpy.run_path({str(script)!r}); scope=entry['main'].__globals__; "
            f"context=SimpleNamespace(gateway_url={url!r}, "
            "gateway_capability='test-capability', attempt_id='attempt_test'); "
            "scope['_context']=lambda _:context; "
            "scope['_request_object']=lambda *_:{'job':'test'}; "
            "sys.exit(entry['main'](['kernel-pareto-frontier', '--request', 'unused.json']))"
        )
        parent = (
            "import subprocess,sys; "
            f"p=subprocess.run([sys.executable, '-c', {cli!r}]); "
            "sys.exit(0 if p.returncode == 2 else 1)"
        )
        result = run_bounded([sys.executable, "-c", parent], tmp_path, 10, environment(tmp_path))
    assert result.returncode == 0
    assert json.loads(result.stdout) == {
        **job.response, "status": "error", "http_status": 422, "command": "kernel-pareto-frontier",
    }
    assert "Traceback" not in result.stderr
