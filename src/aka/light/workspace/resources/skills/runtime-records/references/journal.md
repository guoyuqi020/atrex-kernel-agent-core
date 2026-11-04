# Direction and Experiment requests

All commands use the Session-scoped Runtime client. Put mutation requests below `scratch/`.
Requests must fit the documented 2 MiB envelope.

## Inspect progress and research files

Runtime automatically maintains:

```text
directions/<status>/<direction_id>.json
experiments/<experiment_id>/experiment.json
records/<gateway_record_id>.json
PROGRESS.md
```

Direction files contain their full decision-useful state, immutable `proposed_by` lineage, current
plan progress, next step and queue rank. `owner` separately identifies the trajectory currently
executing an in-progress Direction. `experiment.json` contains only research semantics and Gateway Record references. Root
`records/` contains every currently visible standardized public result, flat and without per-
Experiment copies. Neither contains private requests, input hashes or executor streams. Kernel
commit IDs link Records to exact Git source. Search these files with normal file tools. They are
regenerated from private Runtime state and must not be edited.

Root `PROGRESS.md` is the bounded research projection and includes the complete eligible per-Shape
Pareto frontier plus a compact Direction queue when Direction recording is enabled. Experiments
remain indexed by their normal directories; open the corresponding file for full visible fields.
Peer Directions may appear as `in_progress(Tn)` in `PROGRESS.md`;
only the owning trajectory may mutate them.

## Propose and select a Direction

A Direction is a falsifiable, optimization-relevant hypothesis. It may propose a direct Kernel
change, but it may also isolate a hardware/compiler capability, microbenchmark a mechanism, investigate
correctness, or attribute performance when that answer will change Kernel design, implementation
selection, or repair. It must name the consequential Kernel decision it informs; generic exploration,
Runtime/evaluator maintenance, infrastructure operation, and unrelated documentation do not belong in
the Direction queue. For a probe-led Direction, `change_scope` describes the Kernel path that would be
unlocked or rejected if the hypothesis is confirmed, not the number of files touched by the probe.

```json
{
  "action": "propose",
  "name": "Split KV across CTAs",
  "change_scope": "major_redesign",
  "hypothesis": "KSPLIT can hide long-KV memory latency enough to pay for reduction",
  "applicability": "Long-context regimes with enough KV work to occupy multiple CTAs",
  "expected_benefit": "Reduce long-context latency without regressing short-context dispatch",
  "plan": [
    "Compile a correct KSPLIT=2 capability probe",
    "Evaluate the complete workload",
    "Compare KSPLIT=1/2/4 under the same allocation policy"
  ],
  "stop_conditions": ["Reduction cost exceeds the measured memory-latency opportunity"]
}
```

```bash
python3 tools/sandbox.py --kind update-direction --request-file scratch/direction.json
```

`applicability` states the workload, Shape regime, semantic case or hardware condition where the
hypothesis should hold. `expected_benefit` states the predicted observable Kernel improvement or
the consequential design decision that the investigation can unlock; avoid unsupported precision,
but name the affected metric, bottleneck, or branch in the implementation strategy. `stop_conditions` is optional.
`relationship`, `derived_from_direction_ids`, and
`derived_from_experiment_ids` may describe `refinement`, `correction`, `combination`, `alternative`,
`reimplementation`, or `contradiction`. Reuse the existing ID for an unchanged hypothesis. Propose a
new idea only after refreshing the catalog and checking likely matches. These fields record
genealogy only. A derived Direction receives its own lifecycle, plan, owner and queue position; it
does not inherit parent status, Experiments, Gateway evidence, Kernel source or priority. Materialize
and adopt an exact parent Kernel separately when the new work actually continues that source.
Runtime validates visible parents, parent counts and cycles, but not whether the chosen relationship
label is scientifically correct.

Correct an existing open local Direction without inventing a replacement identity:

