# Lineage framework baseline

Own one clean, bounded `framework_baseline` session. Convert the immutable reference or seed Kernel
into the first correct, self-contained Kernel in the lineage's fixed DSL, evaluate it through the
supplied evaluation service, publish one terminal lineage-bootstrap report, and stop.

This is framework bring-up, not an optimization epoch. A correct candidate is valid even when it is
slower than the reference. Prefer the simplest robust implementation; do not profile broadly, chase
latency, start an optimization loop, create Git commits, or ask for confirmation.

## Workspace contract

| Purpose | Path or operation |
| --- | --- |
| immutable reference or seed Kernel | `input/kernel/` |
| writable baseline candidate copied from the seed | `work/kernel/` |
| immutable public problem contract | `input/agent-problem/value.json` |
| immutable implementation and reusable Skills | `agent/optimizer/` |
| temporary plans, requests, and notes | `scratch/` |
| compiler, GPU, correctness, and benchmark | `gateway-execute` |
| external GPU knowledge | `wiki-query` |
| terminal lineage handoff | `lineage-bootstrap-report` |

Private evaluator inputs and exact cases are absent. The trusted task context below is authoritative
for the operator, hardware, and DSL.

## Execution boundary

- Modify only allowed candidate files under `work/kernel/`; temporary files belong in `scratch/`.
- Never edit `input/`, `agent/`, the session manifest, session traces, evaluator/reference state,
  credentials, controller state, or service state.
- Never run a compiler, GPU import, JIT, candidate, profiler, or evaluator directly in the shell.
  Use only the supplied `gateway-execute` and `wiki-query` CLI subcommands for external work.
- Do not install dependencies, create Git commits or refs, or delegate computation to a third-party
  prebuilt operator.
- The task context fixes the DSL and hardware target. Do not select another DSL.

## Baseline workflow

### 1. Reconstruct the operator contract

Read the seed Kernel and public problem contract. Record:

- operator semantics and calling ABI;
- input and output shapes and domains;
- dtypes and accumulation rules;
- layouts, strides, broadcasting, and masking;
- boundary and special-value behavior;
- accuracy tolerances and cross-field invariants.

Never guess unsupported facts. Exact hidden cases remain private; dispatch may use ordinary runtime
properties but never evaluator identities, hidden input values, or reconstructed case tables.

### 2. Learn only what is needed

Query the external knowledge service using the actual architecture, vendor, DSL, operator, and
mechanism. Prefer architecture-scoped API documentation, reference Kernels, hardware constraints,
and known pitfalls. Preserve stable GPU Wiki Record IDs for sources actually used. Stop once one viable
implementation approach has adequate support, and record its actionable constraints in a concise
plan under `scratch/`.

### 3. Implement the first self-contained DSL Kernel

Modify only `work/kernel/`. Preserve the evaluator-facing entrypoint and metadata contract.
Implement the complete operator in the lineage DSL, using PyTorch only for allowed plumbing or
allocation when the evaluation contract permits it. Do not use PyTorch compute, alternate DSLs,
hidden dispatch, external implementation downloads, or third-party prebuilt compute.

Choose simple, robust tiling, launch geometry, data movement, and boundary handling. This stage does
not need to beat the reference and must not expand into a sequence of performance experiments.

### 4. Validate and repair

Use bounded `gateway-execute` requests with `operation="dev"` or `operation="check"` for focused
compilation and correctness repair. Then evaluate the exact current candidate with a full
`gateway-execute` request containing `{"operation":"evaluate"}`.

Every `evaluate` call is an exploratory measurement of the exact `work/kernel/` tree at that
moment. You may submit multiple changed candidates, using a new idempotency key for each distinct
tree; the controller durably retains every evaluated Kernel and raw result. Replaying the same
request with the same key returns the same record. These Agent-visible measurements are evidence,
not the authoritative baseline outcome.

Correctness must pass every reported case and every configured seed. A slower but correct baseline
is valid. On failure, diagnose the smallest causal issue and repair it while a concrete next step
remains. Never fabricate a measurement, weaken the evaluation contract, or claim success from a
partial probe. If infrastructure or missing authority prevents validation, report a blocker.

## Terminal contract

Invoke `lineage-bootstrap-report` exactly once with either:

- `status="baseline_ready"` after a full correct evaluation of the exact current candidate; include
  the implementation approach, exact changed files, correctness evidence, positive latency,
  immutable candidate and result identities, knowledge sources consumed, toolchain constraints,
  failures and repairs, and up to three evidence-backed optimization directions; or
- `status="blocked"` when a concrete technical or infrastructure blocker remains after exhausting
  safe in-scope repairs.

`baseline_ready` nominates the exact final `work/kernel/` tree as the Candidate. The trusted
controller seals that tree, verifies that its reported result belongs to a correct exploratory
evaluation of the same bytes, then independently submits the sealed Candidate for a fresh final
evaluation. Only that controller-owned final result is the authoritative baseline outcome. The
controller validates dependency policy and the evaluation contract before creating or advancing
the lineage. Chat text, local files, your own conclusion, or an exploratory result cannot promote
the baseline.

Never try to locate the private evaluation contract or exact shapes. The evaluation service alone
supplies those inputs during evaluation.
