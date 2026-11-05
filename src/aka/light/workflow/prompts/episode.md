# Kernel optimization episode {{EPISODE}}

## Context

- Workspace: `{{WORKSPACE}}`
- Supervisor comparison base: `{{BASE_COMMIT}}`; measurable candidate identity: clean Git HEAD
- Campaign record slot: v{{VERSION}}
- Hardware: `{{ARCH}}`
{{DSL_CONTEXT}}
- Additional constraints: {{NOTES}}
{{PLUGINS}}
{{RUNTIME_WAIT_POLICY}}
{{RESUME_DIRECTIVE}}
{{GOAL_DIRECTIVE}}
{{ACCEPTANCE_REQUEST}}
{{CONVERSION_DIRECTIVE}}
{{PPU_DIRECTIVE}}

## Workflow

1. Read `CLAUDE.md`, `WORKSPACE.md`, root `PROGRESS.md`, `skills/runtime-records/SKILL.md`, and the current Kernel. They define the stable policy, editable source, visible frontier, enabled research modules, and report schema. Open only the Direction, Experiment, and Gateway Record files needed for the next decision. Historical Conversation is a last resort: read a specific Session only when Git, PROGRESS, saved research, and Records cannot recover a concrete rationale. Prior Agent analysis is fallible; Gateway Records are measurement authority.
2. Choose the unresolved optimization path with the highest plausible full-workload latency impact and applicability, not merely the easiest edit. An optimization path may be an implementation approach or a consequential uncertainty. Use implementation risk to design a bounded probe, not to discard a higher-upside path. State the hypothesis, concrete change or probe, expected observation, measurement, and keep/repair/revert/abandon condition.
3. Use local Git to manage the Kernel development history and preserve the measured best. Before every Kernel-bound Runtime request, commit the complete candidate and leave the worktree clean. Run the smallest measurement that resolves the current question, inspect its saved Record, then keep, repair, or restore the candidate from evidence.
4. Repeat `probe → candidate → measurement → revert/keep` cycles in this Episode while a concrete high-value next step remains. A promotable checkpoint or one failed candidate is not a reason to stop.
5. Submit `episode-report` only when the selected path is completed or exhausted, pooled search must publish a reusable measured checkpoint to peers, or a concrete blocker prevents further work. `cross_trajectory_share` is available only when this Prompt begins with a `Search location` header. Follow the installed handoff contract and stop only after Runtime accepts the report.

Keep reasoning concise and act once the next step is testable.

{{RESEARCH_MODULE_RULES}}

{{HANDOFF_CONTRACT}}

Write `scratch/episode-report.json` and submit it with `python3 tools/sandbox.py --kind episode-report --request-file scratch/episode-report.json`. Correct a rejected report only while its handoff condition remains true. Report acceptance is a handoff, not promotion; independent verification follows.
