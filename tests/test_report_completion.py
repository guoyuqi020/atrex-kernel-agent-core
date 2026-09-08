from __future__ import annotations

import importlib
import io
import json
import urllib.error
import urllib.request
from dataclasses import replace
from email.message import Message
from pathlib import Path
from typing import Any

import pytest

import runtime_tools
from contexts.attempt import RuntimeAttemptContext
from contexts.lineage_bootstrap import RuntimeLineageBootstrapContext
from sessions.report_completion import ReportContext, report_completion_prompt

_ATTEMPT_ID = "attempt-private-identity"
_DIGEST = "sha256:" + "a" * 64


def _context(tmp_path: Path, *, bootstrap: bool = False) -> ReportContext:
    workspace = tmp_path / "workspace"
    (workspace / "scratch").mkdir(parents=True)
    trace = workspace / "sessions" / "optimizer"
    trace.mkdir(parents=True)
    (trace / "conversation.jsonl").write_text("{}\n", encoding="utf-8")
    task = {
        "operator": "public-matmul",
        "hardware_target": "public-gpu",
        "attempt_ordinal": 987654321,
        "epoch_number": 123456789,
    }
    fields: dict[str, Any] = {
        "workspace": workspace,
        "repository": workspace / "agent" / "optimizer",
        "manifest_path": workspace / ".runtime" / "manifest.json",
        "report_path": workspace / "scratch" / "attempt-report.json",
        "token_usage_path": workspace / "scratch" / "token-usage.json",
        "session_trace_path": trace,
        "gateway_url": "https://runtime.invalid",
        "gateway_capability": "secret-capability",
        "agent_problem": {"private_tests": "DO-NOT-DISCLOSE"},
        "wiki_url": None,
        "wiki_capability": None,
        "usage_unit": "provider_tokens",
        "usage_budget": 1_000.0,
        "timeout_seconds": 60.0,
    }
    if bootstrap:
        return RuntimeLineageBootstrapContext(
            **fields,
            manifest={"bootstrap_attempt_id": _ATTEMPT_ID, "dsl": "triton", **task},
        )
    return RuntimeAttemptContext(
        **fields,
        evidence_prompt="Existing public evidence",
        manifest={"attempt_id": _ATTEMPT_ID, "dsl": "triton", "context": task},
    )


def _report(context: ReportContext, status: str = "blocked") -> dict[str, Any]:
    return {
        "schema_version": 12,
        "attempt_id": context.attempt_id,
        "status": status,
        "analysis": "Use the already recorded evidence.",
        "experiments": [{"id": "experiment-existing", "observations": []}],
        "direction_events": [],
    }


def _accepted(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "result": {
            "status": "accepted",
            "report": report,
            "report_artifact_digest": _DIGEST,
        }
    }


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Unexpected network request")

    monkeypatch.setattr(urllib.request, "urlopen", forbidden)


def _http(monkeypatch: pytest.MonkeyPatch, value: object) -> list[urllib.request.Request]:
    requests: list[urllib.request.Request] = []

    def urlopen(request: urllib.request.Request, *, timeout: float) -> io.BytesIO:
        assert 0 < timeout <= 30.0
        requests.append(request)
        return io.BytesIO(json.dumps(value).encode())

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return requests


def test_missing_queries_runtime_with_fresh_authenticated_keys_and_report_only_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _context(tmp_path)
    requests = _http(monkeypatch, {"result": {"status": "missing"}})
    first = report_completion_prompt(context, 30.0)
    second = report_completion_prompt(context, 30.0)

    assert first == second
    assert first is not None
    assert len(requests) == 2
    payloads = []
    for request in requests:
        assert request.full_url == "https://runtime.invalid/v1/runtime/queries"
        assert request.get_method() == "POST"
        assert request.get_header("Authorization") == "Bearer secret-capability"
        assert isinstance(request.data, bytes)
        payload = json.loads(request.data)
        assert set(payload) == {"schema_version", "attempt_id", "operation", "idempotency_key"}
        assert payload["schema_version"] == 2
        assert payload["attempt_id"] == context.attempt_id
        assert payload["operation"] == "attempt_report_status"
        payloads.append(payload)
    assert payloads[0]["idempotency_key"] != payloads[1]["idempotency_key"]
    for text in (
        "report-only",
        "Do not start new optimization",
        "Runtime Journal",
        "list-directions",
        "list-experiments",
        "load-experiment",
        "sessions/optimizer/conversation.jsonl",
        "Reuse already measured results",
        "attempt-report --request",
        "successful Runtime receipt",
        "status: published",
        "pivot or blocked",
        "public-matmul",
        "public-gpu",
        "triton",
    ):
        assert text in first
    for private in (
        _ATTEMPT_ID,
        "987654321",
        "123456789",
        "DO-NOT-DISCLOSE",
        "secret-capability",
        str(context.workspace),
    ):
        assert private not in first
    assert not context.report_path.exists()


