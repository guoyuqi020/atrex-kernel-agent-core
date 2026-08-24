# Runtime-oriented usage

English | [中文](quickstart.zh.md)

Atrex Kernel Agent Core is not launched as a standalone Campaign CLI. Atrex Kernel Agent Runtime
imports an exact Core Git commit, prepares the workspace and capabilities, then runs
`src/main.py` once for each phase. Calling the entrypoint without Runtime-authored
manifests is expected to fail closed.

## 1. Prerequisites

A deployment needs:

- Atrex Kernel Agent Runtime configured with Registry, Artifact Store, Agate, sandbox, and token
  limits;
- this repository available through Runtime's approved Git Base source at an exact commit;
- Python 3 in the Core worker image;
- the backend selected by `atrex-agent.json` (`claude`, `codex`, `pi`, or `qodercli`);
- backend credentials passed through Runtime's explicit environment allowlist; and
- a reachable Runtime service for Gateway and optional Wiki callbacks.

## 2. Select the Agent backend

Edit `atrex-agent.json` before committing the Core Revision:

```json
{
  "schema_version": 2,
  "agent_backend": "codex",
  "reasoning_effort": "max",
  "session_settings": "",
  "prompts": {
    "problem_generalization": "prompts/generalize_agent_problem.md",
    "framework_baseline": "prompts/framework_baseline.md",
    "optimization_attempt": "prompts/episode.md"
  }
}
```

Supported Backend identifiers are exact. These values are standalone Bundle defaults. In a managed
Session, Runtime injects the authoritative `ATREX_AGENT_BACKEND`,
`ATREX_AGENT_MODEL`, `ATREX_AGENT_REASONING_EFFORT`, and `ATREX_AGENT_SESSION_SETTINGS` binding;
an empty model selects the Backend CLI default. Core rejects incomplete
bindings and applies the complete binding instead of these defaults. Keep credentials out of both
configuration layers.

## 3. Publish or select an exact Core commit

Runtime's `kernel_agent.base_source` fixes the approved repository URL and Git executable. A
Campaign Bootstrap request supplies only a full commit SHA:

```json
{
  "base_revision": {
    "commit": "0123456789abcdef0123456789abcdef01234567"
  }
}
```

Runtime verifies the fetched commit/tree, rejects unsafe content and unresolved or unapproved
submodules, archives it without executing repository code, validates `atrex-bundle.json`, and seals
the resulting Bundle. The current Core tree has no submodules.

## 4. Bootstrap a Campaign

Use Runtime's Campaign schema v3. Common Campaign fields appear once and per-DSL seed and
Evidence inputs live under `lineages`:

```json
{
  "schema_version": 3,
  "creation_key": "vector-add-h100",
  "operator": "vector_add",
  "hardware_target": "nvidia-h100",
  "evaluation_contract": "/trusted/inputs/evaluation.json",
  "base_revision": {
    "commit": "0123456789abcdef0123456789abcdef01234567"
  },
  "challenger_count": 1,
  "challenger_start_epoch": 1,
  "trajectories_per_branch": 1,
  "attempts_per_trajectory": 8,
  "lineages": {
    "triton": {
      "models": {"optimizer": null, "evolver": null},
      "baseline_kernel": "/trusted/inputs/triton-kernel",
      "initial_evidence": "/trusted/inputs/triton-evidence"
    }
  }
}
```

Run the Runtime service first because Core baseline sessions call its Gateway/Wiki routes:

```bash
atrex-kernel-agent-runtime serve --config /etc/atrex/runtime.json

# In another supervised process with the same Runtime secrets and provider credentials:
atrex-kernel-agent-runtime bootstrap \
  --config /etc/atrex/runtime.json \
  --campaign /trusted/inputs/campaign.json
```

The keys of `lineages` are the authoritative DSL set. Runtime creates them in canonical DSL order
and idempotently. Retry the exact Campaign definition after interruption to reuse completed
Lineages and continue the remainder.

