from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.request import Request

import pytest

import runtime_tools
from agent_config import AgentConfig
from backends.claude_runtime_tool import hook_output
from contexts.attempt import RuntimeAttemptContext
from contexts.lineage_bootstrap import RuntimeLineageBootstrapContext
from runtime_contract import project_contract, wiki_enabled
from sessions import attempt, lineage_bootstrap

CORE_ROOT = Path(__file__).resolve().parents[1]


def _live_contract(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    enabled: object = True,
    binding: dict[str, str] | None = None,
    modules: tuple[str, ...] = (),
) -> Path:
    contract = root / "input/runtime-contract"
    contract.mkdir(parents=True)
    bindings: dict[str, Any] = {
        "runtime-contract": {},
        "gateway-execute": {},
        "attempt-report": {},
    }
    if binding is not None:
        bindings["wiki-query"] = binding
    values = {
        "tools": {"schema_version": 1, "gateway": {"operations": {}}, "bindings": bindings},
        "environment": {
            "schema_version": 1,
            "services": {"wiki": enabled},
            "tool_modules": list(modules),
        },
        "limits": {"schema_version": 1},
    }
    for name, value in values.items():
        (contract / f"{name}.json").write_text(json.dumps(value))
    monkeypatch.setenv("ATREX_RUNTIME_CONTRACT_PATH", str(contract))
    return contract


_BINDING = {"kind": "runtime-query", "operation": "wiki_query"}


def _context(root: Path) -> Any:
    (root / "scratch").mkdir(exist_ok=True)
    return SimpleNamespace(
        workspace=root,
        attempt_id="attempt_" + "1" * 32,
        wiki_url="https://wiki.invalid",
        wiki_capability="wiki-capability",
        manifest={"dsl": "cuda"},
    )


@pytest.mark.parametrize("enabled", [None, False, 1, "true"])
def test_non_true_service_never_exposes_wiki(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, enabled: object
) -> None:
    _live_contract(tmp_path, monkeypatch, enabled=enabled)
    assert not wiki_enabled()
    assert "wiki-query" not in runtime_tools._available_attempt_commands()
    assert "wiki-query" not in project_contract(allow_baseline=False)[1]["tools"]
    with pytest.raises(RuntimeError, match="disabled"):
        runtime_tools.wiki_query(_context(tmp_path), {"query": "load constraints"})


@pytest.mark.parametrize(
    "binding",
    [
        None,
        {},
        {"kind": "gateway", "operation": "wiki_query"},
        {"kind": "runtime-query", "operation": "evaluate"},
    ],
)
def test_service_requires_exact_query_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, binding: dict[str, str] | None
) -> None:
    _live_contract(tmp_path, monkeypatch, binding=binding)
    assert not wiki_enabled()
    assert "wiki-query" not in runtime_tools._available_attempt_commands()


def test_absent_contract_does_not_enable_from_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ATREX_RUNTIME_CONTRACT_PATH", raising=False)
    monkeypatch.setenv("ATREX_WIKI_PROXY_URL", "https://wiki.invalid")
    monkeypatch.setenv("ATREX_WIKI_CAPABILITY", "wiki-capability")
    assert not wiki_enabled()
    with pytest.raises(RuntimeError, match="disabled"):
        runtime_tools.wiki_query(_context(tmp_path), {"query": "load constraints"})


def test_enabled_wiki_help_and_discovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _live_contract(tmp_path, monkeypatch, binding=_BINDING)
    assert wiki_enabled()
    with pytest.raises(SystemExit) as error:
        runtime_tools.main(["--help"])
    assert error.value.code == 0
    assert "wiki-query" in capsys.readouterr().out
    tool = project_contract(command="wiki-query", allow_baseline=False)[1]["tools"]["wiki-query"]
    schema = tool["request_schema"]
    assert schema["required"] == ["query"]
    assert set(schema["properties"]) == {"query"}
    assert schema["additionalProperties"] is False


@pytest.mark.parametrize("missing", ["wiki_url", "wiki_capability"])
def test_enabled_wiki_requires_proxy_and_capability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    _live_contract(tmp_path, monkeypatch, binding=_BINDING)
    context = _context(tmp_path)
    setattr(context, missing, None)
    with pytest.raises(RuntimeError, match="capability is unavailable"):
        runtime_tools.wiki_query(context, {"query": "load constraints"})


