"""Build one unredacted, backend-neutral conversation transcript."""

from __future__ import annotations

import base64
import json
from collections.abc import Iterable
from typing import Any

FILTERED_PROVIDER_EVENTS = ("system/thinking_tokens",)


def record_provider_line(line: str) -> bool:
    """Drop only high-frequency Claude token-estimate telemetry from Session evidence."""
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return True
    return not (
        isinstance(event, dict)
        and event.get("type") == "system"
        and event.get("subtype") == "thinking_tokens"
    )


def filter_provider_stdout(stdout: str) -> str:
    """Preserve exact retained lines and their original line endings."""
    return "".join(
        line for line in stdout.splitlines(keepends=True) if record_provider_line(line)
    )


def _record(sequence: int, record_type: str, source: str, **data: Any) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "sequence": sequence,
        "type": record_type,
        "source": source,
        **data,
    }


def initial_records(
    *,
    backend: str,
    session_id: str,
    prompt: str,
) -> list[dict[str, Any]]:
    """Describe the capture boundary and the exact Runtime-supplied initial message."""
    return [
        _record(
            0,
            "session_start",
            "runtime",
            backend=backend,
            session_id=session_id,
            provider_system_prompt={
                "captured": False,
                "reason": "provider-managed system prompt is not exported by the CLI",
            },
        ),
        _record(
            1,
            "message",
            "runtime_input",
            role="user",
            content=[{"type": "text", "text": prompt}],
        ),
    ]


def provider_line_record(
    sequence: int,
    *,
    path: str,
    line: str,
) -> dict[str, Any]:
    """Retain one Provider line as parsed JSON or exact text when it is not JSON."""
    try:
        event = json.loads(line)
        json.dumps(event, ensure_ascii=False, allow_nan=False)
    except (json.JSONDecodeError, TypeError, ValueError):
        return _record(
            sequence,
            "provider_text",
            "provider",
            path=path,
            text=line,
        )
    return _record(
        sequence,
        "provider_event",
        "provider",
        path=path,
        event=event,
    )


def provider_bytes_records(
    sequence: int,
    *,
    path: str,
    payload: bytes,
) -> tuple[list[dict[str, Any]], int]:
    """Project a Provider-owned transcript file without dropping non-UTF-8 data."""
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        return (
            [
                _record(
                    sequence,
                    "provider_binary",
                    "provider",
                    path=path,
                    encoding="base64",
                    content=base64.b64encode(payload).decode("ascii"),
                )
            ],
            sequence + 1,
        )
    records: list[dict[str, Any]] = []
    for line in text.splitlines():
        records.append(provider_line_record(sequence, path=path, line=line))
        sequence += 1
    return records, sequence


def terminal_record(
    sequence: int,
    *,
    state: str,
    exit_status: int | None,
    timed_out: bool | None,
    raw_provider_capture_complete: bool,
    error_type: str | None = None,
) -> dict[str, Any]:
    value = _record(
        sequence,
        "session_end",
        "runtime",
        state=state,
        exit_status=exit_status,
        timed_out=timed_out,
        raw_provider_capture_complete=raw_provider_capture_complete,
    )
    if error_type is not None:
        value["error_type"] = error_type
    return value


def encode_records(records: Iterable[dict[str, Any]]) -> str:
    return "".join(
        json.dumps(record, ensure_ascii=False, allow_nan=False, sort_keys=True) + "\n"
        for record in records
    )


def render_conversation(
    *,
    backend: str,
    session_id: str,
    prompt: str,
    stdout: str,
    raw_provider_files: Iterable[tuple[str, bytes]],
    state: str,
    exit_status: int | None,
    timed_out: bool | None,
    raw_provider_capture_complete: bool,
    error_type: str | None = None,
) -> str:
    """Render conversational input and every retained Provider event in one JSONL file."""
    records = initial_records(backend=backend, session_id=session_id, prompt=prompt)
    sequence = len(records)
    for line in stdout.splitlines():
        if not record_provider_line(line):
            continue
        records.append(
            provider_line_record(
                sequence,
                path="provider/stdout.stream-json",
                line=line,
            )
        )
        sequence += 1
    for path, payload in raw_provider_files:
        projected, sequence = provider_bytes_records(
            sequence,
            path=path,
            payload=payload,
        )
        records.extend(projected)
    records.append(
        terminal_record(
            sequence,
            state=state,
            exit_status=exit_status,
            timed_out=timed_out,
            raw_provider_capture_complete=raw_provider_capture_complete,
            error_type=error_type,
        )
    )
    return encode_records(records)
