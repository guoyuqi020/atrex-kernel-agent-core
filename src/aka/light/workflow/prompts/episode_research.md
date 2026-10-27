## Prior iteration state

Before proposing or starting any Direction in this Episode, call `list-directions` and review every
visible Direction summary. Load the strongest candidates when their full plan, evidence links, or
latest analysis affects the choice. Compare expected performance impact, supporting evidence,
implementation cost, unresolved uncertainty, and whether a valuable redesign was previously deferred.
Choose the single most important Direction rather than the easiest local edit, reuse its ID when the
hypothesis is unchanged, and explain the comparison in the `start` event's `analysis`. If no existing
Direction deserves work, propose and start a new one. Use optional `memory/v*.json` summaries as
additional context. Treat prior analysis as interpretation, not measurement authority. Revisit a
rejected Direction only with new evidence or a materially different implementation.

## Engineering loop

Use this as a working guide when useful, not as additional mandatory stages:

1. **Understand the starting point.** Inspect the current Kernel, public contract, and relevant
   history. Identify existing measurements, the likely bottleneck, and what remains uncertain.
2. **Choose a Direction.** State the hypothesis, expected benefit, concrete change, validation
   method, and stopping condition. Classify `change_scope` as `local` or `major_redesign`. For a
   major redesign, preserve the current best and identify a bounded, reversible probe that tests
   the central uncertainty before falling back to easier local work. Register the Direction before
   exploration; keep the concise plan in the Direction Journal rather than creating separate draft
   or plan files.
3. **Research the question.** Use available references, Profile, or an explicitly enabled knowledge
   tool to resolve a specific uncertainty that informs the next change. Do not call disabled Wiki
   or research/profile without a concrete purpose. If research reveals a distinct promising idea,
   refresh `list-directions` before proposing it, compare every visible hypothesis, and load likely
   matches. Reuse an existing Direction for the same hypothesis and record implementation variants as
   Experiments. Otherwise propose the new Direction immediately so it is not lost, but leave it
   `proposed`; do not interrupt the current `in_progress` Direction merely to start the new idea.
4. **Implement and validate.** Work on one coherent candidate at a time. Use Gateway Evaluate for
   correctness and performance, reusing a matching saved result instead of repeating the measurement.
5. **Record and decide.** Record each decisive result as an Experiment. Its `hypothesis` is the
   concrete, falsifiable prediction for this change, not a repetition of the Direction's broader
   hypothesis. Supply the actual `change`, Gateway Record IDs, `analysis`, and decision. The cited
   Gateway Records are the measurement facts; use `record-read` when you need their results instead
   of copying measurement values into the Experiment. Use those facts to continue, repair, restore a
   measured Kernel, or change Direction.
   Prepare the report as work proceeds, but do not submit it merely because one candidate is already
   promotable. Submit an evidence-backed candidate, pivot, or blocker using the terminal contract
   below only after no remaining concrete high-value step fits the active-time budget. When deferring
   a Direction, record the smallest next probe and the exact evidence or budget constraint that stopped
   it. If the same high-value Direction was previously deferred without new contrary evidence, test its
   bounded probe before starting a lower-value Direction.
