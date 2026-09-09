## Session tools ({{DSL}})

Use the following exact CLI subcommand names; there are no function-style aliases. For each call,
write one JSON request under `scratch/`, then run exactly one of:

```text
python3 {{RUNTIME_TOOL}} gateway-execute --request scratch/<request>.json
python3 {{RUNTIME_TOOL}} kernel-trial-show --request scratch/<request>.json
python3 {{RUNTIME_TOOL}} kernel-artifact-read --request scratch/<request>.json
python3 {{RUNTIME_TOOL}} result-artifact-read --request scratch/<request>.json
python3 {{RUNTIME_TOOL}} update-direction --request scratch/<request>.json
python3 {{RUNTIME_TOOL}} list-directions --request scratch/<request>.json
python3 {{RUNTIME_TOOL}} load-direction --request scratch/<request>.json
python3 {{RUNTIME_TOOL}} record-experiment --request scratch/<request>.json
python3 {{RUNTIME_TOOL}} list-experiments --request scratch/<request>.json
python3 {{RUNTIME_TOOL}} load-experiment --request scratch/<request>.json
python3 {{RUNTIME_TOOL}} attempt-report --request scratch/<request>.json
```

Each local `--request` JSON file is limited to 1 MiB (1,048,576 bytes), including whitespace.
This general limit does not replace the smaller per-field input/Shape limits below.

`gateway-execute` uploads the current `work/kernel` tree by default. For `evaluate`,
`candidate_path` may select Candidate B and `comparison.baseline_path` selects baseline A when
comparing. Trusted Runtime fields are injected automatically. Never embed `baseline` or `candidate`
source payloads, a schema version, capability, or attempt ID in the request.
Runtime-local history queries use their dedicated commands above;
do not pass `kernel_trial_show`, `kernel_artifact_read`, or
`result_artifact_read` to `gateway-execute`.

Every `gateway-execute` request names one `operation`. These are the only Agent-authored fields;
each is optional with the default shown in parentheses unless marked required, and an omitted field
is normally the right choice:

```text
evaluate     candidate_path (work/kernel), mode=full|correctness_only (full),
             input_py or input_path, shapes or shapes_path; omitted input/Shapes reuse that component;
             comparison={method:abba, baseline_path:required, repeats:2..20 (2)} (omitted)
profile      level=survey|sol|deep (sol), profiler=ncu|rocprofv3, counters=[], source (false),
             kernel_name or kernel_regex, launch_skip, launch_count, top_kernels, shape_id
dev          command (required), file_paths=[], env_vars={}, job_timeout_s (<=600), recycle (true),
             note, intent=workspace|scratch_exec|inspect|compile|profile_adhoc|sanitize|
             custom_harness|other
check        arch, sanitize=memcheck|racecheck|initcheck|synccheck
disassemble  fmt=sass|ptx|isa|auto (auto)
env          gpu, capabilities (false, requires gpu), force (false)
```

`profile`, `check`, and `disassemble` additionally accept `env_vars`, `requirements`, and
`deps_mode=freeze_installed|no_deps` to install dependencies for that Job. `kernel_name` and
`kernel_regex` are mutually exclusive, and `level: "deep"` requires one of them. `dev` takes its
extra sources through `file_paths`, a list of workspace-relative paths; each named file is uploaded
under its basename alone and may not shadow a `work/kernel` path, which is why a multi-line probe
does not need to be smuggled through `command`. Prefer `file_paths` over a heredoc inside `command`.

