# Direction-only requests

The Direction module is enabled and the Experiment module is disabled. Runtime maintains
`PROGRESS.md`, `directions/<status>/<direction_id>.json`, and flat
`records/<gateway_record_id>.json`. Read these generated files directly; do not edit them.

## Propose, select, and progress work

A Direction may be a direct Kernel change or an optimization-relevant capability probe,
microbenchmark, correctness investigation, or performance attribution whose result changes Kernel
design or selection. It must identify that consequential Kernel decision. Runtime/evaluator
maintenance, infrastructure operation, and unrelated documentation are outside the Direction queue.
For a probe-led Direction, `change_scope` describes the Kernel path it may unlock or reject.

Write mutation requests below `scratch/`. A proposal requires:

```json
{
  "action": "propose",
  "name": "Split KV across CTAs",
  "change_scope": "major_redesign",
  "hypothesis": "KSPLIT can hide long-KV memory latency enough to pay for reduction",
  "applicability": "Long-context regimes with enough KV work for multiple CTAs",
  "expected_benefit": "Reduce long-context latency without regressing short-context dispatch",
  "plan": ["Compile a KSPLIT=2 probe", "Evaluate the complete workload"]
}
```

`stop_conditions` is optional. Optional genealogy uses `relationship` plus
`derived_from_direction_ids`; Experiment-derived genealogy is unavailable because this mode does
not expose Experiments. Relationship values are
`refinement`, `correction`, `combination`, `alternative`, `reimplementation`, or
`contradiction`. All cited IDs must already be visible. Refinement, correction, alternative,
reimplementation, and contradiction require exactly one effective parent; combination requires at
least two. Reuse an existing Direction ID when the hypothesis is unchanged. Genealogy is descriptive:
the child does not inherit parent status, plan, evidence, Kernel source or priority. Materialize and
adopt parent source separately when needed. Runtime checks parent visibility, arity and cycles, not
the scientific correctness of the relationship label.

```bash
python3 tools/sandbox.py --kind update-direction --request-file scratch/direction.json
```

Before starting work, read `PROGRESS.md` and all relevant Direction files. Prefer expected impact,
including the value of the Kernel path an investigation could unlock, over implementation
convenience; high-value major redesigns should not be abandoned merely because they are difficult.

```json
{"action":"start","direction_id":"direction_11111111111111111111111111111111","analysis":"Highest expected impact and a bounded first probe"}
```

A trajectory may own multiple local active Directions. Keep concurrent work intentional, address
updates by Direction ID, and leave new ideas proposed until work actually starts. Reorder the durable queue with:

```json
{"direction_id":"direction_11111111111111111111111111111111","before_direction_id":"direction_22222222222222222222222222222222","reason":"Highest-information structural probe"}
```

Use `after_direction_id` instead, or omit both positions to move it to the end. Mark a completed
zero-based plan step with:

```json
{"direction_id":"direction_11111111111111111111111111111111","plan_step":0}
```

Submit these through `prioritize-direction` and `progress-direction`, respectively.

## Close or preserve a Direction

```json
{
  "action": "defer",
  "direction_id": "direction_11111111111111111111111111111111",
  "analysis": "The capability probe is blocked by the current compiler",
  "hypothesis_status": "unresolved",
  "reopen_condition": "The target compiler exposes the required instruction"
}
```

`complete`, `abandon`, `block`, and `defer` use the same core fields. Do not provide Experiment
support fields. `hypothesis_status` is `unresolved`, `supported`, or `refuted`. Unresolved defer,
block, and abandon actions require `reopen_condition`. Continue multiple bounded
probe/candidate/measurement cycles while useful work remains; do not hand off the first promotable
checkpoint.

## Adopt an exact measured Kernel

Materialize the source with a shared Git ref, `cherry-pick`, or `kernel-read`, then audit it:

```json
{
  "kernel_commit_id": "0123456789abcdef0123456789abcdef01234567",
  "source_ref": "refs/atrex/shared/T2/e0004/head",
  "reason": "Continue the peer split-KV implementation"
}
```

Submit with `--kind adopt-kernel`. Runtime requires the current SourceProvider-declared candidate bytes to
match that commit. Adoption alone says nothing about correctness or performance.

## Submit the Episode report

Candidate reports cite measurement evidence directly because the Experiment module is disabled:

```json
{
  "status": "candidate_ready",
  "handoff_reason": "direction_terminal",
  "summary": "The selected Direction is complete and the candidate is ready for independent gates",
  "gateway_record_ids": ["gateway-0123456789abcdef0123456789abcdef"]
}
```

The exact reported HEAD must have a completed, passing standard full Evaluate with
`mode=full` and `input_scope=contract`. Subset Evaluate, Profile, Dev, Check, Disassemble, and ABBA
alone cannot qualify it. Submit through `--kind episode-report`.

Use `direction_terminal` only after completing or abandoning all local active Directions. Pooled search
may use `cross_trajectory_share` only with `status=candidate_ready` for a reusable checkpoint backed by a passing standard full
Evaluate of the exact committed source while leaving Directions active. A
blocked report requires `status=blocked` and a nonempty `blocker`. Close every local active Direction
with `block` first.
For a terminal pivot use
`{"status":"pivot","handoff_reason":"direction_terminal","summary":"..."}`.

On a repairable rejection, follow `error.next_action`, inspect the projected Direction and Record
files, correct the request or state, and resubmit. Never invent IDs or repeat GPU work whose outcome
is already saved.
