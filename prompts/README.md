# Prompts index

Store the Agent's versioned phase prompts and tool-protocol instructions. This directory is
read-only in managed Optimizer and Bootstrap sessions; Evolver may revise it for a later Agent
Revision.
Injected authority, tool schemas, and evaluation rules remain controlled by the trusted controller.

When evolving the Agent, update this README after adding, changing, renaming, or removing a prompt,
including its path, phase,
purpose, and dependencies. Preserve the paths referenced by the Agent configuration unless that
configuration is updated too. Do not store credentials, temporary requests, or raw session traces.

## Contents

- episode.md: Optimizer methodology, including explicit adoption of matching historical evidence;
  depends on the shared Session tools and trusted Runtime Journal/evaluation contract.
- framework_baseline.md: Bootstrap methodology and honest blocked handoff without fabricated
  experiments; depends on the shared Session report schema and Runtime Bootstrap validation.
- generalize_agent_problem.md: public operator-contract generation.
- attempt-tools.md: CLI instructions and request/report examples shared by Attempts and Bootstrap,
  including the 1 MiB request limit, paired input/Shape file examples, custom-input and
  correctness-only evaluation, exploratory versus authoritative ABBA, historical `adopt` decisions,
  zero-experiment blocked/pivot reports, and local file-error repair; depends on
  `src/runtime_tools.py`, `src/tool_contracts.py`, and the trusted Runtime request/report contracts.
