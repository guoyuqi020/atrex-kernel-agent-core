# Lineage framework baseline

Own one clean, bounded `framework_baseline` session. Establish from the immutable reference or seed
the first correct, self-contained Kernel in the lineage's fixed DSL, evaluate it through the
supplied evaluation service, publish one terminal Attempt report, and stop.

This is framework bring-up, not an optimization epoch. A correct candidate is valid even when it is
slower than the reference. Prefer the simplest robust implementation; do not profile broadly, chase
latency, start an optimization loop, create Git commits, or ask for confirmation.

## Workspace contract

| Purpose | Path or operation |
| --- | --- |
| immutable reference or seed Kernel | `input/kernel/` |
| writable baseline candidate copied from the seed | `work/kernel/` |
| immutable Agent implementation and included Skills | `agent/optimizer/` |
| read-only pinned upstream GPU kernel projects | `reference/` |
| writable reusable learned Skills | `skills/` |
| writable reusable tools and required tool index | `tools/`, `tools/README.md` |
| temporary plans, requests, and notes | `scratch/` |

Private evaluator inputs and exact cases are absent. The trusted task context below is authoritative
for the operator, hardware, and DSL.

`reference/` holds complete upstream implementations pinned at a known commit — CUTLASS, Triton,
FlashAttention, FlashInfer, TileLang, DeepGEMM, Composable Kernel and others, with `reference/README.md`
listing every project. Read it to see how a production library expresses a layout, a pipeline stage,
or an instruction selection, then write your own baseline. Copying a file wholesale into
`work/kernel/` is not a baseline.

## Execution boundary

- Modify candidate files only under `work/kernel/`; temporary files belong in `scratch/`. Reusable
  methods may be saved under `skills/`, and reusable utilities under `tools/` with a synchronized
  `tools/README.md`. These directories seed every later trajectory; never store credentials or
  one-off measurements in them.
- Never edit `input/`, `agent/`, `reference/`, the session manifest, session traces,
  evaluator/reference state, credentials, controller state, or service state.
- Never run a compiler, GPU import, JIT, candidate, profiler, or evaluator directly in the shell.
  Use the shared tool contract below for external work and Runtime-local reads.
- Do not install dependencies, create Git commits or refs, or delegate computation to a third-party
  prebuilt operator.
- The task context fixes the DSL and hardware target. Do not select another DSL.

## Baseline workflow

### 1. Reconstruct the operator contract

Read the seed Kernel and use the public operator contract injected into this Prompt. Record:

- operator semantics and calling ABI;
- public input/output shape regimes and domains;
- dtypes and accumulation rules;
- layouts, strides, broadcasting, and masking;
- boundary and special-value behavior;
- accuracy tolerances and cross-field invariants.

Never guess unsupported facts. Exact hidden cases remain private; dispatch may use ordinary runtime
properties but never evaluator identities, hidden input values, or reconstructed case tables.

### 2. Learn only what is needed

After the minimal operator-contract review, choose one baseline-construction hypothesis and
immediately follow the shared Direction contract below to propose and start it before any
direction-specific work.

When `wiki-query` is available, query only for the actual architecture, DSL, operator, and mechanism.
Preserve stable Record IDs for knowledge actually used. If Wiki authority is unavailable, continue
from the seed, included Skills, and public contract; Wiki absence alone is not a blocker. Stop once one
viable approach has adequate support and keep its actionable constraints in `scratch/`.

### 3. Establish the first self-contained DSL Kernel

Under the already-started baseline-construction Direction, inspect the writable copy under
`work/kernel/`. If it is already a complete, self-contained implementation in the bound DSL, you
may evaluate that unchanged copy and use it as the first measured construction. Otherwise modify
only `work/kernel/` while preserving the evaluator-facing entrypoint and metadata contract.
Implement the complete operator in the lineage DSL, using PyTorch only for plumbing or allocation
explicitly allowed by the public operator contract and DSL policy. Do not use PyTorch compute,
alternate DSLs, hidden dispatch, external implementation downloads, or third-party prebuilt
compute.

