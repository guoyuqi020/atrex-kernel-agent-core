---
name: runtime-records
description: Record research Directions and Experiments, query saved evidence, and submit a validated Episode Report through the Supervisor.
allowed-tools: "Bash Read"
---

# Runtime records

Use `python3 tools/sandbox.py` inside a controller-registered Long Horizon Episode.
Framework Baseline has its own smoke-and-exit procedure, not an Episode report.
All Episode outcomes require valid report fields and no in-progress Directions.

New Long Horizon Episodes use Runtime Journal. Do not call legacy append/finalize, manage Git,
or write control handoffs. Leave the selected measured source tree in place, including the
editable `adapter.py` entry point; the Supervisor creates the commit and decides acceptance independently.

1. At the beginning of each Episode, call `list-directions` for the compact catalog, then call
   `load-direction` for every visible Direction. Choose the most important Direction and explain the
   comparison in the `start` event's `analysis`. Reuse an existing Direction when its hypothesis is unchanged;
   propose a new one only when no visible Direction represents the intended work.
2. Record decisive Experiments promptly. Use `hypothesis` for this change's concrete, falsifiable
   prediction; provide the actual `change`, `gateway_record_ids`, and your `analysis`. The cited
   Gateway Records are the measurement facts; use `record-read` when needed rather
   than copying their results into the Experiment.
3. If work reveals a distinct promising idea, refresh `list-directions`, load likely name/scope
   matches, and compare their hypotheses. Reuse an existing Direction for the same hypothesis and
   record implementation variants as Experiments. Otherwise propose the new Direction immediately,
   but leave it proposed while the current Direction remains active.
4. Close the Direction with an explicit hypothesis assessment and supporting Experiment IDs.
5. Prepare the report incrementally. Close all active Directions before `episode-report`.
   Correct validation errors and resubmit. An accepted report is a handoff, not promotion.

The link is Direction → Experiment → Gateway Record → exact Kernel. Propose as many useful
nonduplicate ideas as needed; start at most three distinct Directions per Episode and only one at
a time. Proposing does not start a Direction or consume a start slot. List exports are snapshots
under `scratch/`; rerun list before checking a new idea for duplication. Current and finalized
earlier Runtime Journals are visible through the tools, including unsuccessful explorations.
Legacy Journals are not automatically converted into Direction/Experiment IDs.

- [Journal requests and worked example](references/journal.md): seven Journal/report commands.
- [Saved results and source](references/records.md): measurement readers, per-Shape historical best, and errors.
- For new measurements use `skills/gpu-measurement/SKILL.md`; for knowledge queries use
  `skills/KernelWiki/SKILL.md`.

Never invent IDs or measured results. Read an existing result instead of repeating an identical
task. If a transport failure leaves a mutation's outcome unknown, inspect records before retrying.
Identical accepted reports can be resubmitted to repair handoff publication; different terminal
reports and further Journal mutations are rejected.
