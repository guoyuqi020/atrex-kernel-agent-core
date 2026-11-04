---
name: runtime-records
description: Record research Experiments and submit an Episode Report; Direction recording is disabled.
allowed-tools: "Bash Read"
---

# Runtime Experiment records

Experiment recording is enabled; Direction commands are disabled. Start with root `PROGRESS.md`, then open only relevant `experiments/<experiment_id>/experiment.json` and `records/*.json`. These Runtime projections are read-only.

Record each decisive measurement or research conclusion with `name`, concrete `intent`, and bounded `interpretation`; omit `direction_id`. `gateway_record_ids` and archived `references` are independent optional lists. Keep execution facts in Records and source facts in Git rather than copying them into analysis. Continue bounded edit/measurement/revert-or-keep cycles while useful work remains.

To correct an archived local Experiment, resubmit its required fields with the projected
`experiment_id`. Runtime keeps the stable ID, increments `revision`, reruns Review, and updates the
latest projection; omitted optional evidence is preserved. Shared peer Experiments are read-only.

For `candidate_ready`, set `selected_experiment_id` to a current-Episode Experiment that cites a passing standard full Evaluate of the exact clean HEAD. Follow [the exact Experiment, adoption, and report schemas](references/journal.md) and [saved result/source semantics](references/records.md). Never invent IDs or measured results.
