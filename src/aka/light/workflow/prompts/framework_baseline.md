# Framework baseline

{{BASELINE_OBJECTIVE}}

## Context

- Workspace: `{{WORKSPACE}}` (your cwd)
- Baseline measurement: v{{PREV}}; candidate version: v{{N}}
- Platform: `{{ARCH}}`
{{DSL_CONTEXT}}
- Additional constraints: {{NOTES}}
{{PLUGINS}}
{{RUNTIME_WAIT_POLICY}}
{{CORRECTNESS_GUIDANCE}}

## Workflow

1. Read `README.md`, the current Kernel source tree, and the public contract/reference files. Read immutable `metadata.json` when present. For generalized native tasks, `public_shapes.json` exposes exact parameters for only the canonical first five Shapes; do not search for hidden evaluator inputs.
2. Research only implementation-critical missing facts. When `KernelWiki` is enabled, follow `skills/KernelWiki/SKILL.md`; otherwise use the public contract and compiler evidence instead of inventing facts.
3. Implement one complete candidate in `kernel.py` and optional files under `implementation/`. If this is a recovery Session, inspect and repair the candidate already on disk using the Supervisor diagnostics instead of starting over.
4. Leave the complete Kernel source tree in place and stop. The Supervisor performs validation. Do not submit `episode-report`, write canonical memory, or commit. If technically blocked, state the concrete blocker.
