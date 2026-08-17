# Atrex Kernel Agent Core Design

English | [中文](design.zh.md)

## 1. Role

Atrex Kernel Agent Core is an untrusted-to-Runtime, self-evolvable Optimizer Bundle. Runtime seals
the complete repository at one content digest and launches its declared entrypoint in a fresh
sandboxed process. Core determines how an Agent reasons about and modifies a Kernel; it does not
own the optimization campaign lifecycle.

The split is intentional:

| Core owns | Runtime owns |
| --- | --- |
| Agent backend, Prompt, Skills, tool presentation, experiment/report authoring | Campaign/Lineage/Epoch/Attempt state and fencing |
| Candidate edits under `work/kernel` | Workspace construction, isolation, process limits, and cleanup |
| Focused Gateway/Wiki requests through the canonical client | Capability issuance, external credentials, quotas, idempotency, and external clients |
| Provider usage observation, unredacted Session capture, and normalized usage index | Token budget, usage validation, immutable Artifact storage |
| Optimization hypotheses and interpretations | Correctness/performance authority, retention, promotion, rollback |

An Evolver runs outside the parent and candidate repositories. It may propose changes to any tracked
Core file in a private candidate copy, but Runtime validates the resulting repository, records exact
changed paths, evaluates the Challenger independently, and controls promotion.

## 2. Executable repository

`atrex-bundle.json` is the import boundary. Runtime accepts only a strict version-1 manifest with
bundle format `atrex-kernel-agent-bundle-v1` and one safe regular-file entrypoint. It rejects
links, special files, Git metadata, unresolved submodules, unsafe paths, oversized files, and bundle
limit violations before sealing the tree.

`atrex-agent.json` is evolvable behavior configuration. Version 2 contains:

- `agent_backend`: `claude`, `codex`, `pi`, or `qodercli`;
- `reasoning_effort`: `low`, `medium`, `high`, or `max`;
- backend-specific `session_settings` serialized as a string; and
- exact Prompt paths for all supported phases.

Core never selects a second entrypoint or invokes a nested control plane.

## 3. Phase dispatcher

`src/main.py` dispatches only on `ATREX_CORE_PHASE`:

```text
problem_generalization -> sessions/problem_generalization.py
framework_baseline     -> sessions/lineage_bootstrap.py
optimization_attempt   -> sessions/attempt.py
```

Each phase validates a Runtime-authored manifest, fixed workspace paths, report/token destinations,
and the materialized repository identity before starting the configured Agent backend. Unsupported
versions, unknown paths, missing capabilities, or inconsistent environment values fail closed.

### 3.1 Problem generalization

This is the only phase that sees evaluator-private reference, input, shape, and optional aggregate
metadata files. It has no Gateway or Wiki network authority. The Agent must publish one bounded
`atrex.agent_problem.v1` JSON document that preserves operator semantics while hiding exact evaluator
cases and reversible distributions. The session wrapper adds the controller-owned schema metadata
after successful generation; Runtime then independently validates privacy and schema constraints
before sealing the Artifact.

### 3.2 Framework baseline

Runtime supplies one DSL seed Kernel, the public Agent Problem, and a pre-Lineage Gateway/Wiki
capability. Core may edit only `work/kernel` and must finish with a framework-baseline report bound to
the exact authoritative Evaluate outcome. Runtime creates the Baseline Kernel Revision and Ready
Lineage only after report/outcome reconciliation.

### 3.3 Optimization Attempt

Runtime supplies the incumbent Kernel, public Agent Problem, one promoted-lineage Evidence view,
and one immutable Core Revision. The view organizes promoted history and earlier Attempts from the
currently selected revision by Epoch without exposing Active/Challenger roles. The Agent follows one attributable engineering
direction and uses Runtime tools for every GPU or Wiki operation. It records experiments as they
happen and publishes exactly one terminal Attempt report. Candidate publication is evidence, not a
promotion decision.

## 4. Workspace and authority

The normal Attempt layout is fixed by manifest protocol version 6:

```text
attempt.json
input/kernel/
input/evidence/
input/evidence/manifest.json
input/evidence/bootstrap/
input/evidence/epochs/
input/agent-problem/
agent/optimizer/
work/kernel/
sessions/
scratch/
```

Only `work/kernel`, `sessions`, and `scratch` are writable. Runtime owns mount policy and may enforce
that boundary with bubblewrap and cgroup v2. The Evaluation Contract is represented by a digest and
never materialized for the Agent.

