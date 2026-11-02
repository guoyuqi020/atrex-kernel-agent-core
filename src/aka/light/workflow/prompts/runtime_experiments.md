## Handoff

Write `scratch/episode-report.json` as one of:

```json
{"status":"candidate_ready","summary":"...","selected_experiment_id":"experiment_..."}
{"status":"pivot","summary":"..."}
{"status":"blocked","summary":"...","blocker":"..."}
```

For `candidate_ready`, select a current-Episode Experiment that cites a passing standard full Evaluate for the exact source left in the workspace. Do not add `gateway_record_ids` directly. Submit with `python3 tools/sandbox.py --kind episode-report --request-file scratch/episode-report.json`. Correct a rejected handoff and resubmit; stop only after acceptance. Do not commit—the Supervisor seals the source.
