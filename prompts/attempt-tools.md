## Session tools

Use the following exact CLI subcommand names; there are no function-style aliases. For each call,
write one JSON request under `scratch/`, then run exactly one of:

```text
python3 {{RUNTIME_TOOL}} gateway-execute --request scratch/<request>.json
python3 {{RUNTIME_TOOL}} kernel-trial-show --request scratch/<request>.json
python3 {{RUNTIME_TOOL}} kernel-artifact-read --request scratch/<request>.json
python3 {{RUNTIME_TOOL}} gateway-result-read --request scratch/<request>.json
python3 {{RUNTIME_TOOL}} wiki-query --request scratch/<request>.json
python3 {{RUNTIME_TOOL}} update-direction --request scratch/<request>.json
python3 {{RUNTIME_TOOL}} list-directions --request scratch/<request>.json
python3 {{RUNTIME_TOOL}} load-direction --request scratch/<request>.json
python3 {{RUNTIME_TOOL}} record-experiment --request scratch/<request>.json
python3 {{RUNTIME_TOOL}} list-experiments --request scratch/<request>.json
python3 {{RUNTIME_TOOL}} load-experiment --request scratch/<request>.json
python3 {{RUNTIME_TOOL}} attempt-report --request scratch/<request>.json
```

`gateway-execute` automatically attaches the exact current `work/kernel` tree and trusted Runtime
fields. Never embed a candidate, schema version, capability, or attempt ID in its request.
Runtime-local history queries use their dedicated commands above;
do not pass `kernel_trial_show`, `kernel_artifact_read`, or
`gateway_result_read` to `gateway-execute`. Example profile request:

```json
{"operation": "profile", "level": "survey"}
```

An expected tool failure prints one JSON Object and exits nonzero. For request mistakes, repair the
compact `issues` first, then use the operation-specific `request_schema`; an unknown operation
returns `supported_operations`. Runtime Journal and local Report errors may also return bounded `recovery`
steps naming a visibility-safe list/load tool; execute those steps instead of guessing an ID. A
`candidate_rejected` result created before Job execution includes safe source-validation `details`
that should be fixed directly. A hidden-case failure deliberately omits exact inputs; repair it only
from the public contract, opaque per-Shape results, and safe profiling evidence.

Evaluation results identify private cases only by numeric `shape_id`, such as `"0"` or `"1"`, and
never reveal their inputs. After an evaluation, a profile request may add
`"shape_id":"<numeric id>"` to profile that one real case; omitting it selects one evaluator-owned
case and the Profile result reports the selected number. Do not infer or reconstruct case inputs
from ids or measurements.

Agent-visible Gateway responses follow three contracts:

- `evaluate`, `profile`, and `check` retain the exact `kernel_artifact_digest`, `kernel_trial_id`,
  and `gateway_result_digest` needed for experiment provenance;
- `dev` and `disassemble` return their Agent-safe Job result directly and do not print those
  identities;
- `jobs`, `poll`, `cancel`, `env`, `health`, and `config` return their Agent-safe `result` directly.

Profile additionally reports the numeric `shape_id`, normalized per-Kernel durations, resource and
SOL evidence, safe profiler counters, and duration-weighted summary fields. Protocol versions and
trusted request identities are never printed.

Example exploratory evaluation request:

```json
{"operation": "evaluate"}
```

Example knowledge query request:

```json
{"query": "{{DSL}} vectorized load requirements for the target architecture"}
```

Runtime-local query commands infer their operation from the command name. Their request JSON must
not contain `operation`. Examples are `{"kernel_trial_id":"gtrial_<id>"}` for
`kernel-trial-show`,
`{"kernel_artifact_digest":"sha256:<digest>","artifact_file":"kernel.py",`
`"file":"scratch/recovered/kernel.py"}` for
`kernel-artifact-read`, `{"gateway_result_digest":"sha256:<digest>"}` for
`gateway-result-read`. These reads are unmetered and never contact Agate.
`kernel-trial-show` returns only the Kernel Artifact Digest and normalized `gateway_results`;
result entries omit their already-resolved Gateway Result Digests.
`gateway-result-read` returns a normalized Agent-visible measurement with operation, status,
correctness, aggregate latencies, and latency by opaque Shape ID. It does not reveal private
evaluator inputs or hidden-case details.
For `kernel-artifact-read`, `file` is a required destination under `scratch/`; `artifact_file`
selects the source inside the Artifact and defaults to the destination basename. Source content is
written atomically and is not printed to stdout.

