"""Capture Claude's native, session-scoped transcript and settled message usage.

The print stream can contain provisional counters. Never add that stream to the
native ledger: both describe the same responses. The result event remains the
authority for the session bill, even when response attribution is incomplete.
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
    components = (
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "total_tokens",
    )
    complete = (
        bool(responses)
        and not (malformed or missing_usage or stream_only)
        and all(
            getattr(observed, key) is not None and getattr(observed, key) == getattr(terminal, key)
            for key in components
        )
        and terminal.measurement == "exact"
    )
    errors: tuple[str, ...] = ()
    if not complete:
        errors = ("claude_response_usage_incomplete_or_unreconciled",)
        events = [
            replace(event, usage=replace(event.usage, measurement="partial"))
            if event.usage is not None
            else event
            for event in events
        ]
    if terminal.measurement != "exact" and observed.total_tokens is not None:
        terminal = replace(observed, measurement="partial")
    if terminal.total_tokens is not None:
        events.append(NormalizedAgentEvent(0, "terminal_usage", terminal))
    return resequence_agent_events(events), terminal, complete, errors