@pytest.mark.parametrize("local_bytes", [b'{"status":"blocked"}\n', b"unaccepted malformed draft"])
def test_missing_preserves_local_drafts_under_unique_backup_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, local_bytes: bytes
) -> None:
    context = _context(tmp_path)
    _http(monkeypatch, {"result": {"status": "missing"}})
    for _ in range(2):
        context.report_path.write_bytes(local_bytes)
        prompt = report_completion_prompt(context, 30.0)
        assert prompt is not None
        assert not context.report_path.exists()
        backups = list((context.workspace / "scratch").glob("report-completion-backup-*.json"))
        assert any(backup.relative_to(context.workspace).as_posix() in prompt for backup in backups)
        assert "draft only" in prompt
    assert len(backups) == 2
    assert all(backup.read_bytes() == local_bytes for backup in backups)


@pytest.mark.parametrize(
    ("bootstrap", "status"),
    [
        (False, "candidate_ready"),
        (False, "pivot"),
        (False, "blocked"),
        (True, "candidate_ready"),
        (True, "blocked"),
    ],
)
def test_accepted_server_report_is_restored_completely(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bootstrap: bool, status: str
) -> None:
    context = _context(tmp_path, bootstrap=bootstrap)
    report = _report(context, status)
    _http(monkeypatch, _accepted(report))
    assert report_completion_prompt(context, 30.0) is None
    assert json.loads(context.report_path.read_bytes()) == report
    assert context.report_path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("old", [b'{"status":"pivot"}', b"broken JSON"])
def test_accepted_report_replaces_stale_local_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, old: bytes
) -> None:
    context = _context(tmp_path)
    context.report_path.write_bytes(old)
    report = _report(context)
    _http(monkeypatch, _accepted(report))
    assert report_completion_prompt(context, 30.0) is None
    assert json.loads(context.report_path.read_bytes()) == report


def test_equal_local_report_is_not_rewritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _context(tmp_path)
    report = _report(context)
    original = json.dumps(report, indent=2).encode()
    context.report_path.write_bytes(original)
    metadata = context.report_path.stat()
    _http(monkeypatch, _accepted(report))

    def no_write(*args: object, **kwargs: object) -> None:
        pytest.fail("Equal accepted report should not be rewritten")

    monkeypatch.setattr(runtime_tools, "_atomic_json", no_write)
    assert report_completion_prompt(context, 30.0) is None
    assert context.report_path.read_bytes() == original
    assert context.report_path.stat().st_ino == metadata.st_ino
    assert context.report_path.stat().st_mtime_ns == metadata.st_mtime_ns


@pytest.mark.parametrize(
    "mutation",
    ["wrong_attempt", "invalid_status", "object_status", "no_report", "array_report", "no_digest"],
)
def test_invalid_accepted_result_cannot_count_as_completion_or_move_local_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    context = _context(tmp_path)
    context.report_path.write_bytes(b"keep this draft")
    response = _accepted(_report(context))
    result = response["result"]
    if mutation == "wrong_attempt":
        result["report"]["attempt_id"] = "different-private-identity"
    elif mutation == "invalid_status":
        result["report"]["status"] = "completed"
    elif mutation == "object_status":
        result["report"]["status"] = {}
    elif mutation == "no_report":
        del result["report"]
    elif mutation == "array_report":
        result["report"] = []
    else:
        del result["report_artifact_digest"]
    _http(monkeypatch, response)
    with pytest.raises(ValueError):
        report_completion_prompt(context, 30.0)
    assert context.report_path.read_bytes() == b"keep this draft"
    assert not list((context.workspace / "scratch").glob("report-completion-backup-*"))


