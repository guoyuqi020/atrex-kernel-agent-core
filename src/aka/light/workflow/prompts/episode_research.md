## Prior iteration state

Use Journal list/load tools and optional `memory/v*.json` summaries for relevant historical
Directions and Experiments. Treat prior analysis as interpretation, not measurement authority.
Revisit a rejected Direction only with new evidence or a materially different implementation.

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
   or research/profile without a concrete purpose.
4. **Implement and validate.** Work on one coherent candidate at a time. Use Gateway Evaluate for
   correctness and performance, reusing a matching saved result instead of repeating the measurement.
5. **Record and decide.** Record each decisive result as an Experiment, separating measured facts
   from analysis. Use the evidence to continue, repair, restore a measured Kernel, or change Direction.
   Prepare the report as work proceeds, but do not submit it merely because one candidate is already
   promotable. Submit an evidence-backed candidate, pivot, or blocker using the terminal contract
   below only after no remaining concrete high-value step fits the active-time budget. When deferring
   a Direction, record the smallest next probe and the exact evidence or budget constraint that stopped
   it. If the same high-value Direction was previously deferred without new contrary evidence, test its
   bounded probe before starting a lower-value Direction.
