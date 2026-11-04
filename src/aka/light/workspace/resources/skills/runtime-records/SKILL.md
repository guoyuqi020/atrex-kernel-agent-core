---
name: runtime-records
description: Manage research Directions and Experiments, inspect durable evidence, and submit a validated Episode Report.
allowed-tools: "Bash Read"
---

# Runtime records

Use `python3 tools/sandbox.py` inside a registered optimization Episode. Runtime owns immutable
measurement facts, exact measured source, and report validation; the Agent owns hypotheses and
interpretations.

Start with root `PROGRESS.md`, then open only relevant `directions/<status>/*.json`,
`experiments/<experiment_id>/experiment.json`, and `records/<gateway_record_id>.json`. These are
read-only projections. Gateway Records are measurement authority; `PROGRESS.md`, Directions,
Experiments, and historical Conversations may contain fallible analysis.

## Research loop

- A Direction is a consequential Kernel path or uncertainty, not necessarily an immediate source edit. It may be an implementation change, capability probe, microbenchmark, correctness investigation, or performance attribution; state the consequential decision it unlocks. Do not use it for Runtime/evaluator maintenance or infrastructure work. Check for duplicates and rank by expected benefit and applicability. Multiple local Directions may be `in_progress`; keep that set intentional and identify the relevant Direction explicitly when more than one is active.
- Correct an open local Direction with `action=revise`, its stable `direction_id`, and the complete
  revised claim and plan. Runtime increments `revision` without changing lifecycle status or
  genealogy. Start a deferred, blocked, completed, or abandoned Direction before revising it.
- Record decisive work as an Experiment with `name`, concrete `intent`, and bounded
  `interpretation`. `gateway_record_ids` and archived `references` are independent optional lists.
  Do not copy measured values into the interpretation. An Experiment without a Gateway Record is a
  research note, not measured promotion evidence. To correct an archived local Experiment,
  resubmit its required fields with its `experiment_id`; Runtime keeps the ID and creates the next
  reviewed revision. Shared peer Experiments are read-only.
- Use local Git for candidate history. After materializing a measured peer Kernel, call
  `adopt-kernel` before using it as local evidence.
- Continue bounded probe/edit/measurement/revert-or-keep cycles while the selected path has a
  concrete high-value step. Close a terminal Direction with the evidence required by the active
  module contract.

Submit `episode-report` only under the Session Prompt's handoff conditions. A `candidate_ready`
report requires a passing standard full Evaluate of the exact clean HEAD. Report acceptance is a
handoff, not promotion.

- [Research mutations and report schemas](references/journal.md)
- [Saved results and source](references/records.md)
- New measurements: `skills/gpu-measurement/SKILL.md`
- Knowledge queries, when installed: `skills/KernelWiki/SKILL.md`