def test_bootstrap_rejects_pivot_and_missing_prompt_only_offers_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _context(tmp_path, bootstrap=True)
    _http(monkeypatch, _accepted(_report(context, "pivot")))
    with pytest.raises(ValueError, match="terminal status"):
        report_completion_prompt(context, 30.0)
    assert not context.report_path.exists()
    _http(monkeypatch, {"result": {"status": "missing"}})
    prompt = report_completion_prompt(context, 30.0)
    assert prompt is not None
    assert "report blocked honestly" in prompt
    assert "pivot" not in prompt


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"result": None},
        {"result": []},
        {"result": {}},
        {"result": {"status": "registered"}},
        {"result": {"status": "failed"}},
    ],
)
def test_unknown_registry_response_is_an_error_not_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, response: object
) -> None:
    context = _context(tmp_path)
    context.report_path.write_bytes(b"keep local report")
    _http(monkeypatch, response)
    with pytest.raises(ValueError):
        report_completion_prompt(context, 30.0)
    assert context.report_path.read_bytes() == b"keep local report"


@pytest.mark.parametrize("failure", ["unauthorized", "server_error", "unavailable", "timeout"])
def test_registry_failures_propagate_without_moving_or_accepting_local_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    context = _context(tmp_path)
    context.report_path.write_bytes(b"keep local report")
    requests: list[urllib.request.Request] = []

    def urlopen(request: urllib.request.Request, *, timeout: float) -> io.BytesIO:
        requests.append(request)
        if failure in {"unauthorized", "server_error"}:
            code = 401 if failure == "unauthorized" else 500
            raise urllib.error.HTTPError(
                request.full_url,
                code,
                "fake HTTP failure",
                Message(),
                io.BytesIO(b'{"detail":"Registry rejected request"}'),
            )
        if failure == "timeout":
            raise TimeoutError("fake timeout")
        raise urllib.error.URLError("fake offline connection failure")

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    with pytest.raises((RuntimeError, TimeoutError)):
        report_completion_prompt(context, 30.0)
    assert len(requests) == 1
    assert context.report_path.read_bytes() == b"keep local report"
    assert not list((context.workspace / "scratch").glob("report-completion-backup-*"))


@pytest.mark.parametrize("accepted", [False, True])
@pytest.mark.parametrize(
    "unsafe", ["leaf_link", "parent_link", "scratch_link", "outside", "traversal", "directory"]
)
def test_report_path_rejects_links_escape_and_nonregular_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, unsafe: str, accepted: bool
) -> None:
    context = _context(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "report.json"
    sentinel.write_bytes(b"outside must remain unchanged")
    if unsafe == "leaf_link":
        context.report_path.symlink_to(sentinel)
    elif unsafe == "parent_link":
        link = context.workspace / "scratch" / "linked"
        link.symlink_to(outside, target_is_directory=True)
        context = replace(context, report_path=link / "report.json")
    elif unsafe == "scratch_link":
        scratch = context.workspace / "scratch"
        scratch.rmdir()
        scratch.symlink_to(outside, target_is_directory=True)
    elif unsafe == "outside":
        context = replace(context, report_path=sentinel)
    elif unsafe == "traversal":
        context = replace(
            context,
            report_path=context.workspace / "scratch" / ".." / ".." / "outside" / "report.json",
        )
    else:
        context.report_path.mkdir()
    response = _accepted(_report(context)) if accepted else {"result": {"status": "missing"}}
    _http(monkeypatch, response)
    with pytest.raises(ValueError):
        report_completion_prompt(context, 30.0)
    assert sentinel.read_bytes() == b"outside must remain unchanged"
    assert list(outside.iterdir()) == [sentinel]


def test_nested_real_scratch_path_is_supported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _context(tmp_path)
    context = replace(
        context, report_path=context.workspace / "scratch" / "reports" / "report.json"
    )
    report = _report(context)
    _http(monkeypatch, _accepted(report))
    assert report_completion_prompt(context, 30.0) is None
    assert json.loads(context.report_path.read_bytes()) == report


def test_trace_hint_never_discloses_private_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _context(tmp_path)
    context = replace(
        context, session_trace_path=context.workspace / "sessions" / context.attempt_id
    )
    _http(monkeypatch, {"result": {"status": "missing"}})
    prompt = report_completion_prompt(context, 30.0)
    assert prompt is not None
    assert _ATTEMPT_ID not in prompt
    assert "`sessions/`" in prompt


@pytest.mark.parametrize("session", ["attempt", "lineage_bootstrap"])
def test_attempt_entrypoints_wire_report_completion_callback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, session: str
) -> None:
    module = importlib.import_module(f"sessions.{session}")
    context = _context(tmp_path, bootstrap=session == "lineage_bootstrap")
    config = object()
    monkeypatch.setattr(type(context), "from_environment", lambda: context)
    monkeypatch.setattr(module.AgentConfig, "load", lambda *args, **kwargs: config)
    monkeypatch.setattr(module, "render_prompt", lambda *args: "original prompt")
    monkeypatch.setattr(module, "render_system_prompt", lambda *args: "original system prompt")
    checked: list[ReportContext] = []

    def check(value: ReportContext, remaining_timeout_seconds: float) -> str:
        assert remaining_timeout_seconds == 17.0
        checked.append(value)
        return "report-only continuation"

    def execute(*args: object, **kwargs: Any) -> int:
        assert args == (context, config, "original prompt")
        assert kwargs["system_prompt"] == "original system prompt"
        assert kwargs["completion_check"](17.0) == "report-only continuation"
        return 7

    monkeypatch.setattr(module, "report_completion_prompt", check)
    monkeypatch.setattr(module, "execute_agent_session", execute)
    assert module.run() == 7
    assert checked == [context]


