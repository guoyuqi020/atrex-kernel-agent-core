"""Agent contracts accept Runtime facts without demanding duplicate narratives."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import test_runtime_tools as helpers
from test_runtime_tools import _runtime_owned_journals as _runtime_owned_journals

import runtime_tools
from sessions.tool_module_prompts import modular_tool_instructions
from tool_contracts import tool_request_schema


@pytest.mark.parametrize("directions", [False, True])
@pytest.mark.parametrize("bootstrap", [False, True])
def test_experiment_required_fields_only_contain_unique_information(
    directions: bool,
    bootstrap: bool,
) -> None:
    schema = tool_request_schema(
        "record-experiment",
        directions_enabled=directions,
        allow_baseline=bootstrap,
    )
    assert schema is not None
    required = set(schema["required"])
    assert "evidence" not in required and "analysis" not in required
    assert ("hypothesis" in required) is not directions
    assert ("direction_id" in required) is directions
    assert "measurement_summary" not in schema["properties"]
    assert "discard" in schema["properties"]["action"]["enum"]
    direction = tool_request_schema("update-direction", experiments_enabled=not directions)
    assert direction is not None
    assert "analysis" not in direction["oneOf"][1]["required"]


def test_runtime_summary_and_null_prose_survive_report_handoff(tmp_path: Path) -> None:
    context = helpers._context(tmp_path)
    _, receipt = helpers._completed_test_experiment(context)
    state = helpers._fake_state(context)
    summary = {"schema_version": 1, "source": "runtime", "comparison": {"available": False}}
    experiment = state["experiments"][0]
    experiment.update(hypothesis=None, evidence=None, analysis=None, measurement_summary=summary)
    for event in state["direction_events"]:
        event["analysis"] = None
    request = helpers._report(str(receipt["experiment_id"]))
    published = runtime_tools.attempt_report(context, request)
    assert published["report_status"] == "candidate_ready"
    value = json.loads(context.report_path.read_text())
    assert "findings" not in value
    assert value["experiments"][0]["measurement_summary"] == summary
    assert value["experiments"][0]["hypothesis"] is None
    assert all(event["analysis"] is None for event in value["direction_events"])


@pytest.mark.parametrize(
    "modules",
    [
        frozenset(),
        frozenset({"directions"}),
        frozenset({"experiments"}),
        frozenset({"directions", "experiments"}),
    ],
)
def test_prompt_explains_minimal_writing_and_only_enabled_tools(modules: frozenset[str]) -> None:
    template = (Path(__file__).parents[1] / "prompts/attempt-tools.md").read_text()
    prompt = modular_tool_instructions(template, "cuda", modules)
    assert "Candidate_ready requires at least one real Finding" not in prompt
    assert "compact handoff" in prompt
    if "experiments" in modules:
        assert "measurement_summary" in prompt
        assert "Analysis is optional" in prompt
    if "directions" in modules:
        assert "analysis is optional" in prompt
    else:
        assert "Supply the visible Direction ID" not in prompt
