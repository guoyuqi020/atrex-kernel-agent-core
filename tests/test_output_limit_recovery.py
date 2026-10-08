from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from sessions import output_limit_recovery as recovery


def _event(role: str, blocks: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    return {"type": role, "message": {"content": blocks}, **extra}


def _call(identifier: str, name: str = "Bash") -> dict[str, Any]:
    return {"type": "tool_use", "id": identifier, "name": name, "input": {}}


def _receipt(identifier: str, content: str) -> dict[str, Any]:
    return {"type": "tool_result", "tool_use_id": identifier, "content": content}


def _snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    events: list[dict[str, Any]],
) -> tuple[str, dict[str, Any]]:
    monkeypatch.setattr(time, "monotonic", lambda: 100.0)
    prompt = recovery.recovery_prompt(
        SimpleNamespace(workspace=tmp_path),
        "Original task and enabled tool contract.",
        previous_stdout="\n".join(json.dumps(event) for event in events),
        retry=1,
        max_retries=2,
        deadline=200.0,
    )
    snapshot = json.loads(prompt[prompt.index('{\n  "reason"') :])
    return prompt, snapshot


def test_recovery_snapshot_carries_only_root_tool_observations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = [
        _event(
            "assistant",
            [
                {"type": "thinking", "thinking": "PRIVATE-REASONING"},
                {"type": "text", "text": "SPECULATIVE-ANALYSIS"},
                _call("write", "Write"),
            ],
        ),
        _event("user", [_receipt("write", "saved kernel.py")]),
        _event("assistant", [_call("child")], parent_tool_use_id="task"),
        _event("user", [_receipt("child", "CHILD-RESULT")], parent_tool_use_id="task"),
        _event("assistant", [_call("pending")]),
        _event("user", [_receipt("remote", "Result Artifact sha256:actual-runtime-id")]),
    ]
    prompt, snapshot = _snapshot(tmp_path, monkeypatch, events)
    assert "PRIVATE-REASONING" not in prompt
    assert "SPECULATIVE-ANALYSIS" not in prompt
    assert "CHILD-RESULT" not in prompt
    assert [row["tool_use_id"] for row in snapshot["recent_tool_receipts"]] == [
        "write",
        "remote",
    ]
    assert snapshot["recent_tool_receipts"][0]["name"] == "Write"
    assert snapshot["calls_without_observed_result"] == [
        {"tool_use_id": "pending", "name": "Bash"},
    ]
    assert "sha256:actual-runtime-id" in prompt
    assert "not independent validation" in snapshot["receipt_scope"]
    assert "missing receipt does not prove" in snapshot["receipt_scope"]
    assert "Before resubmitting any operation without a receipt" in prompt
    assert "Do not call disabled" in prompt


def test_recovery_inventory_hashes_files_without_importing_source_or_scratch_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kernel = tmp_path / "work/kernel/kernel.py"
    kernel.parent.mkdir(parents=True)
    payload = b"# saved partial kernel; not a measured Artifact\n"
    kernel.write_bytes(payload)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    (scratch / "plan.md").write_text("PLAN-CONTENTS-ARE-NOT-TRUSTED")
    (scratch / "result.json").write_text('{"status":"SPECULATIVE-SUCCESS"}')
    (scratch / "probe.py").write_text("not a plan")
    prompt, snapshot = _snapshot(tmp_path, monkeypatch, [])
    assert snapshot["candidate_files"] == [
        {
            "path": "work/kernel/kernel.py",
            "bytes": len(payload),
            "file_sha256": hashlib.sha256(payload).hexdigest(),
        }
    ]
    assert snapshot["candidate_listing_complete"] is True
    assert snapshot["scratch_note_paths"] == ["scratch/plan.md", "scratch/result.json"]
    assert "not Runtime Kernel Artifact IDs" in snapshot["hash_scope"]
    assert "PLAN-CONTENTS-ARE-NOT-TRUSTED" not in prompt
    assert "SPECULATIVE-SUCCESS" not in prompt
    assert "saved partial kernel" not in prompt
    assert "SAME logical attempt and workspace" in prompt
    assert "do not initialize or reset" in prompt
    assert kernel.read_bytes() == payload


