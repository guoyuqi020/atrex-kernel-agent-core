---
name: runtime-records
description: Inspect saved evidence and submit an Episode Report without Direction or Experiment recording.
allowed-tools: "Bash Read"
---

# Runtime records — report-only mode

Direction and Experiment recording are disabled; automatic Gateway Records, duplicate detection, validation, and promotion remain enabled. Start with root `PROGRESS.md` and relevant `records/*.json`. Do not call Direction or Experiment operations or invent their IDs.
Read or query relevant `records/<gateway_record_id>.json` files directly, selecting only needed
fields or bounded text; do not print complete Records or long streams into the Session.

Use local Git for candidate history. Before every Kernel-bound Runtime request, commit the complete SourceProvider candidate and leave the worktree clean; the full HEAD is its `kernel_commit_id`. Continue bounded edit/measurement/revert-or-keep cycles while the selected path has a concrete high-value next step. A promotable checkpoint alone is not a stop condition.

For `candidate_ready`, cite real `gateway_record_ids`, including a passing standard full Evaluate of the exact clean HEAD. `pivot` and `blocked` reports omit them; a blocker also supplies `blocker`. Follow [the exact report schema and repair rules](references/journal.md) and [saved result/source semantics](references/records.md). Report acceptance is a handoff, not promotion.

Framework Baseline is Episode 0. Use the same Git and Runtime measurement loop, then submit the ordinary `episode-report` after a correctness-passing standard full Evaluate. Runtime applies the ordinary final-candidate gates and records the accepted V1 in Research Store; Episode 0 does not require ABBA or a speedup over V0.
