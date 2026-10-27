---
name: runtime-records
description: Manage research Directions and submit an Episode Report; Experiment recording is disabled.
allowed-tools: "Bash Read"
---

# Runtime Direction records

Use `python3 tools/sandbox.py` inside the Episode. Available Journal commands are
`list-directions`, `load-direction`, `update-direction`, and `episode-report`. Experiment commands
are disabled. Measurement records remain available through `record-read`, `kernel-read`,
`kernel-records`, and `kernel-pareto-frontier`.

Start by exporting the catalog with `--kind list-directions --output-path scratch/directions.json`,
then load every visible entry with `--kind load-direction --record-id direction_...`. Submit
Direction JSON using `--kind update-direction --request-file scratch/direction.json`:

- `propose`: `action,name,change_scope,hypothesis,plan`; optional `stop_conditions` and genealogy.
- `start`: `action,direction_id,analysis`.
- `complete|abandon|block|defer`: `action,direction_id,analysis,hypothesis_status`.

Do not include `supporting_experiment_ids`. Keep at most one Direction in progress and close it
before reporting. Candidate reports use `status,summary,gateway_record_ids`; a cited record must be
a passing standard full Evaluate for the exact source. Pivot uses `status,summary`; blocked also
uses `blocker`. Never invent IDs or measured results.
