from __future__ import annotations

import json
from typing import Any

import pytest

from session_transcript import render_conversation

MAIN = "provider/claude-session.raw-jsonl"
CHILD = "provider/claude-subagents/agent-child.jsonl"
STDOUT = "provider/stdout.stream-json"


def lines(events: list[dict[str, Any]]) -> str:
    return "".join(json.dumps(event) + "\n" for event in events)


def assistant(message_id: str, *blocks: dict[str, Any], output: int = 0) -> dict[str, Any]:
    return {
        "type": "assistant",
        "message": {
            "id": message_id,
            "content": list(blocks),
            "usage": {"output_tokens": output},
        },
    }


def user(content: str | list[dict[str, Any]], uuid: str = "u1") -> dict[str, Any]:
    return {"type": "user", "uuid": uuid, "message": {"content": content}}


def render(
    stream: list[dict[str, Any]],
    native: list[dict[str, Any]],
    *,
    children: list[dict[str, Any]] | None = None,
    backend: str = "claude",
    raw: bytes | None = None,
    state: str = "finished",
) -> list[dict[str, Any]]:
    files = [(MAIN, raw if raw is not None else lines(native).encode())]
    if children is not None:
        files.append((CHILD, lines(children).encode()))
    output = render_conversation(
        backend=backend,
        session_id="s1",
        prompt="initial prompt",
        stdout=lines(stream),
        raw_provider_files=iter(files),
        state=state,
        exit_status=None if state == "interrupted" else 0,
        timed_out=None if state == "interrupted" else False,
        raw_provider_capture_complete=state != "interrupted",
    )
    records = [json.loads(line) for line in output.splitlines()]
    assert [record["sequence"] for record in records] == list(range(len(records)))
    assert records[0]["type"] == "session_start"
    assert records[-1]["type"] == "session_end"
    return records


def provider(records: list[dict[str, Any]], path: str) -> list[dict[str, Any]]:
    return [
        record["event"]
        for record in records
        if record.get("path") == path and record["type"] == "provider_event"
    ]


def test_native_content_blocks_replace_duplicate_stdout_without_losing_usage() -> None:
    thinking = {"type": "thinking", "thinking": "reasoning"}
    text = {"type": "text", "text": "explanation"}
    tool = {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "pwd"}}
    native = [assistant("m1", thinking), assistant("m1", text), assistant("m1", tool, output=17)]
    result = {"type": "result", "usage": {"output_tokens": 17}}
    records = render([assistant("m1", thinking, text, tool), result], native)

    assert provider(records, MAIN) == native  # distinct fragments are never deleted by ID
    assert provider(records, STDOUT) == [result]
    assert records[-2]["event"] == result


def test_keep_stdout_message_if_native_content_is_incomplete() -> None:
    first = {"type": "text", "text": "first"}
    missing = {"type": "tool_use", "id": "missing"}
    stream = [assistant("m1", first, missing), assistant("m2", first)]
    records = render(stream, [assistant("m1", first)], state="interrupted")
    assert provider(records, STDOUT) == stream
    assert records[-1]["state"] == "interrupted"


def test_duplicate_tool_results_removed_but_real_repeated_messages_remain() -> None:
    block = [{"type": "tool_result", "tool_use_id": "t1", "content": "out"}]
    native = [user(block), assistant("m1", {"type": "text", "text": "next"})]
    stream = [user(block, "stdout-u1"), native[1], user(block, "later-u2")]
    records = render(stream, native)
    assert provider(records, MAIN) == native
    assert provider(records, STDOUT) == [stream[2]]


def test_only_the_initial_native_prompt_is_represented_by_runtime_input() -> None:
    native = [
        user("initial prompt"),
        assistant("m1", {"type": "text", "text": "ok"}),
        user("initial prompt", "u2"),
    ]
    records = render(native, native)
    assert provider(records, MAIN) == native[1:]
    assert provider(records, STDOUT) == []
    assert records[1]["content"] == [{"type": "text", "text": "initial prompt"}]


def test_partial_native_ledger_does_not_misidentify_a_later_prompt_as_initial() -> None:
    native = [assistant("m1", {"type": "text", "text": "ok"}), user("initial prompt")]
    assert provider(render([], native), MAIN) == native


def test_housekeeping_omitted_but_compaction_errors_and_unknown_events_retained() -> None:
    hidden = [
        {"type": kind}
        for kind in (
            "queue-operation",
            "last-prompt",
            "ai-title",
            "file-history-snapshot",
        )
    ] + [{"type": "system", "subtype": "thinking_tokens"}]
    preserved = [
        {"type": "system", "subtype": "compact_boundary"},
        {"type": "system", "subtype": "api_error", "error": "retry"},
        {"type": "attachment", "content": "context"},
        {"type": "new-provider-event", "value": "unknown"},
    ]
    records = render([], hidden + preserved, children=hidden + preserved)
    assert provider(records, MAIN) == preserved
    assert provider(records, CHILD) == preserved


def test_stdout_only_diagnostics_stay_near_their_matched_response() -> None:
    first = assistant("m1", {"type": "text", "text": "first"})
    second = assistant("m2", {"type": "text", "text": "second"})
    init = {"type": "system", "subtype": "init"}
    retry = {"type": "system", "subtype": "api_retry", "attempt": 1}
    result = {"type": "result", "usage": {"output_tokens": 19}}
    records = render([init, first, retry, second, result], [first, second, result])
    assert [record["event"] for record in records[2:-1]] == [init, first, retry, second, result]


def test_main_and_child_message_ids_are_not_deduplicated_against_each_other() -> None:
    main = assistant("same-id", {"type": "text", "text": "same text"})
    child = {**main, "parent_tool_use_id": "task1"}
    records = render([main, child], [main], children=[main])
    assert provider(records, MAIN) == [main]
    assert provider(records, CHILD) == [main]
    assert provider(records, STDOUT) == [child]


@pytest.mark.parametrize("payload", [b"", b'{"type":', b"\xff"])
def test_missing_or_corrupt_native_content_keeps_stdout(payload: bytes) -> None:
    stream = [assistant("m1", {"type": "text", "text": "survived"})]
    records = render(stream, [], raw=payload, state="interrupted")
    assert provider(records, STDOUT) == stream


def test_no_content_message_is_not_deduplicated_just_because_id_matches() -> None:
    native = [assistant("m1", {"type": "text", "text": "done"}, output=7)]
    stream = [assistant("m1", output=4)]
    assert provider(render(stream, native), STDOUT) == stream


def test_stdout_error_is_not_hidden_by_matching_native_content() -> None:
    native = [assistant("m1", {"type": "text", "text": "done"})]
    stream = [{**native[0], "error": "billing_error"}]
    assert provider(render(stream, native), STDOUT) == stream


def test_other_backend_projection_is_unchanged() -> None:
    event = assistant("m1", {"type": "text", "text": "same"})
    records = render([event], [event], backend="qodercli")
    assert provider(records, STDOUT) == [event]
    assert provider(records, MAIN) == [event]