@pytest.mark.parametrize(
    "query_request",
    [{}, {"query": " \n "}, {"query": 1}, {"query": "load", "attempt_id": "override"}],
)
def test_query_rejects_invalid_and_runtime_owned_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, query_request: dict[str, Any]
) -> None:
    _live_contract(tmp_path, monkeypatch, binding=_BINDING)
    with pytest.raises(ValueError):
        runtime_tools.wiki_query(_context(tmp_path), query_request)


def test_query_uses_scoped_proxy_and_idempotent_reconnects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _live_contract(tmp_path, monkeypatch, binding=_BINDING)
    context = _context(tmp_path)
    requests: list[Request] = []
    content = {
        "query_id": "query-1",
        "records": {"internal_gpu_wiki::record": {"wiki_identity": {"store": "internal"}}},
        "notes": ["historical measurement, not rerun"],
    }

    def exchange(request: Request) -> dict[str, Any]:
        requests.append(request)
        if len(requests) == 1:
            raise TimeoutError("read timed out")
        return {"content": content, "snapshot_digest": "audit-only"}

    monkeypatch.setattr(runtime_tools, "_exchange", exchange)
    result = runtime_tools.wiki_query(context, {"query": "load constraints"})
    assert result is content
    assert requests[0] is requests[1]
    assert requests[0].full_url == "https://wiki.invalid/v1/wiki/query"
    assert requests[0].get_header("Authorization") == "Bearer wiki-capability"
    assert isinstance(requests[0].data, bytes)
    payload = json.loads(requests[0].data)
    assert payload == {
        "schema_version": 1,
        "attempt_id": context.attempt_id,
        "query": "load constraints",
        "idempotency_key": runtime_tools._idempotency_key(
            "wiki",
            {"schema_version": 1, "attempt_id": context.attempt_id, "query": "load constraints"},
        ),
    }


@pytest.mark.parametrize("phase", ["optimization_attempt", "framework_baseline"])
def test_cli_queries_in_each_session_phase(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    phase: str,
) -> None:
    _live_contract(tmp_path, monkeypatch, binding=_BINDING)
    context = _context(tmp_path)
    monkeypatch.setenv("ATREX_CORE_PHASE", phase)
    context_type = (
        RuntimeLineageBootstrapContext
        if phase == "framework_baseline"
        else RuntimeAttemptContext
    )
    monkeypatch.setattr(context_type, "from_environment", lambda: context)
    (tmp_path / "scratch/query.json").write_text(json.dumps({"query": "load constraints"}))
    content = {"query_id": "q1", "records": {}, "notes": ["No matching record"]}
    monkeypatch.setattr(runtime_tools, "_post", lambda *_args: {"content": content})
    assert runtime_tools.main(["wiki-query", "--request", "scratch/query.json"]) == 0
    assert json.loads(capsys.readouterr().out) == content


def test_malformed_wiki_response_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _live_contract(tmp_path, monkeypatch, binding=_BINDING)
    monkeypatch.setattr(runtime_tools, "_post", lambda *_args: {"content": []})
    with pytest.raises(RuntimeError, match="no Agent-readable content"):
        runtime_tools.wiki_query(_context(tmp_path), {"query": "load constraints"})


@pytest.mark.parametrize(
    "modules", [(), ("directions",), ("experiments",), ("directions", "experiments")]
)
@pytest.mark.parametrize("enabled", [False, True])
def test_wiki_instructions_match_switch_in_both_session_phases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, modules: tuple[str, ...], enabled: bool
) -> None:
    _live_contract(
        tmp_path,
        monkeypatch,
        enabled=enabled,
        binding=_BINDING if enabled else None,
        modules=modules,
    )
    config = AgentConfig.load(CORE_ROOT)
    for phase in (attempt, lineage_bootstrap):
        prompt = phase.render_system_prompt(_context(tmp_path), config)
        assert ("wiki-query --request scratch/wiki-query.json" in prompt) == enabled
        assert ("Historical measurements and interpretations" in prompt) == enabled
        assert ("Preserve exact Record IDs" in prompt) == enabled


def test_wiki_cli_runs_in_foreground_like_other_runtime_tools() -> None:
    output = hook_output(
        {
            "tool_name": "Bash",
            "tool_input": {
                "command": (
                    "python3 agent/optimizer/src/runtime_tools.py "
                    "wiki-query --request scratch/q.json"
                ),
                "run_in_background": True,
            },
        },
        259_200_000,
    )
    assert output is not None
    updated = output["hookSpecificOutput"]["updatedInput"]  # type: ignore[index]
    assert updated["run_in_background"] is False
    assert updated["timeout"] == 259_200_000
