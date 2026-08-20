"""Run exactly one prepared Kernel optimization attempt."""

from __future__ import annotations

import json

from agent_config import AgentConfig
from contexts.attempt import RuntimeAttemptContext

from .common import execute_agent_session, guarded_main


def _tool_instructions(dsl: str) -> str:
    tool = "agent/optimizer/src/runtime_tools.py"
    return f"""
## Session tools

Use the following exact CLI subcommand names; there are no function-style aliases. For each call,
write one JSON request under `scratch/`, then run exactly one of:

```text
python {tool} gateway-execute --request scratch/<request>.json
python {tool} wiki-query --request scratch/<request>.json
python {tool} record-experiment --request scratch/<request>.json
python {tool} attempt-report --request scratch/<request>.json
```

`gateway-execute` automatically attaches the exact current `work/kernel` tree, the trusted Attempt
identity, and a deterministic idempotency key. Never embed a candidate, schema version, capability,
or attempt id in its request. Example profile request:

```json
{{"operation": "profile", "level": "survey"}}
```

Evaluation results identify private cases only by opaque `shape_id` and never reveal their inputs.
After an evaluation, a profile request may add `"shape_id":"<opaque id>"` to profile that one real
case; omitting it selects one evaluator-owned case. Do not infer or reconstruct case inputs from ids
or measurements.

Example authoritative evaluation request:

```json
{{"operation": "evaluate"}}
```

Example knowledge query request:

```json
{{"query": "{dsl} vectorized load requirements for the target architecture"}}
```

`wiki-query` returns the GPU Wiki's exact `records` mapping and `notes`. Each mapping key is a
stable Record ID; each value keeps its Store, source, type, scope, match, and isolated payload.
The payload is the complete safe served Record; no second read step exists. Preserve the exact
mapping keys of records that materially informed your work. Wiki protocol versions, snapshot
identities, and integrity digests are intentionally absent from Agent-facing results.

Each `record-experiment` request must contain exactly these fields:

```json
{{
  "name": "short experiment name",
  "hypothesis": "falsifiable expected mechanism",
  "change": "exact candidate change, including a reverted change",
  "evidence": "profile or evaluation result identities and observations",
  "result": "measured outcome and interpretation",
  "decision": "continue"
}}
```

`decision` must be `continue`, `revert`, or `pivot`. Each `attempt-report` request must contain
exactly these fields:

```json
{{
  "status": "candidate_ready",
  "hypothesis": "tested hypothesis",
  "bottleneck": "localized bottleneck",
  "plan": ["ordered step"],
  "change_summary": "exact final candidate change",
  "profile_evidence": "profile result identities or why profiling was unnecessary",
  "evaluation_evidence": "authoritative evaluation result identity and outcome",
  "result_interpretation": "what the measurements establish",
  "decision": "keep",
  "research_sources": ["stable GPU Wiki Record IDs actually used"],
  "lessons": ["reusable positive or negative lesson"],
  "next_directions": ["evidence-backed next direction"]
}}
```

Use only `candidate_ready`/`keep`, `pivot`/`pivot`, or `blocked`/`blocked` as the status/decision
pair. Do not include the experiment journal in the terminal request; the CLI attaches it. Do not run
GPU, compiler, JIT, profiler, or evaluator work outside these bindings.
""".strip()


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
                context.evidence_prompt.rstrip(),
                _trusted_context(context),
                _tool_instructions(dsl),
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
