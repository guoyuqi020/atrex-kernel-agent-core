from __future__ import annotations

import ast
import contextlib
import os
import re
import shlex
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

DEPENDENCY_GUARD_POLL_SECONDS = 0.25
OUTPUT_READ_CHUNK_CHARS = 64 * 1024
MAX_CAPTURE_CHARS = 8 * 1024 * 1024


@dataclass(frozen=True)
class ProcessResult:
    """Raw bounded Provider streams plus separate trusted process diagnostics."""

    stdout: str
    stderr: str
    returncode: int
    timed_out: bool
    output_overflow: bool
    policy_diagnostics: tuple[str, ...]


class ProcessObserver(Protocol):
    """Observe a live process without receiving authority over its environment."""

    def on_stdout_line(self, line: str) -> bool:
        """Return true when the complete process group must be terminated."""
        ...

    def poll(self) -> bool:
        """Check out-of-band state and return true when execution must stop."""
        ...


class ProcessRunner(Protocol):
    def __call__(
        self,
        command: list[str],
        cwd: Path,
        timeout: int | None,
        env: dict[str, str] | None = None,
        observer: ProcessObserver | None = None,
    ) -> ProcessResult: ...


def python_import_roots(code: str, *, _depth: int = 0) -> set[str]:
    """Return real imported top-level modules without matching strings/comments."""
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError, TypeError):
        return set()

    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".", 1)[0])
        elif isinstance(node, ast.Call) and node.args:
            target: str | None = None
            if (isinstance(node.func, ast.Name) and node.func.id == "__import__") or (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "import_module"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "importlib"
            ):
                target = "import"
            if target and isinstance(node.args[0], ast.Constant):
                module = node.args[0].value
                if isinstance(module, str) and module:
                    roots.add(module.split(".", 1)[0])
            if (
                _depth < 2
                and isinstance(node.func, ast.Name)
                and node.func.id in {"exec", "eval"}
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                roots.update(python_import_roots(node.args[0].value, _depth=_depth + 1))
    return roots


def dependency_process_violation(argv: list[str]) -> str | None:
    """Describe a forbidden dependency build or host GPU action, if any."""
    if not argv:
        return None

    def unwrap(segment: list[str]) -> list[str]:
        result = list(segment)
        while result and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", result[0]):
            result.pop(0)
        if result and Path(result[0]).name.lower() in {"env", "command"}:
            result.pop(0)
            while result and (result[0].startswith("-") or "=" in result[0]):
                result.pop(0)
        if result and Path(result[0]).name.lower() == "timeout":
            result.pop(0)
            while result and result[0].startswith("-"):
                result.pop(0)
            if result:
                result.pop(0)
        return result

    def command_segments(process_argv: list[str]) -> list[list[str]]:
        tokens = process_argv
        executable = Path(process_argv[0]).name.lower()
        if executable in {"bash", "sh", "dash", "zsh", "ksh"}:
            command_index = next(
                (
                    index + 1
                    for index, value in enumerate(process_argv[:-1])
                    if value.startswith("-") and "c" in value[1:]
                ),
                -1,
            )
            if command_index >= 0:
                try:
                    lexer = shlex.shlex(
                        process_argv[command_index], posix=True, punctuation_chars=";&|"
                    )
                    lexer.whitespace_split = True
                    tokens = list(lexer)
                except ValueError:
                    tokens = process_argv
        segments: list[list[str]] = []
        current: list[str] = []
        for token in tokens:
            if token and all(character in ";&|" for character in token):
                if current:
                    segments.append(current)
                    current = []
            else:
                current.append(token)
        if current:
            segments.append(current)
        expanded = list(segments)
        for segment in segments:
            unwrapped = unwrap(segment)
            if unwrapped and Path(unwrapped[0]).name.lower() == "eval" and len(unwrapped) > 1:
                expanded.extend(command_segments(["sh", "-c", " ".join(unwrapped[1:])]))
        return expanded

    def is_installer(segment: list[str]) -> bool:
        tokens = unwrap(segment)
        if not tokens:
            return False
        lowered = [token.lower() for token in tokens]
        executable = Path(lowered[0]).name
        if re.fullmatch(r"pip[0-9.]*", executable):
            return len(lowered) > 1 and lowered[1] in {"install", "wheel"}
        if executable == "uv":
            return lowered[1:3] in (
                ["pip", "install"],
                ["pip", "sync"],
                ["pip", "compile"],
            )
        if executable in {"conda", "mamba", "micromamba"}:
            return len(lowered) > 1 and lowered[1] in {"install", "create"}
        if re.fullmatch(r"python[0-9.]*", executable):
            if len(lowered) > 3 and lowered[1:3] == ["-m", "pip"]:
                return lowered[3] in {"install", "wheel"}
            if len(lowered) > 2 and lowered[1:3] == ["-m", "build"]:
                return True
            for index, token in enumerate(lowered[:-1]):
                if Path(token).name == "setup.py" and lowered[index + 1] in {
                    "install",
                    "build",
                    "build_ext",
                    "bdist_wheel",
                }:
                    return True
            if "--" in lowered:
                boundary = lowered.index("--")
                return is_installer(tokens[boundary + 1 :])
        if Path(executable).name == "setup.py":
            return len(lowered) > 1 and lowered[1] in {
                "install",
                "build",
                "build_ext",
                "bdist_wheel",
            }
        return False

    segments = command_segments(argv)

    if any(is_installer(segment) for segment in segments):
        return "third-party package installation/build command"

    def direct_host_gpu_action(segment: list[str]) -> str | None:
        tokens = unwrap(segment)
        if not tokens:
            return None
        lowered = [token.lower() for token in tokens]
        executable = Path(lowered[0]).name
        info_only = any(token in {"--help", "-h", "--version"} for token in lowered[1:]) or (
            executable == "nvcc" and "-V" in tokens[1:]
        )
        if executable in {"nvcc", "cicc", "ptxas", "fatbinary", "ninja"} and not info_only:
            return "CUDA/JIT build tool executed directly on the host"
        if executable in {"ncu", "rocprof", "rocprofv3", "compute-sanitizer"}:
            return "GPU profiler executed directly on the host"
        if re.fullmatch(r"python[0-9.]*", executable):
            if len(tokens) > 1 and Path(tokens[1]).name in {
                "kernel.py",
                "test_kernel.py",
                "profile_driver.py",
            }:
                return "kernel/evaluator executed directly on the host"
            if "-c" in tokens:
                code_index = tokens.index("-c") + 1
                code = tokens[code_index] if code_index < len(tokens) else ""
                imports = python_import_roots(code)
                if "kernel" in imports:
                    return "kernel imported directly on the host"
                if imports & {"flashinfer", "flash_attn", "xformers", "vllm"}:
                    return "JIT-capable third-party GPU package imported directly on the host"
        if executable in {"bash", "sh", "dash", "zsh", "ksh"} and any(
            Path(token).name in {"profile_nvidia.sh", "profile_kernel.sh"} for token in tokens[1:]
        ):
            return "GPU profiler wrapper executed directly on the host"
        return None

    for segment in segments:
        reason = direct_host_gpu_action(segment)
        if reason is not None:
            return reason

    command = " ".join(argv).lower()
    package_build_tree = re.search(
        r"(?:^|[\s=])[^\s]*(?:pip-install-|pip-build-|pip-modern-metadata-)[^\s]*",
        command,
    )
    build_tools = {
        "cicc",
        "nvcc",
        "ninja",
        "cmake",
        "make",
        "gcc",
        "g++",
        "clang",
        "clang++",
    }
    if package_build_tree and any(
        unwrap(segment) and Path(unwrap(segment)[0]).name.lower() in build_tools
        for segment in segments
    ):
        return "compiler/build tool running in a package-manager temporary tree"
    return None


def descendant_process_commands(root_pid: int) -> list[tuple[int, list[str]]]:
    """Return live descendants and argv using Linux procfs, tolerating races."""
    pending = [root_pid]
    seen = {root_pid}
    descendants: list[tuple[int, list[str]]] = []
    while pending:
        parent = pending.pop()
        task_dir = Path(f"/proc/{parent}/task")
        try:
            thread_dirs = list(task_dir.iterdir())
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        children: set[int] = set()
        for thread_dir in thread_dirs:
            try:
                children.update(
                    int(value) for value in (thread_dir / "children").read_text().split()
                )
            except (FileNotFoundError, PermissionError, ProcessLookupError, ValueError):
                continue
        for pid in children:
            if pid in seen:
                continue
            seen.add(pid)
            pending.append(pid)
            try:
                raw = Path(f"/proc/{pid}/cmdline").read_bytes()
            except (FileNotFoundError, PermissionError, ProcessLookupError):
                continue
            argv = [part.decode(errors="replace") for part in raw.split(b"\0") if part]
            descendants.append((pid, argv))
    return descendants


def descendant_process_groups(root_pid: int) -> set[int]:
    """Capture every process group in a coding session's live process tree."""
    process_groups: set[int] = set()
    for pid in [root_pid, *[pid for pid, _argv in descendant_process_commands(root_pid)]]:
        with contextlib.suppress(ProcessLookupError):
            process_groups.add(os.getpgid(pid))
    return process_groups


def signal_process_groups(process_groups: set[int], sig: signal.Signals) -> None:
    for process_group in process_groups:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process_group, sig)