`record-experiment` sends each validated Experiment to Runtime immediately. Runtime durably appends
it to the logical Attempt before the command returns; the Journal therefore survives a crashed
Session and a new recovery generation. Invoke `list-experiments` with
`{"file":"scratch/experiments-index.json"}`; it asks Runtime for the authorized live-plus-history
view, then atomically writes compact
Experiment ID, sequence, name, and action entries to that file and returns only status, file, and
count. Read the file, then invoke `load-experiment` with
`{"experiment_id":"experiment_<id>"}` only for selected entries to retrieve their complete
original records. Both commands are Runtime-local, unmetered, and bounded by Runtime-authorized Lineage
history. Bootstrap starts with no earlier journal history; its current live Journal remains visible.

`update-direction` likewise persists each proposal or lifecycle event in Runtime before returning.
Direction Journal reads are Runtime-local and unmetered. Invoke `list-directions` with
`{"file":"scratch/directions-index.json"}`; it atomically writes Direction ID, name, and current
status to that file and returns only status, file, and count. Read the file, then invoke
`load-direction` with `{"direction_id":"direction_<id>"}` only for selected entries to retrieve
their complete normalized Directions.
Its `supporting_experiment_ids` automatically includes every visible Experiment whose
`direction_id` names that Direction, together with associations snapshotted internally by prior
Direction status events.

Create a Direction before starting experiments:

```json
{
  "action": "propose",
  "name": "short search direction",
  "hypothesis": "falsifiable mechanism",
  "rationale": "why available evidence makes it worthwhile",
  "plan": ["ordered investigation step"],
  "success_criteria": "measurable condition for success",
  "stop_conditions": "evidence that ends this direction"
}
```

Then use `update-direction` with the returned `direction_id`, an action of `start`, `complete`,
`abandon`, `block`, or `defer`, and a non-empty `analysis`. Do not provide Experiment IDs: the tool
derives them from visible Experiments bound to the Direction. Start the Direction before recording
its Experiments. `complete` and `abandon` require at least one associated Experiment. Direction
updates are appended rather than overwriting prior history. One Attempt may start and advance at
most three distinct Directions, including inherited Directions and Directions proposed in this
Attempt. A proposal alone does not consume this limit. You may propose any number of genuinely
necessary, non-duplicate future Directions, and the terminal report does not limit how many remain
`proposed` or `deferred`. No Direction may remain `in_progress` at terminal handoff. If a started
Direction has no Experiment, use `defer` or `block`; `complete` and `abandon` require supporting
Experiments.

`wiki-query` returns the GPU Wiki's exact `records` mapping and `notes`. Each mapping key is a
stable Record ID; each value keeps its Store, source, type, scope, match, and isolated payload.
The payload is the complete safe served Record; no second read step exists. Preserve the exact
mapping keys of records that materially informed your work. Wiki protocol versions, snapshot
identities, and integrity digests are intentionally absent from Agent-facing results.

Each `record-experiment` request must contain exactly these fields:

```json
{
  "direction_id": "direction_<id>",
  "name": "short experiment name",
  "hypothesis": "falsifiable expected mechanism",
  "change": "exact candidate change, including a reverted change",
  "before": {
    "kernel_artifact_digest": "sha256:<before-kernel>",
    "kernel_trial_id": "gtrial_<before-trial>",
    "gateway_result_digests": ["sha256:<before-evaluate>", "sha256:<before-profile>"]
  },
  "after": {
    "kernel_artifact_digest": "sha256:<after-kernel>",
    "kernel_trial_id": "gtrial_<after-trial>",
    "gateway_result_digests": ["sha256:<after-evaluate>", "sha256:<after-profile>"]
  },
  "evidence": "concise before/after measurements and observations",
  "analysis": "what the evidence means, including whether the hypothesis held",
  "action": "keep_after"
}
```

