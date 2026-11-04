## GPU measurements

- Measure candidate correctness and performance:
  ```bash
  python3 tools/sandbox.py --kind run --no-sync
  ```
  Reuse exact matching saved results when available. On `candidate_ready`, the Supervisor automatically applies the evaluator-specific terminal correctness policy, including its configured coverage, seeds and repetitions. You do not need to reconstruct or request that gate manually. If it fails, inspect the returned diagnostics and Record, repair the candidate and its evidence, then resubmit. Runtime handles batching, repetitions, aggregation and retries.
- Use Profile, a minimal custom Dev probe, or a full-workspace Command only when it answers an unresolved optimization question. Reuse saved Gateway records instead of repeating an identical completed measurement.
