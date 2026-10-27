## Runtime Journal and handoff

Direction tools are enabled; Experiment tools are disabled. Use `list-directions`,
`load-direction`, and `update-direction`. Start at most three Directions per Episode and keep only
one in progress. Close every in-progress Direction before reporting. A closure requires
`analysis` and `hypothesis_status`, and must omit `supporting_experiment_ids`.

Leave the complete selected source tree in place. For `candidate_ready`, submit `status`, `summary`,
and `gateway_record_ids`; at least one cited record must be a passing standard full Evaluate for
the exact candidate. For `pivot`, submit only `status` and `summary`; for `blocked`, also include
`blocker`. Do not submit `selected_experiment_id` or manage Git. Correct rejected reports and stop
only after the Runtime accepts the report.