def dependency_guard(
    proc: subprocess.Popen[str], stop: threading.Event, violations: list[str]
) -> None:
    """Kill a coding session as soon as it starts a forbidden dependency job."""
    while not stop.wait(DEPENDENCY_GUARD_POLL_SECONDS):
        if proc.poll() is not None:
            return
        for pid, argv in descendant_process_commands(proc.pid):
            reason = dependency_process_violation(argv)
            if reason is None:
                continue
            rendered = " ".join(argv)
            violations.append(f"pid={pid}: {reason}: {rendered[:1000]}")
            process_groups = descendant_process_groups(proc.pid)
            signal_process_groups(process_groups, signal.SIGTERM)
            deadline = time.monotonic() + 1.0
            while proc.poll() is None and time.monotonic() < deadline:
                if stop.wait(0.05):
                    return
            signal_process_groups(process_groups, signal.SIGKILL)
            return


def run_bounded(
    command: list[str],
    cwd: Path,
    timeout: int | None,
    env: dict[str, str] | None = None,
    observer: ProcessObserver | None = None,
) -> ProcessResult:
    """Run a guarded command with live output observation and a wall deadline."""
    proc = subprocess.Popen(
        command,
        cwd=str(cwd),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
        env=env,
    )
    guard_stop = threading.Event()
    dependency_violations: list[str] = []
    guard = threading.Thread(
        target=dependency_guard,
        args=(proc, guard_stop, dependency_violations),
        name=f"dependency-guard-{proc.pid}",
        daemon=True,
    )
    guard.start()
    timed_out = False
    observation_stop = threading.Event()
    output_limit_exceeded = threading.Event()
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    reader_errors: list[BaseException] = []

    def read_stdout() -> None:
        captured = 0
        pending = ""
        try:
            if proc.stdout is None:
                return
            while chunk := proc.stdout.readline(OUTPUT_READ_CHUNK_CHARS):
                remaining = MAX_CAPTURE_CHARS - captured
                if remaining <= 0:
                    output_limit_exceeded.set()
                    observation_stop.set()
                    return
                accepted = chunk[:remaining]
                stdout_parts.append(accepted)
                captured += len(accepted)
                pending += accepted
                while "\n" in pending:
                    line, pending = pending.split("\n", 1)
                    if observer is not None and observer.on_stdout_line(line + "\n"):
                        observation_stop.set()
                if len(chunk) > remaining:
                    output_limit_exceeded.set()
                    observation_stop.set()
                    return
            if pending and observer is not None and observer.on_stdout_line(pending):
                observation_stop.set()
        except BaseException as error:
            reader_errors.append(error)

    def read_stderr() -> None:
        captured = 0
        try:
            if proc.stderr is None:
                return
            while chunk := proc.stderr.readline(OUTPUT_READ_CHUNK_CHARS):
                remaining = MAX_CAPTURE_CHARS - captured
                if remaining <= 0:
                    output_limit_exceeded.set()
                    observation_stop.set()
                    return
                stderr_parts.append(chunk[:remaining])
                captured += min(len(chunk), remaining)
                if len(chunk) > remaining:
                    output_limit_exceeded.set()
                    observation_stop.set()
                    return
        except BaseException as error:
            reader_errors.append(error)

    stdout_reader = threading.Thread(
        target=read_stdout,
        name=f"stdout-reader-{proc.pid}",
        daemon=True,
    )
    stderr_reader = threading.Thread(
        target=read_stderr,
        name=f"stderr-reader-{proc.pid}",
        daemon=True,
    )
    stdout_reader.start()
    stderr_reader.start()
    deadline = None if timeout is None else time.monotonic() + timeout
    try:
        while proc.poll() is None:
            if observer is not None and observer.poll():
                observation_stop.set()
            if observation_stop.is_set():
                signal_process_groups(descendant_process_groups(proc.pid), signal.SIGKILL)
                break
            if deadline is not None and time.monotonic() >= deadline:
                timed_out = True
                signal_process_groups(descendant_process_groups(proc.pid), signal.SIGKILL)
                break
            time.sleep(0.05)
        proc.wait()
    except BaseException:
        process_groups = descendant_process_groups(proc.pid)
        signal_process_groups(process_groups, signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            signal_process_groups(process_groups, signal.SIGKILL)
            proc.wait()
        raise
    finally:
        guard_stop.set()
        guard.join(timeout=1)
        stdout_reader.join(timeout=5)
        stderr_reader.join(timeout=5)
    if reader_errors:
        raise RuntimeError(
            f"failed to read Agent process output: {type(reader_errors[0]).__name__}"
        ) from reader_errors[0]
    stdout = "".join(stdout_parts)
    stderr = "".join(stderr_parts)
    returncode = proc.returncode
    policy_diagnostics: list[str] = []
    if output_limit_exceeded.is_set():
        policy_diagnostics.append("Agent output exceeded the bounded capture limit")
        if returncode == 0:
            returncode = 126
    if dependency_violations:
        policy_diagnostics.append(
            "dependency policy violation; terminated coding session:\n"
            + "\n".join(dependency_violations)
        )
        if returncode == 0:
            returncode = 126
    return ProcessResult(
        stdout=stdout or "",
        stderr=stderr or "",
        returncode=returncode,
        timed_out=timed_out,
        output_overflow=output_limit_exceeded.is_set(),
        policy_diagnostics=tuple(policy_diagnostics),
    )
