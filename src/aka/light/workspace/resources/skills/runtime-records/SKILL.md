---
name: runtime-records
description: Manage research Directions and Experiments, inspect durable evidence, and submit a validated Episode Report.
allowed-tools: "Bash Read"
---

# Runtime records

Use `python3 tools/sandbox.py` inside a controller-managed Episode. Framework Baseline has its own
finish procedure. Runtime owns measurement facts, Kernel Artifacts and promotion; the Agent owns
research intent and interpretation.

1. Read the injected Board or run `--kind research-board`. Refresh `list-directions`, load relevant
   entries, and choose the highest-value unresolved work. Use `prioritize-direction` when queue order
   changes.
2. Propose a Direction only after checking for duplicates. Start at most one local Direction at a
   time. Peer `in_progress(Tn)` Directions remain visible and read-only.
3. Commit useful source checkpoints with local Git. Use `progress-direction` after a declared plan
   step. A major redesign may remain active across Episode and Epoch boundaries.
4. Record decisive work with `name,intent,gateway_record_ids,interpretation`. Runtime derives all
   execution facts. Use `record-read` for details.
5. After materializing a measured peer Kernel through Git or `kernel-read`, call `adopt-kernel` so
   Runtime verifies and audits the exact source identity.
6. Submit `episode-report`. Close a Direction only when its research state is actually terminal;
   active work may remain for the next Episode. An accepted report is a handoff, not promotion.

The evidence chain is Direction → Experiment → Gateway Record → immutable Kernel. The Research
Board and Git notes are bounded indexes, not truth sources. Never invent IDs or copy measurement
values into interpretations. Subset Shape evaluations are diagnostic and cannot support promotion.

- [Journal requests and examples](references/journal.md)
- [Saved results and source](references/records.md)
- New measurements: `skills/gpu-measurement/SKILL.md`
- Knowledge queries: `skills/KernelWiki/SKILL.md`
