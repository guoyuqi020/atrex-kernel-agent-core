## Handoff

Write `scratch/episode-report.json` as one of:

```json
{"status":"candidate_ready","summary":"...","gateway_record_ids":["..."]}
{"status":"pivot","summary":"..."}
{"status":"blocked","summary":"...","blocker":"..."}
```

For `candidate_ready`, cite a passing standard full Evaluate for the exact source left in the workspace; correctness-only, Profile, Dev, and ABBA are insufficient. Do not add `selected_experiment_id`. Submit with `python3 tools/sandbox.py --kind episode-report --request-file scratch/episode-report.json`. Correct a rejected handoff and resubmit; stop only after acceptance. Do not commit—the Supervisor seals the source.