```json
{
  "action": "revise",
  "direction_id": "direction_11111111111111111111111111111111",
  "name": "Split KV across CTAs",
  "change_scope": "major_redesign",
  "hypothesis": "KSPLIT benefits only sufficiently long KV regimes",
  "applicability": "Long-context regimes with enough KV work to occupy multiple CTAs",
  "expected_benefit": "Reduce long-context latency without regressing short-context dispatch",
  "plan": ["Measure the KSPLIT crossover", "Evaluate KSPLIT=1/2/4"]
}
```

`revise` keeps `direction_id`, lifecycle status and genealogy, increments `revision`, and reruns
Review. It replaces the claim, scope and plan in the latest projection. Only `proposed` or
`in_progress` local Directions can be revised; start a deferred, blocked, completed or abandoned
Direction first. Shared peer Directions remain read-only. Prior submissions and Reviews remain in
the private Supervisor audit. The reviewer resumes the latest valid Review Session for this
Direction so it can compare the correction with its previous critique; if that native Session is
unavailable, Runtime retries once in a fresh Session.

Start selected work:

```json
{
  "action": "start",
  "direction_id": "direction_11111111111111111111111111111111",
  "analysis": "Highest expected impact; the first probe is bounded and reversible"
}
```

A trajectory may own multiple local active Directions. Keep concurrent work intentional, address
updates by Direction ID, and leave new ideas proposed until work actually starts. Continue each
selected redesign through bounded edit/measurement cycles instead of reporting its first promotable checkpoint.

Reorder the durable queue:

```json
{
  "direction_id": "direction_11111111111111111111111111111111",
  "before_direction_id": "direction_22222222222222222222222222222222",
  "reason": "The profile makes this the highest-information structural experiment"
}
```

```bash
python3 tools/sandbox.py --kind prioritize-direction --request-file scratch/priority.json
```

Use `after_direction_id` instead of `before_direction_id`, or omit both to move the Direction to the
end. Mark a completed zero-based plan step:

```json
{"direction_id":"direction_11111111111111111111111111111111","plan_step":0}
```

```bash
python3 tools/sandbox.py --kind progress-direction --request-file scratch/progress.json
```

## Record an Experiment

Record a research conclusion when it becomes useful. Cite exact Gateway Record IDs and supporting
workspace material when they exist:

```json
{
  "name": "KSPLIT=2 complete-workload measurement",
  "intent": "Determine whether split KV pays for its reduction cost",
  "gateway_record_ids": ["gateway-0123456789abcdef0123456789abcdef"],
  "interpretation": "Long-KV regimes improved, while short-KV remains unresolved",
  "references": ["scratch/ksplit-analysis.md", "profiles/ksplit2"]
}
```

```bash
python3 tools/sandbox.py --kind record-experiment --request-file scratch/experiment.json
```

When Directions are enabled and exactly one local Direction is active, Runtime supplies its
`direction_id`; with zero or multiple active Directions, include the ID explicitly. When Directions
are disabled, omit it.

Runtime validates every cited ID; its standardized public result is available under root `records/`.
Correctness, latency, failure, operation and Kernel commit identity live only in those records. Use
Git to inspect source changes between the cited commits. Do not copy these facts into
`experiment.json` or `interpretation`.

`gateway_record_ids` and `references` are independent optional arrays. Either may be omitted or
empty. An Experiment without Gateway Records remains a useful research note, but cannot by itself
support a measured `supported`/`refuted` Direction verdict or candidate promotion.

Revise a local Experiment by resubmitting its required fields plus the `experiment_id` shown in
`experiments/<experiment_id>/experiment.json`. Runtime keeps that identity, increments `revision`,
reruns independent Review, and updates the projected latest version. Omitted optional evidence is
preserved; explicit empty arrays clear it. `direction_id` is immutable, and shared peer Experiments
cannot be revised locally. Prior submissions, Review receipts, conversations, and referenced bytes
remain available to the Supervisor's private audit even though the Agent sees only the latest revision.
The reviewer resumes that Experiment's latest valid Review Session; if native resume is unavailable,
Runtime falls back to one fresh Review Session without discarding the earlier audit records.