def test_problem_generalization_entrypoint_has_no_report_completion_callback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = importlib.import_module("sessions.problem_generalization")
    context = _context(tmp_path)
    monkeypatch.setattr(
        module.RuntimeProblemGeneralizationContext, "from_environment", lambda: context
    )
    monkeypatch.setattr(module.AgentConfig, "load", lambda *args, **kwargs: object())
    monkeypatch.setattr(module, "render_prompt", lambda *args: "problem generalization")

    def execute(*args: object, **kwargs: Any) -> int:
        assert "completion_check" not in kwargs
        assert callable(kwargs["on_success"])
        return 0

    monkeypatch.setattr(module, "execute_agent_session", execute)
    assert module.run() == 0


@pytest.mark.parametrize("timeout", [0.0, -1.0, float("nan"), float("inf")])
def test_invalid_remaining_timeout_is_rejected_before_query(tmp_path: Path, timeout: float) -> None:
    context = _context(tmp_path)
    with pytest.raises(ValueError, match="positive finite"):
        report_completion_prompt(context, timeout)


@pytest.mark.parametrize("accepted", [False, True])
def test_total_deadline_bounds_stalled_http_without_late_local_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, accepted: bool
) -> None:
    import threading
    import time

    from sessions import report_completion

    context = _context(tmp_path)
    context.report_path.write_bytes(b"preserve local draft")
    response = _accepted(_report(context)) if accepted else {"result": {"status": "missing"}}
    requests = _http(monkeypatch, response)
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    original = report_completion._post_once

    def stalled_post(value: ReportContext, timeout: float) -> dict[str, Any]:
        try:
            started.set()
            assert release.wait(2), "test did not release the read-only HTTP worker"
            return original(value, timeout)
        finally:
            finished.set()

    monkeypatch.setattr(report_completion, "_post_once", stalled_post)
    started_at = time.monotonic()
    try:
        with pytest.raises(TimeoutError, match="remaining session deadline"):
            report_completion_prompt(context, 0.05)
        assert time.monotonic() - started_at < 0.5
        assert started.is_set()
        assert context.report_path.read_bytes() == b"preserve local draft"
        assert not list((context.workspace / "scratch").glob("report-completion-backup-*"))
    finally:
        release.set()
        assert finished.wait(1)
    assert len(requests) == 1
    assert context.report_path.read_bytes() == b"preserve local draft"
    assert not list((context.workspace / "scratch").glob("report-completion-backup-*"))


def test_response_byte_limit_is_preserved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context(tmp_path)
    monkeypatch.setattr(runtime_tools, "_MAX_RESPONSE_BYTES", 16)
    _http(monkeypatch, {"result": {"status": "missing"}})
    with pytest.raises(RuntimeError, match="byte limit"):
        report_completion_prompt(context, 30.0)
    assert not context.report_path.exists()
