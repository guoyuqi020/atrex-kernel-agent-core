from __future__ import annotations

import contextlib
import json
import os
import sys
import threading
import uuid
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import TextIO

from .adapter import (
    DEFAULT_BACKEND_REGISTRY,
    AgentBackendAdapter,
    BackendAdapterRegistry,
    ClaudeAdapter,
    CodexAdapter,
    PiAdapter,
    QoderAdapter,
    credit_usage_from_mapping,
    token_usage_from_mapping,
    token_usage_from_model_usage,
)
from .codex_ledger import (
    CodexLedgerError,
    CodexSessionLedgerObserver,
    CodexTemporaryHome,
    codex_home,
    codex_thread_id_from_stream,
    observe_codex_usage,
)
from .model import (
    AgentRunRequest,
    AgentRunResult,
    AgentRuntime,
    RawSessionFile,
    TokenUsage,
    sum_token_usages,
)
from .process import ProcessObserver, ProcessRunner, run_bounded

REASONING_EFFORTS = frozenset({"low", "medium", "high", "max"})
MAX_CODEX_ROLLOUT_CAPTURE_BYTES = 16 * 1024 * 1024


class TokenBudgetObserver(ProcessObserver):
    """Stop a live coding-Agent process after its provider-native usage reaches quota."""

    _TRANSIENT_CODEX_ERRORS = (
        "session ledger not found",
        "has no new events",
        "exposed no new token_count delta",
    )

    def __init__(
        self,
        adapter: AgentBackendAdapter,
        budget: float,
        *,
        codex_home_path: Path | None = None,
    ) -> None:
        if budget <= 0:
            raise ValueError("Agent provider-usage budget must be positive")
        self._adapter = adapter
        self._budget = budget
        self._used = 0.0
        self._exhausted = False
        self._monitoring_failed = adapter.id == "codex" and codex_home_path is None
        self._seen_message_ids: set[str] = set()
        self._codex_thread_id = ""
        self._codex = (
            CodexSessionLedgerObserver(codex_home_path)
            if adapter.id == "codex" and codex_home_path is not None
            else None
        )
        self._lock = threading.Lock()

    @property
    def exhausted(self) -> bool:
        with self._lock:
            return self._exhausted

    @property
    def monitoring_failed(self) -> bool:
        with self._lock:
            return self._monitoring_failed

    def _add(self, amount: float | None) -> bool:
        if amount is None or amount < 0:
            return self._exhausted
        self._used += amount
        if self._used >= self._budget:
            self._exhausted = True
        return self._exhausted

    def on_stdout_line(self, line: str) -> bool:
        if self._codex is not None:
            thread_id = codex_thread_id_from_stream(line)
            if thread_id:
                with self._lock:
                    self._codex_thread_id = thread_id
            return self.exhausted
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            return self.exhausted
        if not isinstance(raw, Mapping):
            return self.exhausted
        message = raw.get("message")
        usage = raw.get("usage")
        if usage is None and isinstance(message, Mapping):
            usage = message.get("usage")
        message_id = (
            message.get("id")
            if isinstance(message, Mapping) and isinstance(message.get("id"), str)
            else None
        )
        with self._lock:
            if message_id is not None and message_id in self._seen_message_ids:
                return self._exhausted
            if (
                self._adapter.id == "qodercli"
                and isinstance(message, Mapping)
                and isinstance(usage, Mapping)
                and credit_usage_from_mapping(usage).credits is None
            ):
                self._monitoring_failed = True
                return True
            try:
                events, _terminal = self._adapter.normalize_stream(line)
            except Exception:
                return self._exhausted
            deltas: list[float | None] = [
                event.usage.credits if self._adapter.id == "qodercli" else event.usage.total_tokens
                for event in events
                if event.kind == "usage_delta" and event.usage is not None
            ]
            if deltas and message_id is not None:
                self._seen_message_ids.add(message_id)
            return any(self._add(amount) for amount in deltas)

    def on_stderr_line(self, line: str) -> bool:
        del line
        return self.exhausted

    def poll(self) -> bool:
        if self._codex is None:
            return self.exhausted
        with self._lock:
            if self._exhausted or self._monitoring_failed:
                return True
            thread_id = self._codex_thread_id
            if not thread_id:
                return False
            try:
                observation = self._codex.observe(thread_id)
            except CodexLedgerError as error:
                if any(text in str(error) for text in self._TRANSIENT_CODEX_ERRORS):
                    return False
                self._monitoring_failed = True
                return True
            except Exception:
                self._monitoring_failed = True
                return True
            for event in observation.events:
                if event.kind == "usage_delta" and event.usage is not None:
                    self._add(event.usage.total_tokens)
            return self._exhausted


