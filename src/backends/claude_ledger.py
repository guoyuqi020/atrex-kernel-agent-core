"""Capture Claude's native, session-scoped transcript and settled message usage.

The print stream can contain provisional counters. Never add duplicate stream and
native messages. A terminal result can cover either the main session or the whole
session tree; reconcile that scope before using it for accounting.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

from session_transcript import filter_provider_stdout

from .adapter import token_usage_from_mapping
from .model import (
    NormalizedAgentEvent,
    RawSessionFile,
    TokenUsage,
    resequence_agent_events,
    sum_token_usages,
)

CLAUDE_USAGE_UNRECONCILED = "claude_response_usage_incomplete_or_unreconciled"
CLAUDE_MAIN_ONLY_USAGE = "claude_terminal_usage_excludes_subagents"
CLAUDE_USAGE_WARNINGS = frozenset((CLAUDE_USAGE_UNRECONCILED, CLAUDE_MAIN_ONLY_USAGE))
_MAIN_TRANSCRIPT = "provider/claude-session.raw-jsonl"
_COMPONENTS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")


def _same_usage(left: TokenUsage, right: TokenUsage) -> bool:
    return all(
        getattr(left, key) is not None and getattr(left, key) == getattr(right, key)
        for key in (*_COMPONENTS, "total_tokens")
    )


def _conservative_usage(observed: TokenUsage, terminal: TokenUsage) -> TokenUsage:
    """Keep the larger known count per bucket without adding overlapping bills."""
    components: dict[str, int | None] = {}
    for key in _COMPONENTS:
        known = [
            value for value in (getattr(observed, key), getattr(terminal, key)) if value is not None
        ]
        components[key] = max(known) if known else None
    known_total = sum(value for value in components.values() if value is not None)
    available = any(value is not None for value in components.values())
    return TokenUsage(
        input_tokens=components["input_tokens"],
        output_tokens=components["output_tokens"],
        cache_read_tokens=components["cache_read_tokens"],
        cache_write_tokens=components["cache_write_tokens"],
        total_tokens=known_total if available else None,
        measurement="partial" if available else "unavailable",
    )


class ClaudeSessionLedger:
    """Read only this session's main JSONL and its nested subagent JSONLs."""

    def __init__(self, environment: Mapping[str, str], session_id: str) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", session_id):
            raise ValueError("unsafe Claude session ID")
        configured = environment.get("CLAUDE_CONFIG_DIR")
        self.root = (
            Path(configured)
            if configured
            else Path(environment.get("HOME", str(Path.home()))) / ".claude"
        ).resolve() / "projects"
        self.session_id = session_id
        self._offsets: dict[Path, int] = {}

    def _paths(self) -> list[tuple[Path, str]]:
        mains = sorted(self.root.glob(f"*/{self.session_id}.jsonl"))
        if len(mains) > 1:
            raise ValueError("ambiguous Claude session ledger")
        if not mains:
            return []
        main = mains[0]
        paths = [(main, "provider/claude-session.raw-jsonl")]
        children = main.with_suffix("") / "subagents"
        paths.extend(
            (path, "provider/claude-subagents/" + path.name)
            for path in sorted(children.glob("*.jsonl"))
        )
        for path, _relative in paths:
            if path.is_symlink() or not path.resolve().is_relative_to(self.root):
                raise ValueError("Claude ledger escapes session storage")
        return paths

    def sync_live(self, trace_root: Path) -> None:
        """Copy new complete lines only; an unfinished line is retried on the next poll."""
        for path, relative in self._paths():
            offset = self._offsets.get(path, 0)
            if path.stat().st_size < offset:
                raise ValueError("Claude session ledger was truncated")
            with path.open("rb") as source:
                source.seek(offset)
                data = source.read()
            end = data.rfind(b"\n") + 1
            if not end:
                continue
            payload = filter_provider_stdout(data[:end].decode("utf-8")).encode("utf-8")
            destination = trace_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("ab" if offset else "wb") as output:
                output.write(payload)
            self._offsets[path] = offset + end

    def capture(self) -> tuple[RawSessionFile, ...]:
        paths = self._paths()
        if not paths:
            raise FileNotFoundError("Claude native session ledger not found")
        return tuple(
            RawSessionFile(
                relative, filter_provider_stdout(path.read_bytes().decode("utf-8")).encode("utf-8")
            )
            for path, relative in paths
        )


def observe_claude_usage(
    files: tuple[RawSessionFile, ...],
    stream_events: tuple[NormalizedAgentEvent, ...],
    terminal: TokenUsage,
) -> tuple[tuple[NormalizedAgentEvent, ...], TokenUsage, bool, tuple[str, ...]]:
    """Use the last counters per message, preserving attribution back to raw content."""
    responses: dict[str, NormalizedAgentEvent] = {}
    missing_usage: set[str] = set()
    malformed = False
    for file in files:
        for line in file.payload.decode("utf-8").splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                malformed = True
                continue
            if not isinstance(record, dict) or record.get("type") != "assistant":
                continue
            message = record.get("message")
            if not isinstance(message, dict) or not isinstance(message.get("id"), str):
                malformed = True
                continue
            message_id = message["id"]
            usage = token_usage_from_mapping(message.get("usage"))
            if usage.total_tokens is None:
                if message_id not in responses:
                    missing_usage.add(message_id)
                continue
            missing_usage.discard(message_id)
            # A child's copied parent context is not an additional model response.
            if (
                message_id in responses
                and responses[message_id].source_path == _MAIN_TRANSCRIPT
                and file.relative_path != _MAIN_TRANSCRIPT
            ):
                continue
            responses[message_id] = NormalizedAgentEvent(
                sequence=0,
                kind="usage_delta",
                usage=usage,
                message_id=message_id,
                source_path=file.relative_path,
            )

    # Keep stream-only responses as explicitly provisional instead of silently dropping them.
    stream_only = [
        event
        for event in stream_events
        if event.kind == "usage_delta" and event.message_id not in responses
    ]
    events = [*responses.values(), *stream_only]
    observed = sum_token_usages([event.usage for event in events if event.usage is not None])
    main = sum_token_usages(
        [
            event.usage
            for event in responses.values()
            if event.source_path == _MAIN_TRANSCRIPT and event.usage is not None
        ]
    )
    structurally_complete = (
        bool(responses)
        and not (malformed or missing_usage or stream_only)
        and terminal.measurement == "exact"
    )
    whole_tree_matches = _same_usage(observed, terminal)
    main_only_matches = _same_usage(main, terminal)
    complete = structurally_complete and (whole_tree_matches or main_only_matches)
    errors: tuple[str, ...] = ()
    if complete:
        if not whole_tree_matches:
            errors = (CLAUDE_MAIN_ONLY_USAGE,)
        # Charge every unique response, including children excluded by the terminal
        # result. When terminal includes children this is the same bill, not a sum.
        terminal = observed
    else:
        errors = (CLAUDE_USAGE_UNRECONCILED,)
        events = [
            replace(event, usage=replace(event.usage, measurement="partial"))
            if event.usage is not None
            else event
            for event in events
        ]
        terminal = _conservative_usage(observed, terminal)
    if terminal.total_tokens is not None:
        events.append(NormalizedAgentEvent(0, "terminal_usage", terminal))
    return resequence_agent_events(events), terminal, complete, errors