Bootstrap runs Core once in `problem_generalization` for a new Campaign and once in
`framework_baseline` for each selected Lineage. Core's Evaluate calls are exploratory. A Lineage
becomes ready only after Runtime seals the final nomination and a fresh Runtime-final evaluation is
correct.

## 5. Run optimization Epochs

After Bootstrap returns the Campaign ID, schedule an absolute target:

```bash
atrex-kernel-agent-runtime run-campaign \
  --config /etc/atrex/runtime.json \
  --campaign campaign_0123456789abcdef0123456789abcdef \
  --target-epoch 10
```

Each Epoch creates Active and Challenger branches from the same checkpoint. Each branch receives a
fixed number of fresh `optimization_attempt` sessions. Within a branch, a retained Kernel becomes
the next Attempt's incumbent; the opposite branch's intermediate results remain invisible. Runtime
applies the configured trusted retention policy to each terminal nomination. Ordinary A/B Evaluate
and same-allocation ABBA both use their Candidate measurements as the final Kernel Evaluation,
without an extra standalone Eval.

## 6. What an Attempt can access

The selected Agent receives fixed paths:

| Purpose | Path |
| --- | --- |
| immutable incumbent Kernel | `input/kernel` |
| writable candidate Kernel | `work/kernel` |
| unified promoted-lineage/current-Attempt Evidence view | `input/evidence` |
| public operator contract | `input/agent-problem` |
| immutable Core Revision | `agent/optimizer` |
| read-only pinned upstream GPU kernel projects | `reference` |
| requests, plan, journal, reports | `scratch` |
| unredacted Agent Session Artifacts and normalized usage index | `sessions` |

The private Evaluation Contract and exact hidden cases are never placed in this workspace. The Agent
uses Runtime tools described in the phase Prompt:

```bash
python agent/optimizer/src/runtime_tools.py gateway-execute \
  --request scratch/evaluate.json

python agent/optimizer/src/runtime_tools.py wiki-query \
  --request scratch/wiki.json

python agent/optimizer/src/runtime_tools.py record-experiment \
  --request scratch/experiment.json

python agent/optimizer/src/runtime_tools.py attempt-report \
  --request scratch/report.json
```

Exact request schemas are enforced by the tool and Runtime protocols. Use a new idempotency key for
new Gateway/Wiki content; replay the same key only with an identical request.

Every Agent `evaluate` retains the exact candidate files and raw outcome. It does not finalize the
Attempt. `candidate_ready` nominates the current `work/kernel`; Runtime performs the authoritative
fresh evaluation after Core exits.

The Worker receives the short-lived scoped capability used by these calls, but never receives the
upstream Agate or Wiki service credential. Runtime authorization remains authoritative for direct as
well as canonical-client requests. Bubblewrap `host` networking is unfiltered; enforce production
egress restrictions at the deployment network layer.

## 7. Local development checks

Core owns its unit tests and static policy. From this repository run:

```bash
python -m pytest -q
ruff check src tests
mypy src tests
```

Runtime separately owns cross-repository protocol and worker integration tests. From a Runtime
checkout containing this Core development checkout, run its complete suite. For a lightweight Core
syntax check that does not create bytecode in the repository:

```bash
PYTHONPYCACHEPREFIX=/tmp/atrex-core-pycache \
  python -m compileall -q src tests
```

Do not fabricate Runtime environment variables to treat `src/main.py` as a local
standalone optimizer. That bypasses the system boundary the Core protocol is designed to enforce.

## 8. Common failures

- **Missing Runtime environment:** the entrypoint was launched outside a prepared phase workspace.
- **Manifest version/path mismatch:** Core and Runtime protocol versions disagree, or a workspace was
  modified after preparation.
- **Gateway/Wiki capability rejected:** the capability expired, exhausted its call quota, was revoked,
  or does not grant the requested operation.
- **Token report incomplete:** the backend did not expose reliable provider usage; Runtime will not
  estimate it.
- **No terminal report:** the Agent exited, timed out, or exhausted budget before publishing a valid
  phase result.
- **Candidate rejected:** no correct exploratory result matched the nomination, or the independent
  Runtime-final evaluation failed;
  inspect immutable Runtime Evidence rather than local process memory.