For `evaluate`, `input_path` names your UTF-8 Python input generator (at most 128 KiB), and
`shapes_path` names your UTF-8 JSON object of Agate Shape records (at most 256 KiB). Use safe
workspace-relative paths to regular files, such as `scratch/custom-input.py` and
`scratch/custom-shapes.json`; links and Runtime control paths are rejected. The tool uploads their
contents as `input_py` and `shapes` before computing the retry identity. Inline `input_py` and
`shapes` are also supported; do not provide both forms of the same field. Shape IDs must be integer
strings and each record must be an object compatible with the input generator. Either override may
be supplied independently, and an omitted component is reused from the private contract without
being exposed. Custom source must implement the Agate `_make_inputs` interface for the public ABI.
For `evaluate`, `mode: "correctness_only"` checks correctness without performance measurement or automatic profiling.
Custom inputs or Shapes and correctness-only calls provide exploratory evidence; before
`candidate_ready`, the exact current Kernel requires a successful ordinary full Evaluate using
the trusted contract. Request it with `{"operation":"evaluate"}` when no matching evidence exists.
For exact historical source with matching trusted full-Evaluate evidence, register `action: "adopt"`
in this Attempt's Experiment Journal as described below; do not repeat that measurement merely to
obtain a new Trial ID. Runtime validates whether the historical evidence qualifies for nomination.

`evaluate` accepts optional `candidate_path`; omitting it uploads the current `work/kernel` tree.
To compare against baseline A, add `comparison` with `method: "abba"` and required
`baseline_path` inside that object. Both `comparison.baseline_path` and `candidate_path` must be
safe workspace-relative paths to either a regular `.py` file or a Kernel source directory. A single
Python file is uploaded as `kernel.py`; a directory preserves its relative file names. Links,
absolute/traversal paths, Runtime control paths, and empty source directories are rejected. The tool uploads
both sources and computes the request identity from their contents, not the local paths.
Do not embed `baseline` or `candidate` source payloads in the request.
Both sides use the same evaluation inputs. `comparison.repeats` counts observations per side:
the default 2 produces A, B, B, A.
Values from 2 to 20 are accepted only when the schedule fits Runtime's allocation budget.
Each Shape batch runs both sides within one allocation; different Shape batches may use different
allocations. ABBA requires full correctness and timing; omit `mode` or set it to `"full"`.
It is always exploratory, does not retain or promote a Kernel or Agent, and does not satisfy the
full trusted-contract Evaluate required for `candidate_ready`.
Agent ABBA and Runtime's authoritative ABBA are separate paths. Runtime performs its retention
comparison only after a successful terminal Report handoff; it records authoritative measurements,
not an Agent Kernel Trial. Never wait for that later ABBA to create a `gtrial_` for an Experiment
or to make the Report submittable. An Agent ABBA observation belongs to Candidate B's Trial;
the same exact B in the same Attempt and recovery generation keeps the same Trial ID.

A Gateway call blocks until its Job reaches a terminal state, which for `evaluate`, `profile`,
`check`, and `disassemble` may take a long time. Let the command finish and keep stderr out of the
JSON on stdout, because appending `2>&1` corrupts the result you then have to parse. Runtime owns Job
tracking and recovery; do not build polling or retry loops. If a local process interruption loses a
response, run the identical request again. Runtime either reconnects to the in-flight operation or
replays its recorded Result without spending GPU time or call budget.

An expected tool failure prints one JSON Object and exits nonzero. For request mistakes, repair the
compact `issues` first, then use the operation-specific `request_schema`; an unknown operation
returns `supported_operations`. Local input-file errors identify the failing `input_path` or
`shapes_path` in `issues[].path`; source errors identify `comparison.baseline_path` or `candidate_path`.
Evaluate's `request_schema` describes `full`/`correctness_only`, inline
and file forms, and their mutual-exclusion constraints. Supplying `comparison` permits only
`mode: "full"`; its nested schema requires `method` and `baseline_path`, and bounds `repeats`.
Comparison errors identify `comparison.method`, `comparison.baseline_path`, or `comparison.repeats`.
Follow the bounded, field-specific
`recovery` steps to repair the file, path, encoding, JSON object, or conflicting field, then retry.
For Evaluate errors returned by Runtime, its supplied `issues`, `request_schema`, and `recovery`
are preserved; use that guidance. Runtime Journal and local Report errors may also return bounded `recovery`
steps naming a visibility-safe list/load tool; execute those steps instead of guessing an ID. A
`candidate_rejected` result created before Job execution includes safe source-validation `details`
that should be fixed directly. A hidden-case failure deliberately omits exact inputs; repair it only
from the public contract, opaque per-Shape results, and safe profiling evidence.

