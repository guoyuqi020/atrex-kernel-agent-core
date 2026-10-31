## Runtime research and handoff

Start each Episode from the injected Research Board. Refresh it with `research-board`, then use
`list-directions` and `load-direction` before selecting work. Prefer the highest-value unresolved
Direction. A local trajectory may own one `in_progress` Direction; peer entries such as
`in_progress(T2)` are read-only and do not block your lease. Use `prioritize-direction` to change the
durable queue and `progress-direction` after completing a plan step.

Classify a Direction as `local` or `major_redesign`. Preserve the current best and use bounded,
reversible probes, but continue a valuable major redesign across Episodes until a milestone, stop
condition, blocker, or completion. An accepted candidate is a checkpoint, not a reason to discard
unfinished structural work. A genuinely different idea may be proposed while another Direction is
active; check visible Directions first and leave the new idea proposed until selected.

After a decisive measurement, use `record-experiment` with only `name`, `intent`,
`gateway_record_ids`, and `interpretation`. Omit `direction_id` when exactly one local Direction is
active. Runtime derives Kernel identities, source-change, operations, correctness, latency and
failure facts from immutable Gateway Records; use `record-read` rather than restating them. Analysis
can be revised and never overrides measurement facts.

You may keep a useful Direction active at `episode-report` so the next Episode can continue it.
Close it only when the research state is actually completed, abandoned, blocked, or deliberately
deferred. A closure records `hypothesis_status` and supporting Experiments; unresolved deferred,
blocked or abandoned work also needs a concrete `reopen_condition`.

Peer Git refs are engineering context. After cherry-picking or restoring an exact measured peer
Kernel, call `adopt-kernel`; Runtime verifies that `kernel.py` plus `implementation/` matches the
Kernel Artifact before recording adoption. Normal Evaluate and promotion gates still apply.

For `candidate_ready`, select a current-Episode Experiment that cites a passing standard full
Evaluate for the exact source left in the workspace. Profile, Dev or ABBA alone is insufficient.
Correct a rejected report and resubmit; stop only after acceptance.

Chat text is not a handoff. Submit JSON with
`python3 tools/sandbox.py --kind episode-report --request-file scratch/episode-report.json`.
