---
name: runtime-records
description: Manage research Directions and Experiments, inspect durable evidence, and submit a validated Episode Report.
allowed-tools: "Bash Read"
---

# Runtime records

Use `python3 tools/sandbox.py`. Runtime owns immutable measurements, exact measured source, and
report validation; the Agent owns hypotheses and interpretations.

Start with `PROGRESS.md`. Open `frontier.json` only for the complete frontier, then only relevant
Direction, Experiment, and `records/<gateway_record_id>.json` files. Projections are read-only.
Gateway Records are measurement authority; all analysis is fallible.
Use `python3 tools/record.py <gateway_record_id>` for compact local queries; do not print an entire
Record or long stdout/stderr into the Session.

## Research loop

- A Direction is a consequential Kernel path or uncertainty, not necessarily an immediate source edit. It may be a change or bounded investigation; state the consequential decision it unlocks. Do not use it for Runtime/evaluator maintenance or infrastructure work. Check duplicates and rank by expected benefit and applicability. Multiple local Directions may be `in_progress`; the latest started one is the focus, while queue reordering does not change focus.
- Choose work from the Local work queue. The Shared direction pool is discovery-only: absorb
  compatible peer ideas into one local derivative and cite every source ID in
  `derived_from_direction_ids`.
- Revise a proposed or active local Direction using its stable ID and complete replacement claim.
  Restart deferred or blocked work first. Completed and abandoned Directions are terminal; continue
  them through a derived Direction.
- Direction lifecycle is `proposed --start--> in_progress`. Only `in_progress` may be completed,
  abandoned, deferred, or blocked. A supported/refuted closure cites evidence using the field selected
  by the active module contract; an unresolved abandon may close without evidence.
- Record decisive work as an Experiment with `name`, concrete `intent`, and bounded
  `interpretation`. Records and archived references are independent optional evidence. Keep measured
  facts in Records. A note without a Record is not promotion evidence. Revise only
  `local_writable` Experiments; extend `peer_read_only` evidence with a new local Experiment.
- Use local Git for candidate history. Imported peer history is available under `refs/atrex/shared/<trajectory>/<epoch>/head`; inspect it with `git log`/`git diff`, or continue it with `git switch -c <branch> <ref>`. Commit or restore local work before switching.
- Continue bounded probe/edit/measurement/revert-or-keep cycles while the selected path has a
  concrete high-value step. Close a terminal Direction with the evidence required by the active
  module contract.
- Runtime returns the full Review once with the mutation result. `archive_status` reports storage,
  `claim_review_quality` reports scientific support, and `evidence_eligible` controls terminal use.
  `runtime_action` is workflow guidance; `scientific_next_check` is the evidence follow-up. Only projected
  `claim_review_quality`, `scope_safe_interpretation`, and `scientific_next_check` remain Agent-visible afterward; full receipts and reviewer
  conversations are Supervisor-private. Partial or unreviewed claims remain trajectory-local and
  cannot support terminal conclusions or handoff.

Submit `episode-report` only under the Session Prompt's handoff conditions. A `candidate_ready`
report requires a passing standard full Evaluate of the exact clean HEAD. Report acceptance is a
handoff, not promotion.

- [Research mutations and report schemas](references/journal.md)
- [Saved results and source](references/records.md)
- New measurements: `skills/gpu-measurement/SKILL.md`
- Knowledge queries, when installed: `skills/KernelWiki/SKILL.md`
