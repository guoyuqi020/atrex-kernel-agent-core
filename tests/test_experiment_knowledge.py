from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
import test_runtime_tools as helpers
from test_runtime_tools import _runtime_owned_journals as _runtime_owned_journals

import runtime_tools
from runtime_tools import runtime_journal as real_runtime_journal
from sessions.tool_module_prompts import modular_tool_instructions
from tool_contracts import tool_request_schema

_KNOWLEDGE = [
    {
        "record_id": "internal_gpu_wiki::vector-load-alignment",
        "finding": "Vector loads require suitable alignment for this layout.",
        "application": "Added an alignment guard before the vectorized branch.",
    }
]


@pytest.mark.parametrize("directions", [False, True])
@pytest.mark.parametrize("bootstrap", [False, True])
def test_experiment_schema_has_optional_strict_knowledge(directions: bool, bootstrap: bool) -> None:
    schema = tool_request_schema(
        "record-experiment", allow_baseline=bootstrap, directions_enabled=directions
    )
    assert schema is not None
    assert "knowledge_used" not in schema["required"]
    field = schema["properties"]["knowledge_used"]
    assert field["default"] == []
    assert field["type"] == "array"
    assert set(field["items"]["required"]) == {"record_id", "finding", "application"}
    assert field["items"]["additionalProperties"] is False
    assert all(value["pattern"] == r"\S" for value in field["items"]["properties"].values())


@pytest.mark.parametrize(
    "knowledge",
    [
        None,
        "record",
        [{}],
        [{"record_id": "x"}],
        [{"record_id": "x", "finding": " ", "application": "used"}],
        [{"record_id": 1, "finding": "fact", "application": "used"}],
        [{"record_id": "x", "finding": "fact", "application": "used", "extra": True}],
    ],
)
def test_experiment_rejects_malformed_knowledge_before_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, knowledge: object
) -> None:
    monkeypatch.setattr(
        runtime_tools, "runtime_journal", lambda *_args: pytest.fail("must not send")
    )
    with pytest.raises(ValueError, match="knowledge_used"):
        runtime_tools.record_experiment(
            helpers._context(tmp_path), {**helpers._experiment(), "knowledge_used": knowledge}
        )


@pytest.mark.parametrize("with_knowledge", [False, True])
def test_wire_hash_preserves_legacy_omission_and_explicit_citations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, with_knowledge: bool
) -> None:
    monkeypatch.delenv("ATREX_RUNTIME_CONTRACT_PATH", raising=False)
    monkeypatch.setattr(runtime_tools, "runtime_journal", real_runtime_journal)
    context = helpers._context(tmp_path)
    request = helpers._experiment()
    if with_knowledge:
        request["knowledge_used"] = deepcopy(_KNOWLEDGE)
    original = deepcopy(request)
    calls: list[dict[str, Any]] = []

    def post(_url: str, _capability: str, path: str, value: dict[str, Any]) -> dict[str, Any]:
        assert path == "/v1/runtime/journals"
        calls.append(deepcopy(value))
        return {"result": {"status": "recorded", "experiment_id": "experiment_" + "1" * 32}}

    monkeypatch.setattr(runtime_tools, "_post", post)
    runtime_tools.record_experiment(context, request)
    runtime_tools.record_experiment(context, request)
    assert request == original
    assert calls[0] == calls[1]
    assert calls[0]["request"] == original
    assert ("knowledge_used" in calls[0]["request"]) == with_knowledge
    assert calls[0]["idempotency_key"] == runtime_tools._idempotency_key(
        "runtime-journal",
        {
            "schema_version": 2,
            "attempt_id": context.attempt_id,
            "operation": "experiment_record",
            "request": original,
        },
    )


@pytest.mark.parametrize("with_knowledge", [False, True])
def test_experiment_knowledge_survives_load_index_and_terminal_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, with_knowledge: bool
) -> None:
    monkeypatch.delenv("ATREX_RUNTIME_CONTRACT_PATH", raising=False)
    context = helpers._context(tmp_path)
    direction = helpers._propose_and_start_direction(context)
    request = helpers._experiment(direction)
    if with_knowledge:
        request["knowledge_used"] = deepcopy(_KNOWLEDGE)
    receipt = runtime_tools.record_experiment(context, request)
    expected = _KNOWLEDGE if with_knowledge else []
    loaded = runtime_tools.load_experiment(context, {"experiment_id": receipt["experiment_id"]})
    assert loaded["knowledge_used"] == expected
    runtime_tools.list_experiments(context, {"file": "scratch/experiments.json"})
    indexed = json.loads((tmp_path / "scratch/experiments.json").read_text())
    assert indexed["experiments"][0]["knowledge_used"] == expected
    helpers._complete_direction(context, direction)
    report = runtime_tools.attempt_report(context, helpers._report(receipt["experiment_id"]))
    assert report["status"] == "published"
    recorded = helpers._REGISTERED_REPORTS[-1]
    assert recorded["experiments"][0]["knowledge_used"] == expected
    # Normalizing historical records must not rewrite the frozen input objects.
    assert ("knowledge_used" in helpers._fake_state(context)["experiments"][0]) == with_knowledge


@pytest.mark.parametrize("knowledge", [None, [{"record_id": "record", "finding": "fact"}]])
def test_snapshot_rejects_malformed_knowledge(tmp_path: Path, knowledge: object) -> None:
    context = helpers._context(tmp_path)
    helpers._completed_test_experiment(context)
    entry = deepcopy(helpers._fake_state(context)["experiments"][0])
    entry["knowledge_used"] = knowledge
    with pytest.raises(ValueError, match="knowledge_used"):
        runtime_tools._validate_experiment_entries([entry], "journal", allow_baseline=False)


@pytest.mark.parametrize(
    "modules",
    [
        frozenset(),
        frozenset({"directions"}),
        frozenset({"experiments"}),
        frozenset({"directions", "experiments"}),
    ],
)
def test_optional_knowledge_prompt_tracks_experiments_not_wiki(
    monkeypatch: pytest.MonkeyPatch, modules: frozenset[str]
) -> None:
    monkeypatch.delenv("ATREX_RUNTIME_CONTRACT_PATH", raising=False)
    template = (Path(__file__).resolve().parents[1] / "prompts/attempt-tools.md").read_text()
    prompt = modular_tool_instructions(template, "cuda", modules)
    assert ("Its optional knowledge_used defaults to []" in prompt) == ("experiments" in modules)
    assert "wiki-query" not in prompt
    if "experiments" in modules:
        assert "Historical citations need no new Wiki query" in prompt
        assert "not proof of current correctness" in prompt