Contract evaluation results identify private cases only by numeric `shape_id`, such as `"0"` or `"1"`,
and never reveal their inputs. After a contract evaluation, a profile request may add
`"shape_id":"<numeric id>"` to profile that one real case; omitting it selects one evaluator-owned
case and the Profile result reports the selected number. Do not infer or reconstruct case inputs
from ids or measurements.

A case passes when every output is within `atol=0.01` and `rtol=0.05` of the reference, so compare
the reported `max_abs_err` and `max_rel_err` against those thresholds to see how much margin a
candidate actually has. Every selected Shape is checked on each evaluation, but each one draws fresh random
inputs, and the authoritative gate that seals a Kernel draws more of them per Shape than an
exploratory evaluate. A single passing evaluation near either threshold is therefore weak evidence:
treat a thin margin as a defect to fix rather than a pass, because the sealing gate rejects a
candidate the Agent measured as correct and that rejection lands after the Session has exited.

Agent-visible Gateway responses follow three contracts:

- `evaluate`, `profile`, `check`, and `disassemble` retain the exact `kernel_artifact_digest`,
  `kernel_trial_id`, and `result_artifact_digest` needed for experiment provenance;
- `dev` returns its Agent-safe Job result directly and does not print those identities;
- `env` returns its Agent-safe `result` directly.

`check` and `disassemble` report only `status`, `job_id`, `error`, and the nested `result` holding
the compile verdict; a compile-only Job never launches the Kernel, so it carries no register, spill,
or assembly evidence. Use `evaluate` or `profile` for those.

Profile additionally reports the numeric `shape_id`, normalized per-Kernel durations, resource and
SOL evidence, safe profiler counters, and duration-weighted summary fields. No Gateway result echoes
a protocol version or a trusted request identity; the `request_schema` returned with a request error
is the one exception, because it is a schema document and carries its own versions.

Example exploratory evaluation request:

```json
{"operation": "evaluate"}
```

Correctness-only evaluation on the contract inputs and Shapes:

```json
{"operation": "evaluate", "mode": "correctness_only"}
```

For a public VecAdd ABI `Model.forward(left, right)`, the following paired files demonstrate
custom inputs. Adapt the argument names, shapes, dtype, and device to your actual public task ABI;
these are illustrative cases, not private evaluator cases.

Contents of `scratch/custom-input.py`:

```python
import torch


def _make_inputs(num_elements: int) -> dict[str, torch.Tensor]:
    left = torch.randn((num_elements,), device="cuda", dtype=torch.float32)
    return {"left": left, "right": torch.randn_like(left)}
```

Contents of `scratch/custom-shapes.json`:

```json
{
  "0": {
    "input_kwargs": {"num_elements": 1024},
    "init_kwargs": null
  },
  "1": {
    "input_kwargs": {"num_elements": 4097},
    "init_kwargs": null
  }
}
```

Each `input_kwargs` object supplies keyword arguments to `_make_inputs`, not Tensor definitions.
The returned dictionary keys must match `Model.forward` argument names. `init_kwargs` supplies
`Model` constructor arguments; use `null` or `{}` for a no-argument constructor. Do not hard-code
a random seed in the generator. Prefer supplying both custom files together: overriding only one
component can leave it incompatible with the private contract component reused for the other.
Use the public ABI to author your own cases; do not infer or reconstruct private cases.

Correctness-only evaluation using these input and Shape files:

```json
{"operation": "evaluate", "mode": "correctness_only", "input_path": "scratch/custom-input.py", "shapes_path": "scratch/custom-shapes.json"}
```

