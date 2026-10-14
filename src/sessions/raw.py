"""Raw optimization: direct Agate, self-authored memory, no Runtime tool handoff."""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from agent_config import AgentConfig
from contexts.common import correctness_policy_value, json_object_file
from .common import execute_agent_session
from .operator_contract import public_operator_contract


@dataclass(frozen=True)
class RawContext:
    workspace: Path
    token_usage_path: Path
    session_trace_path: Path | None
    usage_unit: str
    usage_budget: float
    timeout_seconds: float
    manifest: Mapping[str, Any]

    @classmethod
    def from_environment(cls) -> RawContext:
        if os.environ.get("ATREX_CORE_PHASE") != "optimization_attempt":
            raise ValueError("Raw requires optimization_attempt phase")
        path = Path(os.environ["ATREX_ATTEMPT_MANIFEST"])
        manifest = json_object_file(path, "Raw Attempt manifest")
        workspace = path.resolve().parent.parent
        if path.resolve() != workspace / ".runtime/attempt.json" or manifest.get("schema_version") != 9:
            raise ValueError("Invalid Raw Attempt manifest")
        def scoped(name: str) -> Path:
            value = Path(os.environ[name]).resolve()
            if not value.is_relative_to(workspace):
                raise ValueError(f"{name} escapes Raw workspace")
            return value
        budget = float(os.environ["ATREX_USAGE_BUDGET"])
        timeout = float(os.environ["ATREX_SESSION_TIMEOUT_SECONDS"])
        if not all(math.isfinite(v) and v > 0 for v in (budget, timeout)):
            raise ValueError("Raw budgets must be finite and positive")
        return cls(workspace, scoped("ATREX_TOKEN_USAGE_REPORT"),
                   scoped("ATREX_SESSION_TRACE_PATH") if os.environ.get("ATREX_SESSION_TRACE_PATH") else None,
                   os.environ["ATREX_USAGE_UNIT"], budget, timeout, manifest)


SYSTEM_PROMPT = """Optimize the supplied kernel by operating Agate directly from the shell or its Python SDK.
No Runtime tools are available. Inspect the installed client using:
  "$ATREX_AGATE_PYTHON" -m atrex_gateway_client.cli --help
  "$ATREX_AGATE_PYTHON" -m atrex_gateway_client.cli eval --help
The deployment sets AGATE_URL and authentication environment variables; use them without printing
or saving credentials. You choose requests, uploads, polling, retries and how to inspect responses.
For SDK access use the same endpoint/auth environment; do not use a different service.
Use the GPU target from the task context (Agate GPU selectors have no vendor prefix or spaces).
Build custom inputs/reference scripts from the public operator contract when required by the API;
the controller's private evaluator files are unavailable. An accepted job is not a passing result.

Start from input/kernel and edit work/kernel. Preserve operator semantics, input side effects,
numerical tolerances and required DSL. Source packaging rules are in .runtime/source-instructions.md
when present. Leave your chosen final implementation in work/kernel before ending the session.
There is no mandatory report schema or report submission command.

You manage all memory yourself: save chosen job IDs, responses, source versions, notes and scripts
under tools/ (for example tools/memory/). Only those self-authored files carry into your next Session
on this trajectory. scratch/ and work/ are temporary; work/kernel starts from the controller-selected
kernel each Session. Runtime does not supply Bootstrap reports, old conversations, summaries,
Kernel Trail, Experiment/Direction journals, Wiki, or other trajectories' notes. You may choose
what to record and in which format. Do not expect forgotten results to be restored automatically.
The controller scores the final source privately after exit; your own measurements are not its score.
"""


def run() -> int:
    context = RawContext.from_environment()
    repository = context.workspace / "agent/optimizer"
    config = AgentConfig.load(repository, workspace=context.workspace)
    problem = json_object_file(context.workspace / ".runtime/agent-problem.json", "public operator contract")
    task = context.manifest["context"]
    trusted = {"dsl": context.manifest["dsl"], "operator": task["operator"],
               "hardware_target": task["hardware_target"],
               "correctness_policy": correctness_policy_value(json.loads(os.environ["ATREX_CORRECTNESS_POLICY_JSON"]))}
    prompt = public_operator_contract(problem) + "\n\nTask context:\n" + json.dumps(trusted, ensure_ascii=False)
    return execute_agent_session(context, config, prompt, system_prompt=SYSTEM_PROMPT)
