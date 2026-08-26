"""Run exactly one prepared Kernel optimization attempt."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping

from agent_config import AgentConfig
from contexts.attempt import RuntimeAttemptContext

from .common import execute_agent_session, guarded_main, public_operator_contract

_RUNTIME_TOOL = "agent/optimizer/src/runtime_tools.py"
_TEMPLATE_PLACEHOLDER = re.compile(r"\{\{([^{}\n]+)\}\}")


def _render_prompt_fragment(template: str, replacements: Mapping[str, str]) -> str:
    placeholders = set(_TEMPLATE_PLACEHOLDER.findall(template))
    expected = set(replacements)
    if placeholders != expected:
        missing = sorted(expected - placeholders)
        unknown = sorted(placeholders - expected)
        raise ValueError(
            "Prompt fragment placeholder contract mismatch: "
            f"missing={missing}, unknown={unknown}"
        )
    rendered = template
    for name, value in replacements.items():
        rendered = rendered.replace("{{" + name + "}}", value)
    if "{{" in rendered or "}}" in rendered:
        raise ValueError("Prompt fragment contains an invalid or unresolved placeholder")
    return rendered.strip()


def _tool_instructions(config: AgentConfig, dsl: str) -> str:
    template = config.prompt_fragment_path("attempt_tools").read_text(encoding="utf-8")
    return _render_prompt_fragment(
        template,
        {
            "DSL": dsl,
            "RUNTIME_TOOL": _RUNTIME_TOOL,
        },
    )


def _trusted_context(context: RuntimeAttemptContext) -> str:
    manifest = context.manifest
    task = manifest["context"]
    value = {
        "dsl": manifest["dsl"],
        "epoch_number": task["epoch_number"],
        "attempt_ordinal": task["attempt_ordinal"],
        "operator": task["operator"],
        "hardware_target": task["hardware_target"],
    }
    return (
        "## Trusted task context\n\n```json\n"
        + json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)
        + "\n```"
    )


def render_prompt(context: RuntimeAttemptContext, config: AgentConfig) -> str:
    base = config.prompt_path("optimization_attempt").read_text(encoding="utf-8").rstrip()
    dsl = str(context.manifest["dsl"])
    return (
        "\n\n".join(
            (
                base,
                public_operator_contract(context.agent_problem),
                context.evidence_prompt.rstrip(),
                _trusted_context(context),
                _tool_instructions(config, dsl),
            )
        )
        + "\n"
    )


def run() -> int:
    context = RuntimeAttemptContext.from_environment()
    config = AgentConfig.load(context.repository)
    return execute_agent_session(context, config, render_prompt(context, config))


def main() -> int:
    return guarded_main(run)
