# Episode Report with research recording disabled

Direction and research Experiment writes are disabled for this Episode. GPU measurement records
are still saved automatically. Do not invent or edit Runtime control files; use only the documented
`episode-report` request for handoff.
Commit the complete SourceProvider-declared candidate before a Kernel-bound measurement or `candidate_ready`
report and keep the worktree clean. Runtime derives the Kernel commit ID from the full Git HEAD;
do not place a Commit ID in the report. Pivot and blocked reports do not nominate the worktree and
do not require a clean candidate commit.

## Candidate

Run the ordinary evaluator prescribed by the current phase:

```bash
python3 tools/sandbox.py --kind run --no-sync
```

Inspect root `records/<gateway_record_id>.json`. Leave the exact measured candidate paths
committed at HEAD as declared by the SourceProvider contract in `WORKSPACE.md`.
Save `scratch/episode-report.json`, replacing the example ID with the returned `gateway_record_id`:

```json
{
  "status": "candidate_ready",
  "handoff_reason": "direction_terminal",
  "summary": "The candidate passes full evaluation and is ready for independent verification",
  "gateway_record_ids": ["gateway-11111111111111111111111111111111"]
}
```

```bash
python3 tools/sandbox.py --kind episode-report --request-file scratch/episode-report.json
```

The list must be nonempty and contain unique, real Kernel-bound Gateway Record IDs. At least one
must be a completed, passing standard full Evaluate for the byte-identical current candidate.
Matching historical results are allowed. Profile, Dev, ABBA, and correctness-only checks alone
cannot qualify the candidate. Do not supply `selected_experiment_id`.

The Supervisor performs or reuses its configured acceptance checks before accepting and sealing the committed source. In production
this includes source-policy review and supplemental numerical validation when those modules are enabled. Acceptance still precedes independent ABBA
verification and does not guarantee promotion. Success returns `status: "accepted"`.

In pooled search, a reusable measured checkpoint uses the same candidate shape with
`status=candidate_ready` and `handoff_reason=cross_trajectory_share`.

## Pivot or blocker

Do not report the first promotable checkpoint. Continue bounded edit/measurement cycles until the
selected optimization path is completed or exhausted, a pooled search must publish a reusable
checkpoint backed by a passing standard full Evaluate of the exact committed source, or a concrete
blocker prevents further work.

An exhausted approach can finish with:

```json
{"status":"pivot","handoff_reason":"direction_terminal","summary":"The tested approach did not provide a useful improvement"}
```

Here `direction_terminal` is a compatibility enum for a terminal optimization path; Direction
recording is disabled and no Direction object is required.

An infrastructure or authority blocker additionally requires a nonempty `blocker`:

```json
{"status":"blocked","handoff_reason":"blocked","summary":"Cannot validate the candidate","blocker":"GPU service unavailable"}
```

Neither state accepts `gateway_record_ids`. Both allow the existing optional
`accepted_ppu_diagnostics` field when the PPU Skill requires it; referenced files must be in `scratch/`.

## Repair and replay

For an invalid report, follow `error.next_action`: correct its fields or evidence, repair and measure
the Kernel if necessary, then resubmit. Rejection leaves the Episode open. If publication was
interrupted after acceptance, repeat the identical report; this does not repeat acceptance or create
another Commit. Never blindly repeat a GPU request with an unknown outcome.

Request files are limited to 2,093,056 bytes (2 MiB minus 4 KiB); the encoded HTTP body must fit
within 2 MiB. Shorten text or lists before retrying a size rejection.
