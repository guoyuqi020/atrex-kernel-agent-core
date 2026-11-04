# Light Workflow internals

This package is private to `aka.light.workflow.plugin`, not a separate startup command.
The checkout's `docs/optional-light-modules.md` describes composition, options and supported
migration paths. The upstream Workflow does not import these mechanics.

`campaign.py` coordinates Episode budgets, continuation, report checks and promotion.
`session.py` borrows the selected public Agent service; it does not construct another backend.
Workspace operations are delegated to the selected CandidateWorkspace implementation.
`journal.py` and `protocol.py` consume controller projections of the private Runtime Journal.
`recorded_verifier.py` requests or reuses evidence through the selected Tool Runtime.

The Agent receives one trajectory-persistent local Git repository and submits `episode-report`.
The repository is an untrusted engineering aid; a passing report still seals exact measured source
in an Episode-scoped Supervisor worktree, so subsequent edits cannot change that candidate. Acceptance remains separate
from promotion. Failed, pivoted or blocked Episodes preserve their research evidence without
replacing the incumbent Kernel. Private checkpoints and promotion audits support restart and
operator repair; canonical `memory/vN.json` records remain Supervisor-owned.

Controller-side implementation files, evidence and Git metadata are never installed as Agent
tools. Only the reviewed clients, prompts and Skill manifests are published to the draft.
