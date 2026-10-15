"""Candidate validation must consume bounded repair, never infinite report retries."""

from test_session_completion import _setup, _config, _Outcome, _read_json
from sessions.common import execute_agent_session
from sessions.source_completion import SourceCompletionCheck


def test_source_validation_exhaustion_keeps_trace_and_budget(tmp_path, monkeypatch):
    context, runtime, clock = _setup(tmp_path, monkeypatch, [_Outcome(elapsed=2) for _ in range(3)])
    protected = tmp_path / ".runtime"
    protected.mkdir()
    (protected / "source-policy.json").write_text("{}")
    (protected / "source-validator.py").write_text(
        'def validate_source_tree(root, **policy):\n    raise ValueError("outside editable_roots: notes.md")\n'
    )
    check = SourceCompletionCheck(tmp_path)
    code = execute_agent_session(context, _config(), "optimize", system_prompt="source limits", completion_check=check)
    assert code == 128 and len(runtime.requests) == 3
    assert [r.usage_budget for r in runtime.requests] == [1000, 984, 968]
    assert runtime.requests[2].timeout_s < runtime.requests[0].timeout_s
    assert "notes.md" in runtime.requests[1].prompt
    assert "terminal attempt report" not in runtime.requests[1].prompt
    assert _read_json(context.token_usage_path)["consumed"] == 48
    assert _read_json(context.session_trace_path / "session.json")["exit_status"] == 128