def test_runtime_receipts_survive_later_file_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call = _call("evaluate")
    call["input"] = {
        "command": (
            "python3 agent/optimizer/src/runtime_tools.py gateway-execute --request scratch/e.json"
        )
    }
    events = [
        _event("assistant", [call]),
        _event("user", [_receipt("evaluate", "Result Artifact sha256:measured")]),
    ]
    for number in range(12):
        events += [
            _event("assistant", [_call(str(number), "Read")]),
            _event("user", [_receipt(str(number), "unrelated file content")]),
        ]
    _prompt, snapshot = _snapshot(tmp_path, monkeypatch, events)
    assert all(row["tool_use_id"] != "evaluate" for row in snapshot["recent_tool_receipts"])
    assert snapshot["recent_runtime_tool_receipts"][0]["tool_use_id"] == "evaluate"
    assert (
        "sha256:measured" in snapshot["recent_runtime_tool_receipts"][0]["observed_content_excerpt"]
    )


@pytest.mark.parametrize("link_location", ["file", "directory", "kernel-root", "scratch"])
def test_recovery_snapshot_does_not_follow_symlinks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    link_location: str,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("SENSITIVE-OUTSIDE-CONTENT")
    root = tmp_path / "work/kernel"
    root.parent.mkdir()
    if link_location == "kernel-root":
        root.symlink_to(outside, target_is_directory=True)
    else:
        root.mkdir()
        if link_location == "file":
            (root / "link.py").symlink_to(outside / "secret.md")
        elif link_location == "directory":
            (root / "linked-dir").symlink_to(outside, target_is_directory=True)
        else:
            (tmp_path / "scratch").symlink_to(outside, target_is_directory=True)
    prompt, snapshot = _snapshot(tmp_path, monkeypatch, [])
    assert snapshot["candidate_files"] == []
    assert snapshot["scratch_note_paths"] == []
    assert "SENSITIVE-OUTSIDE-CONTENT" not in prompt
    assert "file_sha256" not in prompt
    if link_location in {"file", "directory", "kernel-root"}:
        assert snapshot["candidate_listing_complete"] is False


def test_recovery_snapshot_bounds_file_hashes_and_receipt_excerpts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(recovery, "_MAX_FILES", 3)
    monkeypatch.setattr(recovery, "_MAX_HASH_BYTES", 5)
    kernel = tmp_path / "work/kernel"
    kernel.mkdir(parents=True)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    for number in range(5):
        (kernel / f"{number}.py").write_text("123456")
        (scratch / f"{number}.md").write_text("note")
    events = []
    for number in range(12):
        events.extend(
            [
                _event("assistant", [_call(f"receipt-{number}")]),
                _event("user", [_receipt(f"receipt-{number}", "x" * 5000)]),
                _event("assistant", [_call(f"pending-{number}")]),
            ]
        )
    _prompt, snapshot = _snapshot(tmp_path, monkeypatch, events)
    assert len(snapshot["candidate_files"]) == 3
    assert snapshot["candidate_listing_complete"] is False
    assert all("file_sha256" not in row for row in snapshot["candidate_files"])
    assert len(snapshot["scratch_note_paths"]) == 3
    assert len(snapshot["recent_tool_receipts"]) == 8
    assert len(snapshot["calls_without_observed_result"]) == 8
    assert snapshot["recent_tool_receipts"][0]["tool_use_id"] == "receipt-4"
    assert all(row["excerpt_truncated"] for row in snapshot["recent_tool_receipts"])
    assert all(
        len(row["observed_content_excerpt"]) <= 2000 for row in snapshot["recent_tool_receipts"]
    )


def test_recovery_snapshot_ignores_malformed_stream_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(time, "monotonic", lambda: 100.0)
    snapshot = recovery._tool_receipts(
        "\n".join(
            [
                "not json",
                "[]",
                "null",
                '{"message":1}',
                '{"type":"assistant","message":{"content":{}}}',
                '{"type":"user","message":{"content":[1,{"type":"tool_result"}]}}',
            ]
        ),
        200.0,
    )
    assert snapshot["recent_tool_receipts"] == []
    assert snapshot["calls_without_observed_result"] == []


def test_recovery_snapshot_honors_shared_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "work/kernel"
    root.mkdir(parents=True)
    (root / "kernel.py").write_text("candidate")
    monkeypatch.setattr(time, "monotonic", lambda: 200.0)
    with pytest.raises(TimeoutError, match="session deadline"):
        recovery.recovery_prompt(
            SimpleNamespace(workspace=tmp_path),
            "task",
            previous_stdout="",
            retry=1,
            max_retries=2,
            deadline=200.0,
        )
