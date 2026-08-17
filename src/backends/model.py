from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

UsageMeasurement = Literal["exact", "partial", "unavailable"]
NormalizedEventKind = Literal["usage_delta", "terminal_usage"]


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int | None
    output_tokens: int | None
    cache_read_tokens: int | None
    cache_write_tokens: int | None
    total_tokens: int | None
    measurement: UsageMeasurement

    @classmethod
    def unavailable(cls) -> TokenUsage:
        return cls(
            input_tokens=None,
            output_tokens=None,
            cache_read_tokens=None,
            cache_write_tokens=None,
            total_tokens=None,
            measurement="unavailable",
        )

    @classmethod
    def zero(cls) -> TokenUsage:
        return cls(0, 0, 0, 0, 0, "exact")


def sum_token_usages(usages: Sequence[TokenUsage]) -> TokenUsage:
    observed = [usage for usage in usages if usage.total_tokens is not None]
    if not observed:
        return TokenUsage.unavailable()

    def component(name: str) -> int | None:
        values = [getattr(usage, name) for usage in observed]
        if any(value is None for value in values):
            return None
        return sum(int(value) for value in values if value is not None)

    return TokenUsage(
        input_tokens=component("input_tokens"),
        output_tokens=component("output_tokens"),
        cache_read_tokens=component("cache_read_tokens"),
        cache_write_tokens=component("cache_write_tokens"),
        total_tokens=sum(usage.total_tokens or 0 for usage in observed),
        measurement=(
            "exact"
            if len(observed) == len(usages)
            and all(usage.measurement == "exact" for usage in observed)
            else "partial"
        ),
    )


def token_usage_exceeds(observed: TokenUsage, terminal: TokenUsage) -> bool:
    for name in (
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "total_tokens",
    ):
        observed_value = getattr(observed, name)
        terminal_value = getattr(terminal, name)
        if (
            observed_value is not None
            and terminal_value is not None
            and observed_value > terminal_value
        ):
            return True
    return False


def subtract_token_usage(total: TokenUsage, part: TokenUsage) -> TokenUsage:
    if token_usage_exceeds(part, total):
        raise ValueError("token usage part exceeds total")

    def component(name: str) -> int | None:
        left = getattr(total, name)
        right = getattr(part, name)
        if left is None or right is None:
            return None
        return int(left) - int(right)

    return TokenUsage(
        input_tokens=component("input_tokens"),
        output_tokens=component("output_tokens"),
        cache_read_tokens=component("cache_read_tokens"),
        cache_write_tokens=component("cache_write_tokens"),
        total_tokens=component("total_tokens"),
        measurement=(
            "exact" if total.measurement == "exact" and part.measurement == "exact" else "partial"
        ),
    )


@dataclass(frozen=True)
class NormalizedAgentEvent:
    sequence: int
    kind: NormalizedEventKind
    usage: TokenUsage | None = None


def resequence_agent_events(
    events: Sequence[NormalizedAgentEvent],
) -> tuple[NormalizedAgentEvent, ...]:
    return tuple(
        NormalizedAgentEvent(
            sequence=index,
            kind=event.kind,
            usage=event.usage,
        )
        for index, event in enumerate(events)
    )


@dataclass(frozen=True)
class AgentRuntimeCapabilities:
    terminal_usage: bool
    usage_delta: bool
    usage_delta_observed: bool = False


@dataclass(frozen=True)
class AgentRunRequest:
    workspace: Path
    prompt: str
    timeout_s: int
    reasoning_effort: str = "max"
    session_id: str | None = None
    session_settings: str = ""
    token_budget: int | None = None

    def __post_init__(self) -> None:
        if self.token_budget is not None and self.token_budget <= 0:
            raise ValueError("Agent token budget must be positive")


@dataclass(frozen=True)
class RawSessionFile:
    """One unmodified Provider-owned Session file captured before cleanup."""

    relative_path: str
    payload: bytes


@dataclass(frozen=True)
class AgentRunResult:
    runtime_id: str
    exit_status: int
    timed_out: bool
    terminal_usage: TokenUsage
    events: tuple[NormalizedAgentEvent, ...]
    capabilities: AgentRuntimeCapabilities
    observation_errors: tuple[str, ...]
    stdout: str
    stderr: str
    raw_session_files: tuple[RawSessionFile, ...]
    raw_provider_capture_complete: bool
    policy_diagnostics: tuple[str, ...]
    session_id: str = ""
    budget_exhausted: bool = False

    @property
    def stdout_tail(self) -> str:
        return self.stdout[-2000:]

    @property
    def stderr_tail(self) -> str:
        return self.stderr[-2000:]

    @property
    def tokens(self) -> int:
        """Compatibility total used by existing campaign token budgets."""
        return self.terminal_usage.total_tokens or 0


class AgentRuntime(Protocol):
    @property
    def id(self) -> str: ...

    def run(self, request: AgentRunRequest) -> AgentRunResult: ...
