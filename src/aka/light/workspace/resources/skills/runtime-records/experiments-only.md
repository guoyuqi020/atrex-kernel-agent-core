---
name: runtime-records
description: Record research Experiments and submit an Episode Report; Direction recording is disabled.
allowed-tools: "Bash Read"
---

# Runtime Experiment records

Use `python3 tools/sandbox.py` inside the Episode. Available Journal commands are
`list-experiments`, `load-experiment`, `record-experiment`, and `episode-report`. Direction commands
are disabled. Measurement records remain available through `record-read`, `kernel-read`,
`kernel-records`, and `kernel-pareto-frontier`.

Export the catalog with `--kind list-experiments --output-path scratch/experiments.json` and load
selected entries with `--kind load-experiment --record-id experiment_...`. Record decisive work via
`--kind record-experiment --request-file scratch/experiment.json`. The JSON fields are
`name,hypothesis,change,gateway_record_ids,analysis`; omit `direction_id`. Optional Wiki attribution
fields retain their documented schema. Gateway Records own measurement facts, so use `record-read`
instead of copying them into analysis.

Candidate reports use `status,summary,selected_experiment_id`; the selected current-Episode
Experiment must cite a passing standard full Evaluate for the exact source. Pivot uses
`status,summary`; blocked also uses `blocker`. Never invent IDs or measured results.
