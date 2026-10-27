## Runtime Journal and handoff

At the beginning of each Episode, call `list-directions` before proposing or starting work. Review
every visible summary, load the strongest candidates as needed, choose the most important Direction,
and explain why it outranks the alternatives in the `start` event's `analysis`. Do not select a local
change merely because it is easier than a higher-value redesign. Use `update-direction` before
exploring the selected Direction. Advance at most three Directions per Episode, with only one
`in_progress` at a time; additional proposals may remain for later. Put plans in
`plan`, `success_criteria`, and `stop_conditions`, and classify `change_scope` as `local` or
`major_redesign`. A major redesign should begin with a bounded, reversible probe while preserving
the current best. When work reveals a distinct new idea, refresh `list-directions` and compare all
visible hypotheses before proposing it. Reuse the existing ID for the same hypothesis and use an
Experiment for implementation variants. A genuinely new idea may be proposed immediately without
closing or interrupting the active Direction; leave it `proposed` until deliberately selected later.
Use `record-experiment` after decisive results. State a concrete falsifiable prediction in the
Experiment's `hypothesis`, cite Gateway Record IDs, describe the actual `change`, and provide only
your interpretation in `analysis`. Gateway Records are the measurement facts; call `record-read` for
their saved results instead of copying measurement values into the Experiment. Reuse saved results
and exact source when applicable; separate plan/profile documents are not required.

Close every in-progress Direction before `episode-report`. Each closure must explicitly select
`supporting_experiment_ids` and declare `hypothesis_status=unresolved|supported|refuted`.
Lifecycle is not a hypothesis verdict. Every Experiment needs at least one real Kernel-bound Gateway
Record; failed diagnostics may document an unresolved blocker, but supported/refuted requires a
completed observation in every selected Experiment. Runtime validates bindings, not causal relevance.
Never invent evidence to close a Direction. Late Experiments update `associated_experiment_ids`
without reopening a Direction or changing its selected support. For a candidate, select a current-Episode
Experiment citing a passing Evaluate for the exact candidate source left in the workspace
(including every file of a contracted source tree);
Profile or ABBA alone is insufficient for this handoff. Prepare records while working, not only at
termination. Correct rejected reports and submit again; stop after acceptance.

Chat text is not a handoff. Submit JSON in the `runtime-records` Skill's format with
`python3 tools/sandbox.py --kind episode-report --request-file scratch/episode-report.json`.
