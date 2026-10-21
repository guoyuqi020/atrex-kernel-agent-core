"""Instructions for the Runtime-controlled Dev-only ablation."""
from __future__ import annotations

from runtime_contract import dev_only_enabled as dev_only_enabled


def dev_only_tools(dsl: str) -> str:
    return f"""## Session tools ({dsl}, Dev-only)

Only four Runtime CLI commands are available:
- runtime-contract: discover current schemas and limits locally.
- gateway-execute: accepts only operation=dev for GPU work.
- kernel-artifact-read: retrieve visible Kernel source by artifact digest.
- attempt-report: submit the final candidate or a factual pivot/blocked report.

Inspect the Dev schema before building an unfamiliar request:
```bash
python3 agent/optimizer/src/runtime_tools.py runtime-contract --tool gateway-execute --operation dev --output scratch/dev-contract.json
```
Create scratch/probe.py for your custom probe. A minimal request is:
```json
{{"operation":"dev","command":"python3 probe.py","file_paths":["scratch/probe.py"],"intent":"custom_harness"}}
```
Save it as scratch/dev.json and run:
```bash
python3 agent/optimizer/src/runtime_tools.py gateway-execute --request scratch/dev.json
```
The CLI uploads current work/kernel with additional file_paths under their distinct basenames.
Do not shadow candidate paths. Runtime supplies task identity, GPU selection and credentials.
Use Dev for custom compilation, correctness, timing, profiling and environment inspection. Make
inputs from the public contract. Record input coverage, reference calculation, numerical checks,
warmup, synchronization and timing methodology. Inspect actual stdout/stderr as well as job status;
exit code zero alone proves neither correctness nor speed. Wait for the same running request;
do not launch duplicates to work around a slow backend. The live limits govern timeout/recycle.

For a known Kernel digest in your visible history, save this source-read request under scratch:
```json
{{"kernel_artifact_digest":"sha256:<digest>","artifact_file":"kernel.py","file":"scratch/historical-kernel.py"}}
```
Run `python3 agent/optimizer/src/runtime_tools.py kernel-artifact-read --request scratch/source.json`.
Here artifact_file is the path inside that Kernel artifact; file is the local scratch destination.
This reads source, not measurements, and cannot access another isolated trajectory's artifacts.
Result Artifact, Pareto, Journal and Wiki queries remain disabled. Save relevant returned output
in your own notes; authorized historical reports/conversations remain readable as files.
Dev does not produce an authoritative full-workload score. Do not claim it does.

Discover the terminal schema with runtime-contract --tool attempt-report. The CLI automatically supplies empty tool_modules,
experiments and direction_events; do not add them to the Agent request. Use only real observations and real citation IDs;
the report contains only status, final_candidate, contributing_result_artifact_digests and blocker.
Keep custom profiling analysis in your own notes; no analysis or Findings are requested in the
handoff. Nominate
current work/kernel with candidate_ready when ready; no prior Agent Evaluate is required in this
arm. Runtime performs private validation after handoff and decides retention. Submit via:
```bash
python3 agent/optimizer/src/runtime_tools.py attempt-report --request scratch/report.json
```
Repair reported schema errors and finish only after a registered receipt. Chat text is not a report.
"""


def dev_only_workflow() -> str:
    return """# Optimize the current Kernel through Dev

Read the public operator contract, trusted hardware context, current source and only relevant
visible history. Choose a concrete optimization hypothesis and implement it in work/kernel while
preserving required semantics, numerical tolerance and input side effects. Use the selected DSL.
Use custom Dev scripts to test hypotheses, repair failures and measure changes on representative
public-contract inputs. Inspect source/compiler evidence when useful. Keep claims scoped to the
actual tests and distinguish observations from explanations. Do not infer whole-workload success
from a partial probe. Do not modify trusted prompts, skills, contract, evaluator or credentials.
Keep temporary files in scratch and reusable executable helpers in tools with an up-to-date index.

Iterate within the Session budget. At handoff submit a candidate_ready Report for the exact final
candidate, or pivot for an exhausted/reverted hypothesis, or blocked for a genuine external blocker.
No Agent full-evaluation prerequisite applies to this Dev-only arm. Runtime privately evaluates
nominated source and controls acceptance. Do not wait for its post-handoff result. Keep all Journal
arrays empty; preserve observations in your own notes and never fabricate evidence.
"""