Choose simple, robust tiling, launch geometry, data movement, and boundary handling. This stage does
not need to beat the reference. Use the shared Direction and Experiment Journal to preserve every
decisive construction, repair, retained change, and reverted failure for later optimization
Attempts, but do not expand framework bring-up into an unbounded performance search.

### 4. Validate and repair

Use bounded `gateway-execute` requests with `operation="dev"` or `operation="check"` for focused
compilation and correctness repair. Then evaluate the exact current candidate with a full
`gateway-execute` request containing `{"operation":"evaluate"}`.

Every `evaluate` call is an exploratory measurement of the exact `work/kernel/` tree at that
moment. You may measure the unchanged seed once during Bootstrap and submit multiple repaired
candidates. Core assigns their request identities, and the controller durably retains every
evaluated Kernel and raw result. Record each meaningful repair as an Experiment using the previous
measured subject as `before` and the newly measured subject as `after`. These measurements are
evidence, not the authoritative baseline outcome.

The first measured construction has no measured predecessor. Save the request below as
`scratch/baseline-experiment.json`, then invoke `record-experiment` with that request using the
exact CLI listed in the shared Runtime tool contract below. Record it exactly once with
`action="baseline"`, `before=null`, and its complete measured Kernel/Trial/Result subject as `after`.
The receipt returns an `experiment_id`; use that ID when completing the Direction and in every
Finding supported by this construction. This creates the Experiment anchor only; it does not register `v0`.
For later repairs use `keep_after` or `restore_before` with complete `before` and `after` subjects.

```json
{
  "direction_id": "direction_<id>",
  "name": "establish first measured DSL candidate",
  "hypothesis": "the direct DSL implementation satisfies the public contract",
  "change": "established the first measured DSL candidate; state whether the seed was unchanged",
  "before": null,
  "after": {
    "kernel_artifact_digest": "sha256:<kernel>",
    "kernel_trial_id": "gtrial_<id>",
    "gateway_result_digests": ["sha256:<evaluate-result>"]
  },
  "evidence": "factual correctness and latency returned by the evaluation",
  "analysis": "whether the first construction held and what must be repaired",
  "action": "baseline"
}
```

Before nomination, the Agent-visible Evaluate must pass every case and seed it reports. Runtime
later applies the complete private Bootstrap Gate independently; do not claim that the exploratory
Evaluate proves that final outcome. A slower but correct candidate is valid. On failure, diagnose
the smallest causal issue and repair it while a concrete next step remains. Never fabricate a
measurement, weaken the public contract, or claim success from a partial probe. If infrastructure
or missing authority prevents validation, report a blocker.

## Terminal contract

Use the shared `attempt-report` schema described below. Bootstrap permits only:

- `status="candidate_ready"` after a correct Agent-visible Evaluate of the exact current candidate;
  Runtime still owns complete Bootstrap validation; or
- `status="blocked"` when a concrete technical or infrastructure blocker remains after exhausting
  safe in-scope repairs.

Do not use `pivot` during Bootstrap. Before terminal handoff, close every started Direction and
record each decisive construction or repair as an Experiment. Leave any useful unstarted
optimization ideas as `proposed` or `deferred` Directions so later optimization Attempts can load
and advance them without reconstructing the Bootstrap session.

A `candidate_ready` Bootstrap report requires exactly one `baseline` Experiment. A blocked report
may omit it only when no candidate reached an identity-bearing Gateway result.

Use the shared report fields with Bootstrap semantics: `diagnosis` names the bring-up or correctness
issue, `approach` explains the construction or repair, and `expected_impact` states the expected
correctness or compatibility effect. Set `profile_evidence` to `null` unless profiling was actually
needed. If the nominated Kernel is unchanged from the seed, say that explicitly in
`final_candidate.change_summary`; do not invent a change or a performance bottleneck.

`candidate_ready` nominates the exact final `work/kernel/` tree as the Baseline Candidate. The
trusted controller seals that tree, resolves the correct Evaluate result referenced by its Journal,
and applies the Bootstrap finalization policy before creating `v0` and the Lineage. Direction and
Experiment Journals, their Kernel/Trial/Result identities, and the backend-neutral conversation
become immutable Lineage history. Chat text, local files, or the Agent's conclusion cannot create
the baseline by themselves.
