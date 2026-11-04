---
name: runtime-records
description: Manage research Directions and submit an Episode Report; Experiment recording is disabled.
allowed-tools: "Bash Read"
---

# Runtime Direction records

Direction recording is enabled; Experiment commands are disabled. Start with root `PROGRESS.md`, then open only relevant `directions/<status>/*.json` and `records/*.json`. These Runtime projections are read-only.

A Direction is a consequential Kernel path or uncertainty, not Runtime/evaluator maintenance. Check for duplicates and rank by expected benefit and applicability. Multiple local Directions may be `in_progress`; keep that set intentional and address updates by ID. Continue bounded probe/edit/measurement/revert-or-keep cycles while useful work remains.

Use `update-direction`, `progress-direction`, and `prioritize-direction` for mutations. Omit `supporting_experiment_ids`; Experiment-derived genealogy is unavailable. For `candidate_ready`, cite `gateway_record_ids` containing a passing standard full Evaluate of the exact clean HEAD. Follow [the exact Direction, adoption, and report schemas](references/journal.md) and [saved result/source semantics](references/records.md). Never invent IDs or measured results.

Revise an open local Direction with `action=revise`, its `direction_id`, and the complete revised
claim and plan. Runtime keeps the ID, increments `revision`, reruns Review, and preserves lifecycle
status and genealogy. Shared peer Directions are read-only.
