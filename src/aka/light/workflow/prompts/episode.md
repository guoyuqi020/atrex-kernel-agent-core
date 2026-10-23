# Kernel optimization episode {{EPISODE}}

Optimize the candidate described in `WORKSPACE.md` through evidence-backed engineering.
Continue investigating, implementing, measuring, and repairing while a concrete next step
remains; one failed compile or benchmark is not, by itself, a reason to stop.

## Context

- Workspace: `{{WORKSPACE}}`
- Canonical version produced by the supervisor: `v{{VERSION}}`
- Platform: `{{PLATFORM}}`
- Framework: `{{FRAMEWORK}}`
- Additional constraints: {{NOTES}}
{{AGENT_RUNTIME}}

{{PLUGINS}}

{{RESUME_DIRECTIVE}}

{{GOAL_DIRECTIVE}}

Read `WORKSPACE.md` for editable paths, protected files, reusable and temporary storage,
this Episode's inheritance policy, and access to historical evidence.

{{MODE_POLICY}}

{{EVALUATOR}}

{{HARDWARE}}

{{SANDBOX}}

{{ACCEPTANCE_REQUEST}}

{{CONVERSION_DIRECTIVE}}

## Evidence and next decision

Separate measured facts from hypotheses: cite the saved result that establishes a fact;
label a proposed bottleneck or causal explanation as unverified until tested.
Before changing the candidate, identify the next concrete edit or probe, the expected
observable effect, and the result that would justify keeping, repairing, or restoring it.
Use the smallest useful test for the uncertainty, then ordinary full Evaluate for nomination.
Missing metrics and infrastructure failures do not refute a Kernel hypothesis.

Keep this reasoning concise in the conversation or enabled Journal; no separate draft,
plan, or profile document is required. Once the next step is actionable, implement or
measure rather than expanding the plan. Wait for each Runtime call to finish before
editing the measured source or starting another task; follow the waiting and recovery
rules in `CLAUDE.md` and the relevant tool Skill.

{{RESEARCH_WORKFLOW}}

{{WIKI_DIRECTIVE}}

{{RUNTIME_JOURNAL_CONTRACT}}

## Terminal contract

Choose an evidence-backed state:

1. `candidate_ready`: {{REPORT_SELECTION}}
   The Supervisor automatically runs or reuses the evaluator-specific acceptance check before
   accepting the report: six-case multi-seed correctness for Atrex-Bench, or the official full
   `workload.jsonl` evaluation for SOL-ExecBench. A failed check leaves the Episode open for repair
   and resubmission.
   In production, the Supervisor also runs targeted numerical probes. A measured failure returns
   distributions and metrics for repair; the same retained plan is checked after resubmission.
   Incomplete probe validation is a blocker, not evidence that the Kernel is wrong.
2. `pivot`: the engineering direction is exhausted and a fresh episode should pursue another one.
3. `blocked`: infrastructure or missing authority prevents meaningful progress.

The Supervisor decides promotion using the required ABBA comparison. It reuses a matching Runtime
record when available, otherwise requests the missing measurement; ordinary Evaluate does not
replace ABBA. You need not run ABBA just to finish. Report acceptance is not Kernel promotion.
Do not claim a speedup merely to terminate; a well-supported pivot is valid.

{{PPU_DIRECTIVE}}
