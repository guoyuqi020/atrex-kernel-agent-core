"""Run one managed framework-baseline session."""

from __future__ import annotations

import json

from agent_config import AgentConfig
from contexts.lineage_bootstrap import RuntimeLineageBootstrapContext

from .common import execute_agent_session, guarded_main


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


def _tool_instructions() -> str:
    tool = "agent/optimizer/src/runtime_tools.py"
    return f"""
## Session tools

Use the following exact CLI subcommand names; there are no function-style aliases. Write each
operation request as one JSON object under `scratch/`, then invoke:

```text
python {tool} gateway-execute --request scratch/<request>.json
python {tool} wiki-query --request scratch/<request>.json
python {tool} lineage-bootstrap-report --request scratch/<report>.json
```

`gateway-execute` attaches the exact `work/kernel` tree and trusted session identity. Never include
a candidate, schema version, capability, or operation identity in the request. Typical requests are
`{{"operation":"check"}}`, `{{"operation":"evaluate"}}`, and `{{"query":"focused GPU question"}}`.

Each `lineage-bootstrap-report` request must contain exactly these fields:

```json
{{
  "status": "baseline_ready",
  "approach": "implementation approach",
  "change_summary": "exact candidate files and changes",
  "correctness_evidence": "authoritative evaluation identity and outcome",
  "latency_us": 1.0,
  "candidate_artifact_digest": "candidate identity returned by evaluation",
  "gateway_result_digest": "result identity returned by evaluation",
  "research_sources": ["knowledge source and snapshot identity"],
  "lessons": "framework constraints, failures, and repairs",
  "next_directions": ["evidence-backed optimization direction"],
  "blocker": null
}}
```

For `status="blocked"`, set `latency_us`, `candidate_artifact_digest`, and
`gateway_result_digest` to `null`, and set `blocker` to non-empty text. Do not add other fields.
""".strip()


def render_prompt(
    context: RuntimeLineageBootstrapContext,
    config: AgentConfig,
) -> str:
    base = config.prompt_path("framework_baseline").read_text(encoding="utf-8").rstrip()
    return "\n\n".join((base, _trusted_context(context), _tool_instructions())) + "\n"


def run() -> int:
    context = RuntimeLineageBootstrapContext.from_environment()
    config = AgentConfig.load(context.repository)
    return execute_agent_session(context, config, render_prompt(context, config))


def main() -> int:
    return guarded_main(run)
