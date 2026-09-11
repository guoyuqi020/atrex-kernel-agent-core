# Reusable insights index

Store only compact conclusions that change a later Agent's search decision. An Insight must explain
what to do differently, its operator/DSL/architecture or mechanism scope, the Direction/Experiment
and Kernel/Result evidence it was derived from, contrary evidence, and when it should be revisited.
Use a typed entry such as `heuristic`, `constraint`, `failure_pattern`, `strategy`, or
`open_question`.

Do not copy latency tables, Kernel versions, success/failure status, changes, or other facts that
Runtime Journal and Result tools can reconstruct. Measurements are authoritative; Insights are
Agent-authored interpretations that future Agents may correct or invalidate. Static reference
material belongs in a Skill's references; temporary requests and raw outputs belong in `scratch/`.

Whenever you add, change, rename, or remove content here, update this README with each file's path,
claim, scope, evidence identities, decision effect, and superseded conclusion. Read this index before
adding duplicates. Never store credentials here.

## Contents

No initial Insights. Maintain the current file index here.