`runtime_tools.py` is the canonical Core client for Runtime HTTP capabilities. The delegated,
Attempt-scoped capability is visible to the untrusted Worker, so this client is not a credential
isolation boundary. Runtime remains authoritative even if an Agent constructs a direct proxy
request. The client:

- attaches the current candidate tree to Gateway operations;
- binds every request to the Runtime identity and capability;
- provides live external Wiki queries without exposing the upstream Wiki credential;
- writes an atomic, contiguous experiment journal; and
- validates the shape of terminal baseline and Attempt reports before submission.

Tool results remain Agent-visible evidence. Runtime separately freezes authoritative Gateway
outcomes and Wiki interactions.

## 5. Agent sessions and token accounting

All phases create a new provider session. `src/backends/` normalizes Claude, Codex, Pi, and Qoder
processes into a common result and Session Event stream. The launch layer uses an explicit
environment, isolated backend homes where required, a process group, timeout/reaping, and bounded
stdout/stderr capture.

When Session capture is enabled, Core creates the Runtime-selected Trace directory only after the
Agent process exits. It stores the exact Prompt, captured Provider stdout/stderr, and the raw Codex
rollout when Codex is selected. It performs no redaction, event filtering, or text rewriting on
those files. `events.jsonl` is a separate normalized usage index for Runtime projection; it does not
replace the raw files. `session.json` records capture completeness and trusted process diagnostics.
An output overflow, missing Codex rollout, unsafe raw-file path, or Agent-created Trace path fails
closed. Safety bounds prevent unbounded capture, but incomplete data is never reported as complete.

The live observer counts provider-reported uncached input, output, cache-read, and cache-write tokens
equally. Reaching the Runtime budget terminates the process group. Core always writes a strict token
report; Runtime rejects missing, incomplete, or internally inconsistent accounting. Wall-clock
timeouts are safety limits, not optimization iteration quotas.

Core has no nested plan-review implementation because its provider usage could not be hidden from
the phase budget. Planning remains inside the selected primary Agent session.

## 6. Evidence and memory

Core has no durable local campaign database. A fresh Attempt reconstructs history from immutable
inputs:

- the unified Evidence view contains the promoted prior-Epoch Agent lineage and bounded projections;
- the current Epoch contains only contiguous earlier Attempts from the selected revision;
- the public Agent Problem describes stable operator constraints; and
- the incumbent Kernel is the exact checkpoint selected by Runtime.

The Agent-authored experiment journal is local to one Attempt until Runtime seals its terminal
report. Core contains no second local memory manager; Runtime Evidence and the terminal report are
the only cross-session handoff.

## 7. Knowledge and GPU execution

Core contains no Wiki corpus or local Gateway. `wiki-query` reaches the independent GPU Wiki through
a Runtime proxy and returns one frozen response with source identity. `gateway-execute` reaches Agate
through Runtime and supports only the operations granted to that Attempt. GPU validation, profiling,
compiler inspection, and development commands therefore remain observable and quota-controlled.

Only a correct authoritative Evaluate result can enter Runtime retention or promotion comparison.

Bubblewrap provides filesystem, process, and resource isolation. Its `host` network mode does not
implement destination filtering; production deployments that require live Agent-provider and
Runtime service access must enforce egress policy outside bubblewrap. `isolated` mode provides no
network. This limitation is explicit and is not delegated to Agent instructions.

## 8. Evolution boundary

A Core Revision is the digest of the complete tracked repository. Evolution may change prompts,
skills, backend selection, adapters, analysis tools, and workflow implementation. It cannot change:

- Runtime code or deployment configuration;
- sandbox mounts, credentials, capabilities, or quotas;
- Registry, Artifact, Gateway, or Wiki service state;
- the immutable parent Revision or Evidence inputs; or
- Runtime's Kernel/Agent comparison and promotion decisions.

Runtime evaluates Active and Challenger from the same Kernel and Evidence checkpoint. A better Kernel
may be retained independently of whether the producing Agent Revision is promoted.

## 9. Required invariants

1. One Runtime launch executes exactly one supported phase and one fresh primary Agent session.
2. Core reads only declared inputs and writes only declared output roots.
3. Exact evaluator cases remain private outside problem generalization and Gateway.
4. Every GPU/Wiki operation uses Runtime-issued scoped authority.
5. Every decisive experiment is recorded before terminal reporting.
6. A candidate report never overrides correctness, latency, retention, or promotion facts.
7. Token usage is measured from provider evidence and is never estimated.
8. No Git branch, worktree, local daemon, or in-process memory is a cross-Attempt handoff mechanism.
