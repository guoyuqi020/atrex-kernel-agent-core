## Prior iteration state and engineering loop

At Episode start, call `list-experiments`, then load the Experiments relevant to the current Kernel
and likely bottleneck. Treat their analysis as interpretation and their cited Gateway Records as
measurement authority. Direction recording is disabled: do not call Direction tools and omit
`direction_id` from Experiment requests.

Choose and implement the highest-value coherent change. After each decisive measurement, record an
Experiment with a concrete falsifiable prediction, the actual change, Gateway Record IDs, and a
concise analysis. Use `record-read` instead of copying measurement facts into prose. Preserve the
current best while testing major redesigns with bounded probes. Do not stop merely because one
candidate is promotable; continue until no remaining high-value step fits an explicitly configured
active-time budget.
