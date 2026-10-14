"""Own Runtime HTTP calls outside the Provider's stoppable process tree.

The CLI hands off a complete request over a private socket. Only after the
supervisor has stopped the Provider (including its tools and subagents) may the
HTTP worker start. Killing/backgrounding/timing out the CLI cannot release this
barrier. HTTP workers belong to the supervisor and are killed on Session exit.
"""

from __future__ import annotations

import contextlib
import json
import signal
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
from collections.abc import Callable
from io import BufferedIOBase
from pathlib import Path
from typing import Any, BinaryIO

REQUEST_SOCKET_ENV = "ATREX_RUNTIME_REQUEST_SOCKET"
# Includes base64 for a 64 MiB candidate tree plus the ordinary request fields.
_MAX_FRAME_BYTES = 128 * 1024 * 1024


class RuntimeServiceError(RuntimeError):
    """One error type shared by imported clients and the runtime_tools.py script."""

    def __init__(self, status_code: int, payload: dict[str, Any]) -> None:
        self.status_code = status_code
        self.payload = payload
        super().__init__(str(payload.get("detail", payload.get("error", "Runtime service error"))))


def _read_frame(stream: BinaryIO | BufferedIOBase) -> dict[str, Any]:
    header = stream.read(8)
    if len(header) != 8:
        raise RuntimeError("Runtime request supervisor disconnected")
    size = int.from_bytes(header, "big")
    if size > _MAX_FRAME_BYTES:
        raise ValueError("Runtime request supervisor message exceeds its byte limit")
    body = stream.read(size)
    if len(body) != size:
        raise RuntimeError("Runtime request supervisor returned an incomplete message")
    value = json.loads(body)
    if not isinstance(value, dict):
        raise ValueError("Invalid Runtime request supervisor message")
    return value


def _frame(value: dict[str, Any]) -> bytes:
    body = json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
    if len(body) > _MAX_FRAME_BYTES:
        raise ValueError("Runtime request supervisor message exceeds its byte limit")
    return len(body).to_bytes(8, "big") + body


def supervised_post(
    socket_path: str, url: str, capability: str, path: str, value: object,
) -> dict[str, Any]:
    """No socket timeout or direct-HTTP fallback may bypass the supervisor."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.connect(socket_path)
        connection.sendall(_frame({
            "url": url, "capability": capability, "path": path, "value": value,
        }))
        with connection.makefile("rb") as stream:
            reply = _read_frame(stream)
    if "service_error" in reply:
        error = reply["service_error"]
        raise RuntimeServiceError(error["status_code"], error["payload"])
    if "error" in reply:
        raise RuntimeError(reply["error"])
    result = reply.get("result")
    if not isinstance(result, dict):
        raise RuntimeError("Runtime request supervisor returned no result")
    return result


class _Server(socketserver.ThreadingUnixStreamServer):
    # close() first kills HTTP workers and shuts down sockets, then joins all
    # handlers. No worker or request thread survives the owning Session.
    daemon_threads = False
    block_on_close = True


class RuntimeRequestBarrier:
    """One barrier per Provider launch; overlapping requests share one pause."""

    def __init__(self) -> None:
        # macOS AF_UNIX paths are limited to 104 bytes; avoid long TMPDIR paths.
        self._directory = tempfile.TemporaryDirectory(prefix="atrex-wait-", dir="/tmp")
        self.path = str(Path(self._directory.name) / "requests.sock")
        self._lock = threading.RLock()
        self._paused = threading.Event()
        self._pending = 0
        self._closed = False
        self._workers: set[subprocess.Popen[bytes]] = set()
        self._connections: set[socket.socket] = set()
        barrier = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self) -> None:
                with barrier._lock:
                    if barrier._closed:
                        return
                    barrier._connections.add(self.request)
                try:
                    # Partial/abandoned uploads have not submitted a Runtime call.
                    self.request.settimeout(60)
                    request = _read_frame(self.rfile)
                    self.request.settimeout(None)
                    reply = barrier._execute(request)
                    # Resume before writing: the stopped CLI cannot drain a large
                    # response, and may already have been killed by its shell.
                    self.wfile.write(reply)
                except (OSError, ValueError, RuntimeError):
                    return
                finally:
                    with barrier._lock:
                        barrier._connections.discard(self.request)

        try:
            self._server = _Server(self.path, Handler)
        except BaseException:
            self._directory.cleanup()
            raise
        self._thread = threading.Thread(
            target=lambda: self._server.serve_forever(poll_interval=0.05),
            name="runtime-request-supervisor", daemon=True,
        )
        self._thread.start()

    def _execute(self, request: dict[str, Any]) -> bytes:
        with self._lock:
            if self._closed:
                raise RuntimeError("Runtime request supervisor is closed")
            self._pending += 1
        worker: subprocess.Popen[bytes] | None = None
        try:
            # poll() acknowledges SIGSTOP before we can send anything to Runtime.
            self._paused.wait()
            with self._lock:
                if self._closed:
                    raise RuntimeError("Runtime request supervisor is closed")
                worker = subprocess.Popen(
                    [sys.executable, str(Path(__file__).resolve())],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    # Stay in the supervisor's group so an outer Session kill
                    # also kills us; the Provider owns a separate session/group.
                    stderr=subprocess.DEVNULL,
                )
                self._workers.add(worker)
            output, _ = worker.communicate(_frame(request))
            if worker.returncode != 0 or not output:
                return _frame({"error": "Runtime HTTP worker failed before returning a result"})
            return output
        finally:
            with self._lock:
                if worker is not None:
                    self._workers.discard(worker)
                self._pending -= 1

    def poll(self, send_signal: Callable[[signal.Signals], None], *, runtime_waiting: bool) -> bool:
        """Stop the whole owned scope, never the independent HTTP worker."""
        with self._lock:
            waiting = self._pending > 0 or runtime_waiting
            if waiting:
                # Repeat for any child that was forking when SIGSTOP arrived.
                send_signal(signal.SIGSTOP)
                self._paused.set()
            elif self._paused.is_set():
                send_signal(signal.SIGCONT)
                self._paused.clear()
            return waiting

    def close(self) -> None:
        """Shutdown never resumes an Agent; its caller first terminates that scope."""
        with self._lock:
            self._closed = True
            self._paused.set()
            workers = tuple(self._workers)
            connections = tuple(self._connections)
            for worker in workers:
                # communicate() in the handler is the sole process reaper.
                with contextlib.suppress(ProcessLookupError):
                    worker.kill()
            for connection in connections:
                with contextlib.suppress(OSError):
                    connection.shutdown(socket.SHUT_RDWR)
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)
        self._directory.cleanup()


def _http_worker() -> None:
    from runtime_tools import RuntimeServiceError, _post_direct

    reply: dict[str, Any]
    try:
        request = _read_frame(sys.stdin.buffer)
        result = _post_direct(
            request["url"], request["capability"], request["path"], request["value"],
        )
        reply = {"result": result}
    except RuntimeServiceError as error:
        reply = {"service_error": {"status_code": error.status_code, "payload": error.payload}}
    except Exception as error:
        reply = {"error": str(error)}
    sys.stdout.buffer.write(_frame(reply))


if __name__ == "__main__":
    _http_worker()
