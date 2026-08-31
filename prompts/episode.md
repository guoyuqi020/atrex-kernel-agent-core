# Kernel optimization attempt

Run one evidence-driven Kernel optimization attempt in a fresh Agent session. Continue while a
concrete, testable next step remains; stop with a mature candidate, an exhausted direction, or a
real external blocker.

The trusted controller owns the incumbent, public problem contract, private evaluation inputs,
workspace construction, evidence visibility, evaluation capabilities, retention, promotion, and
rollback. The controller-injected sections later in this Prompt are authoritative for the exact
workspace, Evidence view, task identity, and tool protocols.

## Execution boundary

- Modify the candidate Kernel only under `work/kernel/`. Use the injected reusable directories only
  for genuinely reusable Agent methods or tools, and use `scratch/` for temporary work.
- Give every `Model` constructor parameter a default: `check` constructs `Model()` bare and reports
  the failure as an `error` diagnostic inside a `succeeded` job.
- Candidate source importing `builtins` `cffi` `ctypes` `ftplib` `http` `importlib`
  `marshal` `multiprocessing` `os` `pathlib` `pickle` `requests` `shutil` `smtplib`
  `socket` `subprocess` `sys` `telnetlib` `urllib` is rejected before any job runs.
- A `dev` request accepts `file_paths`: workspace-relative paths, usually under `scratch/`,
  that are placed beside the candidate in the pod under their base names. Write a
  multi-line probe to `scratch/` and name it there instead of encoding it into `command`;
  a payload embedded in `command` can be truncated and cannot be reused.
- Treat controller-provided inputs, the Agent implementation, Session capture, service
  state, credentials, and evaluation material as read-only.
- Route GPU execution, compilation, JIT, correctness, benchmarking, profiling, disassembly, and
  GPU-import probes through the supplied evaluation command. Use the dedicated local commands for
  history and Artifact reads.
- Do not install dependencies, alter services or evaluation policy, reconstruct private cases, or
  use Git/chat text as the handoff mechanism.
- The injected DSL is immutable. Do not introduce a different-DSL implementation or fallback.

## Optimization workflow

### 1. Recover only relevant state

Inspect the incumbent and confirm the writable candidate initially matches it. Read the injected
Evidence in order, then use the Journal indexes to locate only the Directions and Experiments
relevant to the present bottleneck. Do not replay the entire lineage by default.

Honor the injected measurement-reuse policy. Reuse matching trusted measurements and exact source;
do not repeat work merely because an earlier Agent's interpretation may be wrong.

### 2. Choose and plan one causal hypothesis

Continue one useful visible Direction or create a materially distinct Direction. State a falsifiable
chain:

```text
evidence -> mechanism -> change -> expected measurable effect
```

Before editing, write a concise plan under `scratch/` containing:

- one optimization category and concrete goal;
- trusted facts, uncertain interpretations, and unsupported assumptions;
- the smallest dependency-ordered edits that test the mechanism;
- invariants, scope boundaries, risks, and rollback points;
- correctness and performance acceptance/rejection criteria; and
- measurable success and direction-exhaustion conditions.

Treat numeric targets as trends unless the trusted task input makes them hard thresholds. Reject
placeholders, hidden-case reconstruction, unrelated refactors, and plans that mix independent
optimization categories.

### 3. Localize before broad changes

Profile only when it can change the implementation decision. Start with a survey, use SOL/resource
evidence to identify the bottleneck, and request deep profiling or source correlation only for a
specific Kernel or claim. Use disassembly for generated-code questions and bounded development or
check operations for focused repair.

For multi-shape tasks, select expensive or distinct regimes only from public inputs and trusted
opaque measurements. Stop profiling once the evidence identifies an actionable mechanism and code
target.

### 4. Research progressively

Use the visible Direction and Experiment history for what this lineage already measured, and the
knowledge query command for external architecture-, DSL-, compiler-, and operator-specific facts.
This workspace carries no upstream project checkout. Preserve stable knowledge
Record IDs only for records that materially affect the work. Test every adopted recommendation;
stop research when one actionable direction has adequate support.

### 5. Implement and repair causally

Make the smallest attributable change. Keep each experiment within one category such as tiling,
vectorization, layout, pipeline staging, buffering, fusion, occupancy, or launch geometry. Avoid
unrelated cleanup and formatting.

When a call fails, preserve its structured evidence and decide whether it falsifies the mechanism
or exposes a repairable defect. Apply the smallest causal repair, restore the last viable candidate
when needed, and pivot when no evidence-based repair remains.

### 6. Validate the exact candidate

Development and check operations may accelerate repair, but a nominated candidate requires a
completed full Evaluate for the exact current `work/kernel/` tree. Require reported correctness,
finite positive latency, and credible performance evidence. An exploratory result is evidence for
the nomination; only controller policy decides whether the Kernel or Agent is retained.

Publish a mature candidate promptly. Secondary hypotheses belong to another Direction or Attempt.

### 7. Record as work proceeds

After every decisive measured keep, restoration, or direction-ending result, record the Experiment
before another edit. Keep observations in `evidence` and interpretation in `analysis`, preserve the
exact source/Trial/Result identities supplied by the tools, and record the action actually taken.
Negative results are first-class evidence.

Follow the Session-tool contracts for Direction state, Experiment linkage, incremental Report
construction, retry behavior, and terminal validation; do not reconstruct the Journal from memory
at the end.

## Terminal behavior

Stop only with a mature evaluated candidate, an exhausted or reverted direction, or a genuine
external blocker. Follow the exact terminal statuses, Direction closure rules, Finding links, and
Report schema in the Session-tool contract. Never invent correctness, performance, profiler output,
or knowledge use merely to terminate; an evidence-backed pivot is valid.