class _LiveSessionTraceObserver(ProcessObserver):
    """Best-effort projection of live Provider streams into the fixed Session workspace."""

    def __init__(self, root: Path) -> None:
        self._codex: CodexSessionLedgerObserver | None = None
        self._codex_thread_id = ""
        self._lock = threading.Lock()
        self._stdout = (root / "provider/stdout.stream-json").open("a", encoding="utf-8")
        self._stderr = (root / "provider/stderr.log").open("a", encoding="utf-8")
        raw_codex = root / "provider/codex-rollout.raw-jsonl"
        self._raw_codex = raw_codex.open("r+b") if raw_codex.is_file() else None

    def attach_codex(self, observer: CodexSessionLedgerObserver) -> None:
        self._codex = observer

    @staticmethod
    def _append(output: TextIO, value: str) -> None:
        try:
            output.write(value)
            output.flush()
        except (OSError, ValueError):
            return

    def on_stdout_line(self, line: str) -> bool:
        with self._lock:
            self._append(self._stdout, line)
            thread_id = codex_thread_id_from_stream(line)
            if thread_id:
                self._codex_thread_id = thread_id
        return False

    def on_stderr_line(self, line: str) -> bool:
        with self._lock:
            self._append(self._stderr, line)
        return False

    def poll(self) -> bool:
        observer = self._codex
        with self._lock:
            thread_id = self._codex_thread_id
        if observer is None or not thread_id:
            return False
        try:
            payload = observer.capture_raw_rollout(
                thread_id,
                max_bytes=MAX_CODEX_ROLLOUT_CAPTURE_BYTES,
            )
            output = self._raw_codex
            if output is None:
                return False
            with self._lock:
                output.seek(0)
                output.write(payload)
                output.truncate()
                output.flush()
        except (CodexLedgerError, OSError):
            pass
        return False

    def close(self) -> None:
        with self._lock:
            self._stdout.close()
            self._stderr.close()
            if self._raw_codex is not None:
                self._raw_codex.close()


class _CombinedProcessObserver(ProcessObserver):
    def __init__(self, *observers: ProcessObserver) -> None:
        self._observers = observers

    def on_stdout_line(self, line: str) -> bool:
        return any(observer.on_stdout_line(line) for observer in self._observers)

    def on_stderr_line(self, line: str) -> bool:
        return any(observer.on_stderr_line(line) for observer in self._observers)

    def poll(self) -> bool:
        return any(observer.poll() for observer in self._observers)


def terminal_usage_from_stream(stdout: str) -> TokenUsage:
    """Parse the existing cross-backend terminal contract without event attribution."""
    terminal = TokenUsage.unavailable()
    deltas: list[TokenUsage] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, Mapping):
            continue
        if event.get("type") in {"result", "turn.completed"}:
            parsed = token_usage_from_mapping(event.get("usage"))
            if parsed.total_tokens is None:
                parsed = token_usage_from_model_usage(event.get("modelUsage"))
            if parsed.total_tokens is not None:
                terminal = parsed
            continue
        usage = event.get("usage")
        message = event.get("message")
        if usage is None and isinstance(message, Mapping):
            usage = message.get("usage")
        parsed = token_usage_from_mapping(usage)
        if parsed.total_tokens is not None:
            deltas.append(parsed)
    if terminal.total_tokens is not None:
        return terminal
    fallback = sum_token_usages(deltas)
    return (
        replace(fallback, measurement="partial") if fallback.total_tokens is not None else fallback
    )


def token_usage_from_stream(stdout: str) -> int:
    """Preserve the terminal-token compatibility contract for legacy callers."""
    return terminal_usage_from_stream(stdout).total_tokens or 0


