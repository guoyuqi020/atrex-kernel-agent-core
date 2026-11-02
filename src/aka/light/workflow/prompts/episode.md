# Kernel optimization episode {{EPISODE}}

## Context

- Workspace: `{{WORKSPACE}}`
- Current Kernel: v{{VERSION}}
- Platform: `{{ARCH}}`
{{DSL_CONTEXT}}
- Additional constraints: {{NOTES}}
{{PLUGINS}}
{{RUNTIME_WAIT_POLICY}}
{{RESUME_DIRECTIVE}}
{{GOAL_DIRECTIVE}}
{{RESEARCH_BOARD_SECTION}}
{{ACCEPTANCE_REQUEST}}
{{CONVERSION_DIRECTIVE}}
{{PPU_DIRECTIVE}}

## Workflow

1. Read `WORKSPACE.md`, the current Kernel, and only the saved records needed for the next decision. Treat prior analysis as fallible; immutable Gateway Records are measurement authority.
2. Choose the highest-value unresolved optimization or uncertainty. State a concrete change or bounded probe, its expected observable effect, and the result that would make you keep, repair, restore, or abandon it.
3. Preserve the current measured best before risky work, then implement one coherent candidate.
4. Measure through Runtime and use its Gateway Record to decide the next action.
5. Continue while a concrete high-value step remains. A promotable checkpoint or one failed candidate is not a reason to stop. When the useful path is completed, exhausted, or blocked, submit exactly one terminal handoff below.

Keep reasoning concise and act once the next step is testable.

{{RESEARCH_MODULE_RULES}}

{{HANDOFF_CONTRACT}}
