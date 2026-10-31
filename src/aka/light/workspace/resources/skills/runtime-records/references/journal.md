# Research Journal requests

All commands use the Session-scoped Runtime client. Put mutation requests below `scratch/`.
Requests must fit the documented 2 MiB envelope.

## Inspect the Board and catalogs

```bash
python3 tools/sandbox.py --kind research-board
python3 tools/sandbox.py --kind list-directions --output-path scratch/directions.json
python3 tools/sandbox.py --kind list-experiments --output-path scratch/experiments.json
python3 tools/sandbox.py --kind load-direction --record-id direction_...
python3 tools/sandbox.py --kind load-experiment --record-id experiment_...
```

The Board is a bounded projection. Direction lists contain only `direction_id`, `name`,
`change_scope`, and `status`; Experiment lists contain only `experiment_id`, optional
`direction_id`, and `name`. Load a record for its full visible fields. Peer Directions may appear as
`in_progress(Tn)` on the Board; only the owning trajectory may mutate them.

## Propose and select a Direction

```json
{
  "action": "propose",
  "name": "Split KV across CTAs",
  "change_scope": "major_redesign",
  "hypothesis": "KSPLIT can hide long-KV memory latency enough to pay for reduction",
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

`stop_conditions` is optional. `relationship`, `derived_from_direction_ids`, and
`derived_from_experiment_ids` may describe `refinement`, `correction`, `combination`, `alternative`,
`reimplementation`, or `contradiction`. Reuse the existing ID for an unchanged hypothesis. Propose a
new idea only after refreshing the catalog and checking likely matches.

Start selected work:

```json
{
  "action": "start",
  "direction_id": "direction_...",
  "analysis": "Highest expected impact; the first probe is bounded and reversible"
}
```

A trajectory owns at most one local active Direction. It may start at most three distinct Directions
per Episode. A major redesign can remain active when the Episode reports a checkpoint.

Reorder the durable queue:

```json
{
  "direction_id": "direction_...",
  "before_direction_id": "direction_...",
  "reason": "The profile makes this the highest-information structural experiment"
}
```

```bash
python3 tools/sandbox.py --kind prioritize-direction --request-file scratch/priority.json
```

Use `after_direction_id` instead of `before_direction_id`, or omit both to move the Direction to the
end. Mark a completed zero-based plan step:

```json
{"direction_id":"direction_...","plan_step":0}
```

```bash
python3 tools/sandbox.py --kind progress-direction --request-file scratch/progress.json
```

## Record an Experiment

Run the appropriate Runtime measurement first. Then cite its exact Gateway Record IDs:

```json
{
  "name": "KSPLIT=2 complete-workload measurement",
  "intent": "Determine whether split KV pays for its reduction cost",
  "gateway_record_ids": ["gateway-..."],
  "interpretation": "Long-KV regimes improved, while short-KV remains unresolved"
}
```

```bash
python3 tools/sandbox.py --kind record-experiment --request-file scratch/experiment.json
```

When Directions are enabled and exactly one local Direction is active, Runtime supplies its
`direction_id`; otherwise include the ID explicitly. When Directions are disabled, omit it.

Runtime derives and stores `before_kernel_id`, `after_kernel_id`, bounded source-change summary,
operations, correctness, latency summary, per-regime summary, failure class, and measurement
digests. These are measurement facts. Do not copy them into `interpretation`; inspect the cited
record with `record-read`.

Optional Wiki attribution retains `wiki_usage_status`, `wiki_query_ids`, and `wiki_usage`. Each usage
row has `query_id`, `disposition`, and the returned `wiki_id` or `cite`. Do not invent identifiers.

## Close, defer, or preserve a Direction

Close only when the research state is truly terminal:

```json
{
  "action": "complete",
  "direction_id": "direction_...",
  "analysis": "The planned KSPLIT family was implemented and measured",
  "hypothesis_status": "supported",
  "supporting_experiment_ids": ["experiment_..."]
}
```

`complete`, `abandon`, `block`, and `defer` use the same fields. Direction-only mode omits
`supporting_experiment_ids`. `hypothesis_status` is `unresolved`, `supported`, or `refuted`.
Unresolved `defer`, `block`, or `abandon` also requires a non-empty `reopen_condition` describing
what evidence or capability should reactivate it. An active Direction may remain open across Episode
and Epoch boundaries; reporting a valid checkpoint does not force closure.

## Adopt a measured peer Kernel

First materialize the source through a shared Git ref, `cherry-pick`, or `kernel-read`. Then audit the
adoption:

```json
{
  "kernel_id": "kernel-...",
  "source_ref": "refs/atrex/shared/T2/e0004/head",
  "reason": "Use the peer split-KV family as the base for this trajectory's independent refinement"
}
```

```bash
python3 tools/sandbox.py --kind adopt-kernel --request-file scratch/adopt.json
```

Runtime rejects the request unless the current `kernel.py` and `implementation/` bytes exactly match
the cited Kernel Artifact. Adoption does not assert correctness, performance, or promotion; evaluate
it normally.

## Submit an Episode report

Candidate:

```json
{
  "status": "candidate_ready",
  "summary": "A measured checkpoint is ready for independent gates",
  "selected_experiment_id": "experiment_..."
}
```

With Experiments disabled, use `gateway_record_ids` instead. The selected evidence must include a
passing standard full Evaluate for the exact current source. Subset Evaluate, Profile, Dev, Check,
Disassemble, and ABBA alone do not satisfy nomination.

```bash
python3 tools/sandbox.py --kind episode-report --request-file scratch/episode-report.json
```

Pivot uses `{"status":"pivot","summary":"..."}`. Blocked adds a non-empty `blocker`. A repairable
rejection leaves the Episode open; follow `error.next_action` and resubmit. Acceptance is a handoff,
not a promotion. Stop after acceptance.