def build_session_environment(runtime_id: str) -> dict[str, str]:
    """Build the explicit environment for one coding-agent session."""
    environment = os.environ.copy()
    # Private Atrex-Bench evaluator inputs are campaign-scoped and must be reintroduced only by
    # the owning campaign, never inherited accidentally by an unrelated or legacy session.
    environment.pop("ATREX_PRIVATE_REFERENCE_DIR", None)
    python_bin = str(Path(sys.executable).resolve().parent)
    path_parts = [
        part
        for part in environment.get("PATH", "").split(os.pathsep)
        if part and part != python_bin
    ]
    environment["PATH"] = os.pathsep.join([python_bin, *path_parts])
    environment["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    if runtime_id == "pi":
        environment["PI_SKIP_VERSION_CHECK"] = "1"
        environment["PI_TELEMETRY"] = "0"
    if runtime_id == "claude" and environment.get("ANTHROPIC_AUTH_TOKEN"):
        environment.pop("ANTHROPIC_API_KEY", None)
    return environment


class CliAgentRuntime:
    def __init__(
        self,
        adapter: AgentBackendAdapter,
        *,
        process_runner: ProcessRunner = run_bounded,
    ) -> None:
        self._adapter = adapter
        self._process_runner = process_runner

    @property
    def id(self) -> str:
        return self._adapter.id

    def build_command(
        self,
        prompt: str,
        session_id: str,
        reasoning_effort: str,
        session_settings: str | None = None,
        model: str | None = None,
    ) -> list[str]:
        return self._adapter.build_command(
            prompt,
            session_id,
            reasoning_effort,
            self._session_settings() if session_settings is None else session_settings,
            model,
        )

    def _session_settings(self) -> str:
        return os.environ.get(self._adapter.settings_variable) or os.environ.get(
            "ATREX_SESSION_SETTINGS", ""
        )

    def run(self, request: AgentRunRequest) -> AgentRunResult:
        if request.reasoning_effort not in REASONING_EFFORTS:
            raise ValueError(f"unsupported reasoning effort: {request.reasoning_effort!r}")
        session_id = request.session_id or str(uuid.uuid4())
        command = self.build_command(
            request.prompt,
            session_id,
            request.reasoning_effort,
            request.session_settings,
            request.model,
        )
        environment = build_session_environment(self.id)
        environment["IS_SANDBOX"] = "1"
        codex_observer = None
        codex_temporary_home = None
        pre_observation_errors: tuple[str, ...] = ()
        original_codex_home = environment.get("CODEX_HOME")
        isolated_home_ready = False
        if self.id == "codex":
            try:
                codex_temporary_home = CodexTemporaryHome(codex_home(environment))
                isolated_home = codex_temporary_home.open()
                isolated_home_ready = True
                environment["CODEX_HOME"] = str(isolated_home)
                codex_observer = CodexSessionLedgerObserver(isolated_home)
            except Exception as exc:
                pre_observation_errors = (f"codex_ledger_setup_failed:{type(exc).__name__}",)
                if not isolated_home_ready:
                    if codex_temporary_home is not None:
                        cleanup_error = codex_temporary_home.close()
                        if cleanup_error:
                            pre_observation_errors += (cleanup_error,)
                    codex_temporary_home = None
                    if original_codex_home is None:
                        environment.pop("CODEX_HOME", None)
                    else:
                        environment["CODEX_HOME"] = original_codex_home
                with contextlib.suppress(ValueError):
                    command.insert(command.index("--json") + 1, "--ephemeral")
        budget_observer = (
            TokenBudgetObserver(
                self._adapter,
                request.usage_budget,
                codex_home_path=(
                    codex_temporary_home.path if codex_temporary_home is not None else None
                ),
            )
            if request.usage_budget is not None
            else None
        )
        live_trace_observer = (
            _LiveSessionTraceObserver(request.live_trace_path)
            if request.live_trace_path is not None
            else None
        )
        if live_trace_observer is not None and codex_observer is not None:
            live_trace_observer.attach_codex(codex_observer)
        observers = tuple(
            observer for observer in (live_trace_observer, budget_observer) if observer is not None
        )
        process_observer = (
            None
            if not observers
            else observers[0]
            if len(observers) == 1
            else _CombinedProcessObserver(*observers)
        )
        try:
            if process_observer is None:
                process = self._process_runner(
                    command,
                    cwd=request.workspace,
                    timeout=request.timeout_s,
                    env=environment,
                )
            else:
                process = self._process_runner(
                    command,
                    cwd=request.workspace,
                    timeout=request.timeout_s,
                    env=environment,
                    observer=process_observer,
                )
        except BaseException:
            if live_trace_observer is not None:
                live_trace_observer.close()
            if codex_temporary_home is not None:
                codex_temporary_home.close()
            raise
        if live_trace_observer is not None:
            live_trace_observer.close()
        stdout = process.stdout
        stderr = process.stderr
        observation_errors: tuple[str, ...] = pre_observation_errors
        if budget_observer is not None and budget_observer.monitoring_failed:
            observation_errors += ("token_budget_monitoring_failed",)
        try:
            events, terminal_usage = self._adapter.normalize_stream(stdout)
        except Exception as exc:
            # Observation parsing must not turn a completed Agent run into a failure,
            # and the existing terminal provider-usage budget must remain available.
            events = ()
            terminal_usage = terminal_usage_from_stream(stdout)
            observation_errors += (f"stream_normalization_failed:{type(exc).__name__}",)
        capabilities = replace(
            self._adapter.capabilities,
            usage_delta_observed=any(event.kind == "usage_delta" for event in events),
        )
        codex_capture_thread_id = ""
        if codex_observer is not None:
            observed_session_id = codex_thread_id_from_stream(stdout)
            try:
                if not observed_session_id:
                    observed_session_id = codex_observer.identify_new_thread(request.workspace)
                session_id = observed_session_id
                codex_capture_thread_id = observed_session_id
                (
                    events,
                    terminal_usage,
                    capabilities,
                    ledger_errors,
                ) = observe_codex_usage(codex_observer, observed_session_id, terminal_usage)
                observation_errors += ledger_errors
            except Exception as exc:
                observation_errors += (f"codex_ledger_unavailable:{type(exc).__name__}",)
        raw_session_files: tuple[RawSessionFile, ...] = ()
        raw_provider_capture_complete = not process.output_overflow
        if self.id == "codex":
            if codex_observer is None:
                raw_provider_capture_complete = False
                observation_errors += ("codex_raw_rollout_capture_unavailable",)
            else:
                try:
                    if not codex_capture_thread_id:
                        codex_capture_thread_id = codex_thread_id_from_stream(stdout)
                    if not codex_capture_thread_id:
                        codex_capture_thread_id = codex_observer.identify_new_thread(
                            request.workspace
                        )
                    raw_session_files = (
                        RawSessionFile(
                            relative_path="provider/codex-rollout.raw-jsonl",
                            payload=codex_observer.capture_raw_rollout(
                                codex_capture_thread_id,
                                max_bytes=MAX_CODEX_ROLLOUT_CAPTURE_BYTES,
                            ),
                        ),
                    )
                except Exception as exc:
                    raw_provider_capture_complete = False
                    observation_errors += (
                        f"codex_raw_rollout_capture_failed:{type(exc).__name__}",
                    )
        if codex_temporary_home is not None:
            cleanup_error = codex_temporary_home.close()
            if cleanup_error:
                observation_errors += (cleanup_error,)
        return AgentRunResult(
            runtime_id=self.id,
            exit_status=process.returncode,
            timed_out=process.timed_out,
            terminal_usage=terminal_usage,
            events=events,
            capabilities=capabilities,
            observation_errors=observation_errors,
            stdout=stdout,
            stderr=stderr,
            raw_session_files=raw_session_files,
            raw_provider_capture_complete=raw_provider_capture_complete,
            policy_diagnostics=process.policy_diagnostics,
            session_id=session_id,
            budget_exhausted=(budget_observer.exhausted if budget_observer is not None else False),
        )


class ClaudeRuntime(CliAgentRuntime):
    def __init__(
        self,
        *,
        process_runner: ProcessRunner = run_bounded,
    ) -> None:
        super().__init__(ClaudeAdapter(), process_runner=process_runner)


class QoderRuntime(CliAgentRuntime):
    def __init__(
        self,
        *,
        process_runner: ProcessRunner = run_bounded,
    ) -> None:
        super().__init__(QoderAdapter(), process_runner=process_runner)


class PiRuntime(CliAgentRuntime):
    def __init__(
        self,
        *,
        process_runner: ProcessRunner = run_bounded,
    ) -> None:
        super().__init__(PiAdapter(), process_runner=process_runner)


class CodexRuntime(CliAgentRuntime):
    def __init__(
        self,
        *,
        process_runner: ProcessRunner = run_bounded,
    ) -> None:
        super().__init__(CodexAdapter(), process_runner=process_runner)


def build_agent_runtime(
    runtime_id: str,
    *,
    process_runner: ProcessRunner = run_bounded,
    registry: BackendAdapterRegistry = DEFAULT_BACKEND_REGISTRY,
) -> AgentRuntime:
    adapter = registry.create(runtime_id)
    return CliAgentRuntime(adapter, process_runner=process_runner)