Omit `mode` or set `"mode":"full"` to measure performance for your custom cases. These requests
retain Kernel Trial and Result Artifact identities. Their nested result records the effective
`mode` and `input_scope` (`"custom"` when either component was supplied, otherwise `"contract"`).
Correctness-only results contain no performance measurements; do not interpret missing latency as zero.

Example Evaluate comparison using a saved baseline source and the current Kernel:

```json
{"operation": "evaluate", "comparison": {"method": "abba", "baseline_path": "scratch/baseline.py"} }
```

An ABBA comparison selecting both source directories and your own input generator and Shapes:

```json
{"operation": "evaluate", "candidate_path": "scratch/candidate-kernel", "comparison": {"method": "abba", "baseline_path": "scratch/baseline-kernel", "repeats": 2}, "input_path": "scratch/custom-input.py", "shapes_path": "scratch/custom-shapes.json"}
```

Comparison responses retain `operation: "evaluate"` and identify the method through
`result.comparison: {"method":"abba","repeats":2}` (using the actual repeat count).
The returned Kernel Trial and Kernel Artifact identities belong to B. The nested result names
`baseline_kernel_artifact_digest` for A, provides `baseline` and
`candidate` correctness and latency summaries, and reports `speedup` as A/B and `improvement_pct`
as (A-B)/A × 100. It retains `schedule` and all `measurements`, plus `mode` and `input_scope`.
Read the retained comparison with `result-artifact-read`, or find its digest with
`kernel-trial-show`; comparison results do not create an ordinary Evaluate record. Record the comparison as
experiment evidence. Nomination still requires a successful ordinary full Evaluate or an explicit,
Runtime-accepted `adopt` decision binding matching historical full-Evaluate evidence.

Runtime-local query commands infer their operation from the command name. Their request JSON must
not contain `operation`. Examples are `{"kernel_trial_id":"gtrial_<id>"}` for
`kernel-trial-show`,
`{"kernel_artifact_digest":"sha256:<digest>","artifact_file":"kernel.py",`
`"file":"scratch/recovered/kernel.py"}` for
`kernel-artifact-read`, `{"result_artifact_digest":"sha256:<digest>"}` for
`result-artifact-read`. These reads are unmetered and never contact Agate.
`kernel-trial-show` returns the Kernel Artifact Digest and a compact `result_artifacts` index. Each
entry is `{"result_artifact_digest", "operation", "status"}`; it does not inline result content.
Use `result-artifact-read` only for the Evaluate, Profile, or other result that you actually need.
`result-artifact-read` returns `{"operation", "status", "result"}`; the measurement lives under
`result`, not beside those keys. `operation`, `status`, and `result` are the same canonical values
returned by the original `gateway-execute` call. For an Evaluate, `result` holds `correct`,
`correctness`, and `failures`; a full evaluation also reports `latency_us_by_shape` keyed by opaque
Shape ID and the aggregates `latency_us_arith_mean` and `latency_us_geomean`. Custom and correctness-only
evaluations also carry `mode` and `input_scope`. It does not reveal private evaluator inputs or
hidden-case details.
For `kernel-artifact-read`, `file` is a required destination under `scratch/`; `artifact_file`
selects the source inside the Artifact and defaults to the destination basename. Source content is
written atomically and is not printed to stdout.

`record-experiment` sends each validated Experiment to Runtime immediately. Runtime durably appends
it to the logical Attempt before the command returns; the Journal therefore survives a crashed
Session and a new recovery generation. Invoke `list-experiments` with
`{"file":"scratch/experiments-index.json"}`; it asks Runtime for the authorized live-plus-history
view, then atomically writes compact
Experiment ID, name, hypothesis, change, evidence, analysis, and action entries to that
file and returns only status, file, and count. Read the file, then invoke `load-experiment` with
`{"experiment_id":"experiment_<id>"}` only for selected entries to retrieve their complete
Agent-visible records; Runtime-internal ordering metadata is omitted. Both commands are Runtime-local, unmetered, and bounded by Runtime-authorized Lineage
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

