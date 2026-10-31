## Prior iteration state and engineering loop

At Episode start, inspect the injected Board and refresh `list-directions` when needed so every
visible Direction is considered. Load the queue leader and plausible alternatives in full before
choosing; do not bulk-load unrelated closed entries. Choose the highest-value Direction, not merely the easiest edit. Reuse its ID when the hypothesis
is unchanged; otherwise propose a nonduplicate Direction and start it. Record the mechanism, scope,
falsifiable expected outcome, concrete plan, and any additional stop conditions. Preserve the
current best while testing major redesigns with bounded probes.

Implement and measure coherent candidates with Gateway tools. Measurement facts are saved as
Gateway Records; inspect them with `record-read`. Experiment recording is disabled, so do not call
Experiment tools or add `supporting_experiment_ids`. Close each in-progress Direction with an
`analysis` and `hypothesis_status=unresolved|supported|refuted`; base the verdict on measured facts
without copying those facts into the Direction. New ideas may be proposed while another Direction
is active and left proposed for a later Episode.

Do not stop merely because one candidate is promotable. Submit only after no remaining concrete
high-value step fits an explicitly configured active-time budget. State the smallest next probe for
deferred work. Prior analysis is interpretation, not measurement authority.
