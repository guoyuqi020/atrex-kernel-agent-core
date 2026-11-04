# Framework baseline

{{BASELINE_OBJECTIVE}}

## Context

- Workspace: `{{WORKSPACE}}` (your cwd)
- Reference version: v{{PREV}}; candidate version: v{{N}}
- Hardware: `{{ARCH}}`
{{DSL_CONTEXT}}
- Additional constraints: {{NOTES}}
{{PLUGINS}}
{{CORRECTNESS_GUIDANCE}}
{{SHAPE_DISCLOSURE}}

## Workflow

1. Read `README.md`, `CLAUDE.md`, `WORKSPACE.md`, the current candidate source, and the public contract/reference files. Read immutable `metadata.json` when present.
2. Research only implementation-critical missing facts. When `KernelWiki` is enabled, follow `skills/KernelWiki/SKILL.md`; its Wiki query is a knowledge request, not a Kernel measurement. Otherwise use the public contract and supplied static/compiler evidence instead of inventing facts.
3. Implement one complete candidate using the existing SourceProvider layout and only its declared editable paths. Keep its entry point and manifest, when present, consistent with the implementation. If this is a recovery Session, inspect and repair the candidate already on disk using the Supervisor diagnostics instead of starting over.
4. Leave the complete candidate source tree in place and stop. Do not invoke GPU measurement, diagnostic, or custom-execution Runtime operations in this stage; the optional Wiki query in step 2 remains allowed. The Supervisor validates the worktree, returns gate diagnostics to a recovery Session when repair is needed, and commits an accepted V1. Do not create a Git commit, submit `episode-report`, or write canonical memory. If technically blocked, state the concrete blocker.
