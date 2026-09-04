"""Run one managed framework-baseline session."""

from __future__ import annotations

import json

from agent_config import AgentConfig
from contexts.lineage_bootstrap import RuntimeLineageBootstrapContext

from .attempt import _tool_instructions
from .common import execute_agent_session, guarded_main
from .operator_contract import public_operator_contract


def _trusted_context(context: RuntimeLineageBootstrapContext) -> str:
    value = {
        key: context.manifest[key]
        for key in (
            "dsl",
            "operator",
            "hardware_target",
        )
    }
    return (
        "## Trusted task context\n\n```json\n"
        + json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)
        + "\n```"
    )


def render_prompt(
    context: RuntimeLineageBootstrapContext,
    config: AgentConfig,
) -> str:
    base = config.prompt_path("framework_baseline").read_text(encoding="utf-8").rstrip()
    return (
        "\n\n".join(
            (
                base,
                public_operator_contract(context.agent_problem),
                _trusted_context(context),
            )
        )
        + "\n"
    )


def render_system_prompt(
    context: RuntimeLineageBootstrapContext,
    config: AgentConfig,
) -> str:
    """Carry the Session-tool contract where context compaction cannot drop it."""
    return _tool_instructions(config, str(context.manifest["dsl"]))


def run() -> int:
    context = RuntimeLineageBootstrapContext.from_environment()
    config = AgentConfig.load(context.repository, workspace=context.workspace)
    return execute_agent_session(
        context,
        config,
        render_prompt(context, config),
        system_prompt=render_system_prompt(context, config),
    )


def main() -> int:
    return guarded_main(run)
