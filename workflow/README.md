# Workflow

`main.py` is the sole executable Workflow entry in this Agent Revision. It defines how one Epoch
uses the bounded orchestration services exposed by `runtime.py`.

Runtime may construct an initial production or ablation Revision by replacing `main.py` with a
controlled external template. Alternative arm programs are not included in the sealed Revision and
are therefore not visible to Optimizer or Evolver sessions.

Evolver may modify `main.py`, `runtime.py`, or add supporting modules under this directory. The
Workflow may organize the granted Attempt budget and route trusted same-Epoch Kernel or Runtime
State outcomes, but it cannot change evaluation, gates, promotion, capabilities, hidden inputs, or
resource limits owned by Runtime.

State flow is executable Workflow behavior. Each Trajectory starts with an immutable initial State;
every later round also uses that State unless `main.py` explicitly routes a completed Attempt's
`output_state` into the Trajectory. Runtime validates and materializes the selected State but does
not choose a reset-versus-retain policy.

`limits.optimizer_attempts` is a hard capacity. Normal multi-Branch organizations spend it exactly.
Controlled Challenger-only evolution topologies may omit Active and run only the sole Challenger,
which must spend the exact configured single-Branch budget; Runtime performs no same-Epoch Agent
comparison.