A Direction is the durable unit of research and exploration for one causal hypothesis, not an
Experiment container. Before choosing one, inspect only the contract, incumbent, Journal indexes,
and generic Runtime state. Once chosen, immediately `propose` and `start` it before its research,
Dev/Check/Profile/Evaluate, disassembly, tools, or source edits. `TaskCreate`, scratch
plans, and prose do not register it; do not wait for measurement or `record-experiment`.

Propose a Direction with:

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

Then call `update-direction` with the returned `direction_id`, an action of `start`, `complete`,
`abandon`, `block`, or `defer`, and non-empty `analysis`. Runtime derives Experiment links; do not
provide them. Events append to history. An Attempt may advance at most three inherited or new
Directions; proposals are unlimited and do not consume this limit. Only one Direction may be
`in_progress` at a time: do not interleave their research, tools, edits, or measurements. Before
starting another, close the current one with `complete`, `abandon`, `defer`, or `block`. None may
remain `in_progress` at handoff. Without an Experiment use `defer` or `block`; `complete` and
`abandon` require supporting Experiments.

If evidence was omitted before closure, `record-experiment` can append it to a visible
`completed`, `abandoned`, `blocked`, or `deferred` Direction without reopening it. The receipt
does not change its status or prior events; `load-direction` automatically includes the new
Experiment in its supporting IDs. A merely `proposed` Direction must still be started first.
Late recording uses the same Trial visibility, ownership, and evidence checks as ordinary recording.
Use it to complete the Journal before terminal handoff, not to resume research without `start`.

Each `record-experiment` request must contain exactly these fields:

```json
{
  "direction_id": "direction_<id>",
  "name": "short experiment name",
  "hypothesis": "falsifiable expected mechanism",
  "change": "exact candidate change, including a reverted change",
  "before": {"kernel_trial_id": "gtrial_<before-trial>"},
  "after": {"kernel_trial_id": "gtrial_<after-trial>"},
  "evidence": "concise before/after measurements and observations",
  "analysis": "what the evidence means, including whether the hypothesis held",
  "action": "keep_after"
}
```

`before` and `after` identify both measured sides using only their Kernel Trial IDs. Runtime resolves
and freezes each Trial's exact Kernel Artifact and all Result Artifacts when it records the
Experiment; do not submit those derived identities yourself. Record the entry before changing or
reverting the candidate. For `keep_after`, `restore_before`, and `adopt`, both sides are required.
Use `adopt` when choosing exact already-measured historical source: restore its complete Kernel
tree into `work/kernel/`, then record this Attempt's decision with the real `before` and `after`
Trial IDs. Only `adopt` permits a historical Trial as `after`; ordinary actions require `after`
to belong to the current Attempt. Runtime verifies visibility, the same Lineage, DSL, hardware,
and evaluation contract, and a successful ordinary full Evaluate for that exact Kernel and Result.
The adopted Trial keeps its original ownership; adoption creates a decision, not a new measurement
or a replacement Trial. It can qualify the exact restored Kernel for `candidate_ready` without
rerunning Evaluate. A custom-input, correctness-only, or Agent ABBA result does not qualify.
For `abandon_direction` before any identity-bearing operation, set both `before` and `after` to
`null`; never set only one side to `null`. The phase Prompt may additionally permit Bootstrap-only
`baseline`, which requires `before=null` and a measured `after` Trial. A `dev` result alone supplies no
identity.
`record-experiment` persists the complete entry and prints only a compact receipt such as
`{"status":"recorded","experiment_id":"experiment_<id>"}`; use that ID when referring to the
experiment later. It does not echo the Agent-authored text, assigned sequence, or timestamp.
Keep `evidence` factual: report observations and measurements. Use `analysis` for interpretation,
the hypothesis verdict, causal explanation, limitations, and remaining uncertainty. Do not use a
top-level `result` field; that term is reserved for Gateway responses. `action` records what you
actually did after analysis and normally must be `keep_after`, `restore_before`, `adopt`, or
`abandon_direction`; use `baseline` only when the phase Prompt explicitly permits it.
After each receipt, update the working terminal Report draft
`scratch/attempt-report-draft.json`. Accumulate the Experiment ID in the relevant Finding and
refine the diagnosis, evidence summaries, Profile bindings, knowledge use, and Attempt-level
analysis while the evidence is fresh. The draft may be overwritten throughout the session. Do not
call `attempt-report` during this process. When the engineering loop and all started Directions are
closed, validate the completed draft and use it as the request. The first successful call publishes
the write-once terminal handoff to the separate `scratch/attempt-report.json` and prints only a
compact receipt such as
`{"status":"published","report_status":"candidate_ready","file":"scratch/attempt-report.json",`
`"experiment_count":3,"finding_count":2}`; the report text itself is never echoed back. An
error response publishes nothing: correct the same draft using
`issues`, `request_schema`, and `recovery`, then call `attempt-report` again. Never call it again
after a successful response.