Every non-empty `references` entry must already exist and name a regular file or directory inside the Agent
workspace. Paths may not overlap or traverse links. Runtime snapshots at most 32 roots, 256 files,
16 MiB per file and 64 MiB total. It rejects the Experiment without mutation when a path is missing
or unsafe. On success, `experiment.json` contains rewritten paths such as
`experiments/<experiment_id>/references/scratch/ksplit-analysis.md`; these archived bytes are
independent of later edits to the originals and are shared with the Experiment at the configured
peer-publication boundary (a synchronous Epoch barrier or an asynchronous terminal-Episode publication).

Optional Wiki attribution retains `wiki_usage_status`, `wiki_query_ids`, and `wiki_usage`. Each usage
row has `query_id`, `disposition`, and the returned `wiki_id` or `cite`. Do not invent identifiers.

## Close, defer, or preserve a Direction

Close only when the research state is truly terminal:

```json
{
  "action": "complete",
  "direction_id": "direction_11111111111111111111111111111111",
  "analysis": "The planned KSPLIT family was implemented and measured",
  "hypothesis_status": "supported",
  "supporting_experiment_ids": ["experiment_22222222222222222222222222222222"]
}
```

`complete`, `abandon`, `block`, and `defer` use the same fields. Direction-only mode omits
`supporting_experiment_ids`. `hypothesis_status` is `unresolved`, `supported`, or `refuted`.
Unresolved `defer`, `block`, or `abandon` also requires a non-empty `reopen_condition` describing
what evidence or capability should reactivate it. Active Directions may cross an Episode boundary
only when a pooled search reports `status=candidate_ready` with
`handoff_reason=cross_trajectory_share`; ordinary terminal handoff
requires all local active Directions to be completed or abandoned. For a blocked handoff, close all
local active Directions with `block` first.

## Adopt a measured peer Kernel

First materialize the source through a shared Git ref, `cherry-pick`, or `kernel-read`. Then audit the
adoption:

```json
{
  "kernel_commit_id": "0123456789abcdef0123456789abcdef01234567",
  "source_ref": "refs/atrex/shared/T2/e0004/head",
  "reason": "Use the peer split-KV family as the base for this trajectory's independent refinement"
}
```

```bash
python3 tools/sandbox.py --kind adopt-kernel --request-file scratch/adopt.json
```

Runtime rejects the request unless the current SourceProvider-declared candidate bytes exactly match
the cited Git-identified Kernel source. Adoption does not assert correctness, performance, or promotion; evaluate
it normally.

## Submit an Episode report

Candidate:

```json
{
  "status": "candidate_ready",
  "handoff_reason": "direction_terminal",
  "summary": "A measured checkpoint is ready for independent gates",
  "selected_experiment_id": "experiment_22222222222222222222222222222222"
}
```

With Experiments disabled, use `gateway_record_ids` instead. The selected evidence must include a
passing standard full Evaluate for the exact current source. Subset Evaluate, Profile, Dev, Check,
Disassemble, and ABBA alone do not satisfy nomination.

```bash
python3 tools/sandbox.py --kind episode-report --request-file scratch/episode-report.json
```

`handoff_reason` is required. Use `direction_terminal` after completing or abandoning the selected
Direction. In pooled search, `cross_trajectory_share` with `status=candidate_ready` publishes a
reusable checkpoint backed by a passing standard full Evaluate of the exact committed source and
may leave that Direction active.
Use `blocked` only with `status=blocked` and a non-empty `blocker`. If a local Direction is active,
close it with `block` first. For example, pivot is
`{"status":"pivot","handoff_reason":"direction_terminal","summary":"..."}`. A repairable rejection
leaves the Episode open; follow `error.next_action` and resubmit only when the stated handoff condition
is satisfied. Acceptance is a handoff, not a promotion. Stop after acceptance.
