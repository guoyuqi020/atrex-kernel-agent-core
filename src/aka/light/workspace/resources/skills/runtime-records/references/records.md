# Read saved measurement evidence

Runtime projects every visible result as `records/<gateway_record_id>.json`. Read and search those
flat files normally. `record-read` remains a compatibility query for one explicit ID.

| ID | Meaning | Use |
| --- | --- | --- |
| `gateway-…` | One saved measurement operation | Measured evidence, result reader, direct report citation when the Experiment module is disabled |
| full Git commit ID | Exact committed Kernel source; exposed as `kernel_commit_id` | Source reader, measurement index, peer Git history |
| `direction_…` | Research hypothesis and lifecycle | `PROGRESS.md`, `directions/<status>/*.json`, Direction updates |
| `experiment_…` | Interpretation tied to real measurement IDs | `experiments/<experiment_id>/experiment.json`, closure and report |

Agate Job IDs cannot replace these IDs.

```bash
python3 tools/sandbox.py --kind record-read --record-id gateway-11111111111111111111111111111111
python3 tools/sandbox.py --kind kernel-records --kernel-commit-id aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
python3 tools/sandbox.py --kind kernel-read --kernel-commit-id aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa --output-path scratch/previous-kernel
```

Each projected file, and `record-read`, contains the same public result returned by a new measurement:
`operation`, `status`, `result`, `gateway_record_id`, and `kernel_commit_id` when Kernel-bound.
ABBA uses `operation: evaluate` with `result.comparison`, `candidate_kernel_commit_id`, and
`baseline_kernel_commit_id`.
Other operation names are `profile`, `dev`, `check`, and `disassemble`.
Parse the whole stdout as JSON; do not look for legacy executor markers.
Dev preserves probe text in `result.stdout`/`result.stderr` and its process `exit_code`.
Its Kernel is the workspace snapshot, not proof the probe measured it.

The compatibility read exits zero when evidence was retrieved, regardless of the stored outcome.
Inspect `status` (`completed`, `failed`, `cancelled`) and, for Evaluate, `result.correct`.
An evaluator can complete with a rejected Kernel. Missing or corrupt records still fail the query.
Old stream-only records are converted in memory, without changing their stored evidence or
submitting GPU work. Private request/options/input hashes are never included.

`kernel-records` lists Record ID, operation and timestamp, not result bodies. `kernel-read`
writes the complete source tree to the requested scratch directory and acknowledges it. Inspect
that directory before restoring it. A SOL record may still produce one file.

Use returned IDs, not these illustrative examples. Shape parameters are public only for IDs listed
in `public_shapes.json`; unlisted Shape IDs are opaque. Profile duration and
Evaluate latency are different measurements; a diagnostic cannot establish untested performance.
When adopting an older Kernel, cite its saved measurement in a current-Episode Experiment if the
Experiment module is enabled; otherwise cite it directly in `episode-report`.

## Per-Shape historical best

```bash
# Read root PROGRESS.md for the Research Board and per-Shape frontier.
```

The Pareto section of Runtime-managed root `PROGRESS.md` takes no IDs and submits no GPU work:
The Current state section identifies the lowest-GeoMean eligible complete Evaluate as the best
verified whole Kernel and cites its Gateway Record. `best_by_shape` maps each Shape ID to
`kernel_commit_id`, `gateway_record_id`, and `latency_us`;
`winner_kernel_commit_ids` lists the distinct winning sources; `shape_count` counts returned Shapes;
`evaluation_count` counts eligible full Evaluations, not distinct Kernels. With no eligible history,
the map and list are empty and both counts are zero. Use `kernel-read` and `record-read` on returned IDs.

Only completed, passing standard full Evaluations in this Campaign count, across Episodes,
including saved baseline/controller full Evaluations. Custom-input, Shape-subset, correctness-only,
ABBA, Profile and Dev results do not count. Failed, incomplete and invalid records are excluded.
The projection remains available with research recording disabled. Equal latencies break ties by
Kernel commit ID, then Record ID. These are observed minima, not a statistically confirmed bound or a
single Kernel that achieves every minimum; combining implementations requires a new full Evaluate.

## Errors

Research/report validation errors return `ok:false`, `repairable:true`, and `error` with
`code`, `message`, `next_action`; field errors also list missing/unexpected/allowed fields.
Fix the indicated request or lifecycle state and call again. This is not permission to replay an
unknown-outcome mutation. Missing/corrupt private storage and post-write publication failures are
infrastructure errors, not input mistakes.

- Duplicate measurement: read `error.gateway_record_id`; do not submit another GPU task.
- Report rejected: verify exact source and measured evidence. When the Experiment module is enabled,
  check the selected Experiment; otherwise check `gateway_record_ids`. When the Direction module is
  enabled, close or explicitly defer/block all local active Directions as the report contract requires.
  Correct the report and resubmit. No successful finalization occurred; the Agent owns engineering
  commits and the Supervisor independently validates and seals the exact reported HEAD.
- Report publication failed: after storage recovery, resubmit the identical report; the durable
  report is reused. Do not rerun GPU work or modify the reported HEAD/create a replacement commit.
- Unknown transport outcome: inspect `PROGRESS.md` and the projected research files, or ask the
  operator before resubmitting a write.