A final chat message does not replace a successful `attempt-report` call. If you are brought
back solely to complete the handoff, use the existing Journal, draft, and measured evidence;
do not restart optimization or invent missing results.

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
      "result_artifact_digest": "sha256:..."
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
  "contributing_kernel_trial_ids": ["gtrial_<id>"],
  "blocker": null
}
```

Use `candidate_ready` when nominating the current Kernel for the controller-owned disposition,
`pivot` when the phase Prompt permits ending without a nomination, and `blocked` for an external or
infrastructure blocker. `candidate_ready` does not mean the Kernel is retained or registered; only
Runtime policy can make that decision. `candidate_ready` requires `final_candidate` and a null `blocker`; `pivot`
requires both to be null; `blocked` requires a non-empty `blocker` and null `final_candidate`.
Only `candidate_ready` requires non-empty Experiments, Direction events, and `findings`.
If no experiment was completed, `blocked` or `pivot` may carry zero Experiments and `findings: []`;
the Runtime-supplied Direction event list may also be empty when no Direction was started.
Still close any `in_progress` Direction with `block` or `defer` before handoff. Explain the actual
blocker or stopping decision in the structured report; never fabricate an Experiment, Finding,
Trial, or measurement merely to satisfy a non-empty list.
Keep `knowledge_used` and `findings` structured as shown. Directions left `proposed` or `deferred`
are the next available directions and require no duplicate ID list in the report. Every finding
must state its `resolution`: the applied fix, rollback,
workaround, explicit absence of a fix, or deferred action. Every finding must also name one or more
unique `supporting_experiment_ids`
returned by `record-experiment`; each ID must belong to this Attempt's Experiment Journal.
`profile_evidence` must describe evidence returned by Runtime-bound profiling and
bind every supporting Profile result to the exact Kernel Artifact, Kernel Trial, and Result
Artifact identifiers returned by Runtime. Any Runtime-recorded Profile result in your visible
history is citable, including an earlier Profile or one obtained after recording an Experiment.
No Experiment reference is required: do not create a diagnostic Experiment or reopen a Direction
solely to make a Profile citable. Runtime verifies the exact identities and the `profile` operation
against its own observations, not your prose. Include at least one `profile` result, set
`profile_evidence` to `null` when you have no recorded Profile evidence, and never invent it.
Use `contributing_kernel_trial_ids` to name the historical Kernel Trials whose code or approach this
Attempt actually drew on, using the Trial identifiers Runtime returned. Supply at most 64 entries
in any order; the tool and Runtime automatically sort and deduplicate them. ID format validation
still applies to every entry. Use `[]` when you drew on none.
It records where your work came from for whoever reads this Attempt later; it
is a claim rather than a measured fact, and it neither replaces nor duplicates the Experiment
`before` and `after` bindings. Do not include the
Journal in the terminal request; the CLI obtains the authoritative current-Attempt snapshot from
Runtime and attaches it. Do not run GPU, compiler, JIT,
profiler, or evaluator work outside these bindings.