`before` and `after` bind both sides of the modification to exact source and measurements. Each side
contains one Kernel Artifact Digest, its Trial ID, and a non-empty unique list of every relevant
Evaluate/Profile/Check Result Digest for that exact Kernel. Record the entry before changing or
reverting the candidate. For `keep_after` and `restore_before`, both sides are required. For
`abandon_direction` before any identity-bearing operation, set both `before` and `after` to `null`;
never set only one side to `null`. The phase Prompt may additionally permit Bootstrap-only
`baseline`, which requires `before=null` and a complete `after`. A `dev` or `disassemble` result
alone supplies no identity.
`record-experiment` persists the complete entry and prints only a compact receipt such as
`{"status":"recorded","experiment_id":"experiment_<id>"}`; use that ID when referring to the
experiment later. It does not echo the Agent-authored text, assigned sequence, or timestamp.
Keep `evidence` factual: report observations and measurements. Use `analysis` for interpretation,
the hypothesis verdict, causal explanation, limitations, and remaining uncertainty. Do not use a
top-level `result` field; that term is reserved for Gateway responses. `action` records what you
actually did after analysis and normally must be `keep_after`, `restore_before`, or
`abandon_direction`; use `baseline` only when the phase Prompt explicitly permits it.
After each receipt, update the working terminal Report draft
`scratch/attempt-report-draft.json`. Accumulate the Experiment ID in the relevant Finding and
refine the diagnosis, evidence summaries, Profile bindings, knowledge use, and Attempt-level
analysis while the evidence is fresh. The draft may be overwritten throughout the session. Do not
call `attempt-report` during this process. When the engineering loop and all started Directions are
closed, validate the completed draft and use it as the request. The first successful call publishes
the write-once terminal handoff to the separate `scratch/attempt-report.json`. An
error response publishes nothing: correct the same draft using
`issues`, `request_schema`, and `recovery`, then call `attempt-report` again. Never call it again
after a successful response.

Each `attempt-report` request must contain
exactly these fields:

```json
{
  "status": "candidate_ready",
  "hypothesis": "tested hypothesis",
  "diagnosis": {
    "bottleneck": "localized correctness or performance issue",
    "evidence": "measured evidence supporting the diagnosis"
  },
  "approach": {
    "summary": "engineering mechanism tested",
    "steps": ["ordered step"],
    "expected_impact": "falsifiable expected effect",
    "risks": ["known risk or rollback concern"]
  },
  "final_candidate": {"change_summary": "exact nominated working-tree state, including unchanged"},
  "evidence_summary": {
    "correctness": "Agent-visible correctness evidence",
    "performance": "Agent-visible performance evidence"
  },
  "profile_evidence": {
    "tool_used": "gateway-execute/profile",
    "profiler": "ncu",
    "profile_level": "sol",
    "bottleneck_type": "memory_bound",
    "evidence_summary": "key profiler metrics and host-side observations",
    "evidence_chain": "causal chain from measurements to bottleneck and decision",
    "supporting_results": [{
      "operation": "profile",
      "kernel_artifact_digest": "sha256:...",
      "kernel_trial_id": "gtrial_...",
      "gateway_result_digest": "sha256:..."
    }]
  },
  "analysis": "Attempt-level synthesis and hypothesis verdict",
  "knowledge_used": [{
    "record_id": "stable GPU Wiki Record ID",
    "finding": "relevant knowledge",
    "application": "how it changed the work"
  }],
  "findings": [{
    "category": "correctness or performance",
    "observation": "measured fact",
    "root_cause": "supported cause",
    "resolution": "fix, rollback, workaround, no fix, or deferred action",
    "lesson": "reusable lesson",
    "supporting_experiment_ids": ["experiment_<id>"]
  }],
  "blocker": null
}
```

Use `candidate_ready` when nominating the current Kernel for the controller-owned disposition,
`pivot` when the phase Prompt permits ending without a nomination, and `blocked` for an external or
infrastructure blocker. `candidate_ready` does not mean the Kernel is retained or registered; only
Runtime policy can make that decision. `candidate_ready` requires `final_candidate` and a null `blocker`; `pivot`
requires both to be null; `blocked` requires a non-empty `blocker` and null `final_candidate`.
Keep `knowledge_used` and `findings` structured as shown. Directions left `proposed` or `deferred`
are the next available directions and require no duplicate ID list in the report. Every finding
must state its `resolution`: the applied fix, rollback,
workaround, explicit absence of a fix, or deferred action. Every finding must also name one or more
unique `supporting_experiment_ids`
returned by `record-experiment`; each ID must belong to this Attempt's Experiment Journal.
`profile_evidence` must describe evidence returned by Runtime-bound profiling and
bind every supporting Profile result to the exact Kernel Artifact, Kernel Trial, and Gateway
Result identifiers returned by Runtime. Those exact identifiers must also occur in an Experiment
Journal `before` or `after` subject. Include at least one `profile` result, set `profile_evidence`
to `null` if no Profile was executed, and never invent profiler evidence. Do not include the
Journal in the terminal request; the CLI obtains the authoritative current-Attempt snapshot from
Runtime and attaches it. Do not run GPU, compiler, JIT,
profiler, or evaluator work outside these bindings.
