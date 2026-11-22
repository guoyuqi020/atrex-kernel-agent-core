from copy import deepcopy

import pytest

from aka.workflow.light.host.errors import AgentRequestError
from aka.workflow.light.host.journal import record_experiment, _public_experiment
from aka.workflow.light.host.request_feedback import journal_schema
from aka.workflow.light.research import ResearchStore


def state():
    return {"episode": 1, "state": "in_progress", "tool_modules": ["experiments"],
            "central_research": {"directions": {}, "experiments": {}, "queue": []},
            "experiments": []}


def request():
    return {"name": "Output layout probe", "intent": "Compare layouts", "interpretation": "Try a new layout",
            "implementation": "Microbenchmark contiguous and strided stores",
            "expectations": [{"scope": "long sequences", "prediction": "Lower latency",
                              "observation": "No valid measurement yet", "assessment": "unresolved"}]}


def test_expectations_reach_review_archive_projection_and_revisions(tmp_path):
    current = state()
    reviewed = []
    def review(kind, target):
        reviewed.append(deepcopy(target))
        return {"claim_review_quality": "review_not_requested", "review_skipped": True}
    first = record_experiment(current, tmp_path, request(), review_claim=review)
    original = deepcopy(current["experiments"][0])
    assert reviewed[0]["expectations"] == [dict(request()["expectations"][0], prediction_origin="retrospective")]
    store = ResearchStore(tmp_path / "research")
    store.observe_experiment(original)

    revision = {k: v for k, v in request().items() if k not in {"implementation", "expectations"}}
    revision["experiment_id"] = first["experiment_id"]
    record_experiment(current, tmp_path, revision, review_claim=review)
    assert reviewed[-1]["implementation"] == original["implementation"]
    assert reviewed[-1]["expectations"] == original["expectations"]

    revision["expectations"] = [dict(request()["expectations"][0], observation="Measured regression", assessment="missed")]
    record_experiment(current, tmp_path, revision, review_claim=review)
    latest = current["experiments"][0]
    store.observe_experiment(latest)
    assert store.read()["experiments"][first["experiment_id"]]["expectations"][0]["assessment"] == "missed"
    assert store.events()[0]["payload"]["experiment"]["expectations"][0]["assessment"] == "unresolved"
    assert _public_experiment(latest)["expectations"] == [dict(revision["expectations"][0], prediction_origin="retrospective")]
    assert _public_experiment(latest)["implementation"] == original["implementation"]
    assert 'Measured regression' in str(store.board())

    revision["expectations"] = []
    record_experiment(current, tmp_path, revision)
    assert current["experiments"][0]["expectations"] == []


@pytest.mark.parametrize("bad", [None, {}, [None], [{}], [{"scope": "x", "prediction": "p", "observation": "o", "assessment": "supported"}], [{"scope": "x", "prediction": "p", "observation": None, "assessment": "unresolved"}], [{"scope": "x", "prediction": "p", "observation": "o", "assessment": "met", "latency": 1}]])
def test_invalid_expectations_do_not_record_or_review(tmp_path, bad):
    current = state()
    reviewed = []
    value = request()
    value["expectations"] = bad
    with pytest.raises(AgentRequestError):
        record_experiment(current, tmp_path, value, review_claim=lambda *args: reviewed.append(args))
    assert not current["experiments"]
    assert not reviewed


def test_exposed_schema_matches_structured_request():
    import jsonschema
    schema = journal_schema("experiment_record", {}, experiments_enabled=True, directions_enabled=False)
    jsonschema.validate(request(), schema)
    assert "direction_id" not in schema["properties"]
    assert set(schema["properties"]["expectations"]["items"]["properties"]["assessment"]["enum"]) == {"met", "missed", "unresolved"}
