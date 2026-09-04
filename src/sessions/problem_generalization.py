"""Run one managed problem-generalization session."""

from __future__ import annotations

import json

from agent_config import AgentConfig
from contexts.problem_generalization import RuntimeProblemGeneralizationContext

from .common import atomic_json, execute_agent_session, guarded_main

_OUTPUT_SCHEMA_VERSION = "atrex.agent_problem.v1"
_OUTPUT_FIELDS = {
    "objective",
    "evaluation",
    "operator_contract",
    "workload_profile",
    "distribution_profile",
    "shape_domain",
    "invariants",
    "coverage_regimes",
    "development_cases",
}
_EVALUATION_FIELDS = {
    "exact_cases",
    "correctness_requirement",
    "performance_requirement",
    "development_cases_are_evaluation_cases",
}


def render_prompt(
    context: RuntimeProblemGeneralizationContext,
    config: AgentConfig,
) -> str:
    base = config.prompt_path("problem_generalization").read_text(encoding="utf-8").rstrip()
    trusted = {
        key: context.manifest[key]
        for key in (
            "dsl",
            "operator",
            "hardware_target",
        )
    }
    appendix = (
        "## Trusted task context\n\n```json\n"
        + json.dumps(trusted, ensure_ascii=False, sort_keys=True, indent=2)
        + "\n```\n\nThe only accepted output path is `work/output/agent_problem.json`."
    )
    return base + "\n\n" + appendix + "\n"


def finalize_output(context: RuntimeProblemGeneralizationContext) -> None:
    """Attach controller-owned protocol metadata after successful generation."""
    try:
        value = json.loads(context.output_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError("generated problem contract is not valid JSON") from error
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError("generated problem contract must be a JSON object")
    if "schema_version" in value:
        raise ValueError("generated problem contract must not set protocol metadata")
    if set(value) != _OUTPUT_FIELDS:
        raise ValueError("generated problem contract fields do not match the protocol")
    objective = value.get("objective")
    if not isinstance(objective, str) or not objective.strip():
        raise ValueError("generated problem objective must be non-empty text")
    evaluation = value.get("evaluation")
    if not isinstance(evaluation, dict) or set(evaluation) != _EVALUATION_FIELDS:
        raise ValueError("generated problem evaluation fields do not match the protocol")
    if (
        evaluation.get("exact_cases") != "private"
        or evaluation.get("development_cases_are_evaluation_cases") is not False
    ):
        raise ValueError("generated problem evaluation privacy fields are invalid")
    for field in ("correctness_requirement", "performance_requirement"):
        text = evaluation.get(field)
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"generated problem evaluation {field} must be non-empty text")
    for field in (
        "operator_contract",
        "workload_profile",
        "distribution_profile",
        "shape_domain",
    ):
        if not isinstance(value.get(field), dict):
            raise ValueError(f"generated problem {field} must be an object")
    invariants = value.get("invariants")
    if (
        not isinstance(invariants, list)
        or not invariants
        or any(not isinstance(item, str) or not item.strip() for item in invariants)
    ):
        raise ValueError("generated problem invariants must contain non-empty text")
    for field in ("coverage_regimes", "development_cases"):
        items = value.get(field)
        if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
            raise ValueError(f"generated problem {field} must be an array of objects")
    atomic_json(
        context.output_path,
        {"schema_version": _OUTPUT_SCHEMA_VERSION, **value},
    )


def run() -> int:
    context = RuntimeProblemGeneralizationContext.from_environment()
    config = AgentConfig.load(context.repository, workspace=context.workspace)
    return execute_agent_session(
        context,
        config,
        render_prompt(context, config),
        on_success=lambda: finalize_output(context),
    )


def main() -> int:
    return guarded_main(run)
