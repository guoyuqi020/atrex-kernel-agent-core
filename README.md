# Atrex Kernel Agent Core

English | [中文](README.zh.md)

Atrex Kernel Agent Core is the complete evolvable Optimizer Bundle executed by Atrex Kernel Agent
Runtime. This repository is not a scheduler, benchmark harness, Gateway, GPU Wiki, sandbox manager,
or promotion controller. Runtime imports an exact Git commit, seals the whole tracked tree as one
Kernel Agent Revision, prepares an isolated workspace, and launches the single command declared in
[`atrex-bundle.json`](atrex-bundle.json).

Core owns Agent-visible optimization behavior:

- Agent backend selection and invocation;
- phase-specific prompts and GPU-kernel engineering workflow;
- live provider-token observation, unredacted Session capture, and normalized usage indexing;
- Runtime Gateway and external Wiki tool bindings;
- experiment journaling and terminal report authoring; and
- structured profiling-evidence interpretation inside the selected Agent workflow.

Runtime remains authoritative for Campaign and Lineage state, sandboxing, credentials, token quota,
Evaluation Contracts, Gateway outcomes, Evidence, Kernel retention, Agent promotion, rollback, and
Evolver execution. Core cannot directly read Runtime state or evaluator-private inputs unless a
specific phase materializes them.

## Repository contracts

Two root manifests are intentionally separate:

- [`atrex-bundle.json`](atrex-bundle.json) is the Runtime-facing import contract. It identifies this
  as an `atrex-kernel-agent-bundle-v1` Bundle and declares `src/main.py` as the only
  executable entrypoint.
- [`atrex-agent.json`](atrex-agent.json) provides standalone Agent defaults for `claude`, `codex`,
  `pi`, or `qodercli`, reasoning/session options, and phase Prompt mappings. Managed Runtime
  Sessions apply an authoritative Backend/model/effort/settings binding while leaving Prompt and
  workflow evolution intact. An empty model selects the Backend CLI default.

Runtime sets `ATREX_CORE_PHASE` and the corresponding strict environment/manifest protocol. The
entrypoint supports exactly three fresh-process phases:

| Phase | Purpose | Writable result |
| --- | --- | --- |
| `problem_generalization` | Convert evaluator-private operator inputs into a bounded public Agent Problem. | `work/output/agent_problem.json` |
| `framework_baseline` | Turn one DSL seed into a correct authoritative baseline through Runtime tools. | Kernel tree plus baseline report under `scratch/` |
| `optimization_attempt` | Test one attributable optimization direction from immutable Evidence and an incumbent Kernel. | Candidate Kernel plus terminal Attempt report under `scratch/` |

Every phase runs in a new Agent session and produces a Runtime-validated token report. Core never
resumes process memory between Attempts. Historical experience is supplied through one immutable,
single-lineage Evidence view that combines the promoted cross-Epoch history with only earlier
Attempts from the currently selected revision. Active/Challenger roles are not exposed.

## Runtime workspace

Core validates the Runtime-owned manifest before launching an Agent. A normal Attempt exposes:

```text
<attempt>/
├── attempt.json
├── input/
│   ├── kernel/                 # immutable incumbent
│   ├── evidence/               # immutable unified, Epoch-organized Evidence view
│   │   ├── manifest.json       # trusted scope and source digests
│   │   ├── instructions.md     # Runtime-authored Prompt Fragment
│   │   ├── bootstrap/
│   │   └── epochs/             # promoted lineage plus visible current Attempts
│   └── agent-problem/          # public operator contract
├── agent/optimizer/            # immutable materialized Core Revision
├── work/kernel/                # writable candidate
├── sessions/                   # unredacted Agent-session artifacts
└── scratch/                    # requests, experiment journal, reports, token usage
```

The private Evaluation Contract is referenced only by digest and remains inside Runtime/Gateway.
Runtime injects the Evidence structure instructions into the final Agent Prompt; this repository
only verifies and appends the Digest-bound Fragment and does not own that structure text.
Gateway and Wiki access use short-lived, Attempt-scoped capabilities issued by Runtime. The Worker
can read those delegated capabilities, while upstream Agate/Wiki credentials remain outside the
sandbox. Core writes operation requests under `scratch/` and uses
[`src/runtime_tools.py`](src/runtime_tools.py) as the canonical bounded protocol client; Runtime
still enforces identity, operation allowlists, quotas, idempotency, and result authority.

When enabled by the Runtime manifest, each phase writes one Session Artifact directory:

```text
sessions/<name>/
├── input/prompt.md
├── conversation.jsonl                # observable retained-event transcript
├── provider/stdout.stream-json
├── provider/stderr.log
├── provider/codex-rollout.raw-jsonl  # Codex only
├── events.jsonl                      # normalized usage index
└── session.json                      # capture status and diagnostics
```

`conversation.jsonl` starts with the exact Runtime-supplied user Prompt, embeds every retained
Provider stdout event, includes the raw Codex rollout when applicable, and ends with Runtime's
capture status. It explicitly records that a Provider-managed system Prompt is unavailable when
the CLI does not export it. Prompt and retained Provider files are captured without redaction or
text rewriting. The high-frequency Claude `system/thinking_tokens` estimate event is omitted from
both stdout and the conversation; `session.json.provider_event_filters` records that selection and
the final authoritative Provider usage remains in `events.jsonl`;
reasoning, tool arguments/results, command output, and any sensitive values emitted by the Provider
remain present. Core does not proactively copy credentials that the Provider never emitted. A
bounded-output overflow or incomplete Codex rollout capture fails the phase instead of silently
claiming a complete Trace. Before launch, Core creates this fixed directory with `session.json`
marked `running`, then streams stdout/stderr and mirrors the Codex rollout while the process is
alive. This live view is explicitly unsealed. After reaping the process, Core discards it and
rebuilds the complete final directory from bounded captures before Runtime seals the Artifact.
The Coding Agent may not pre-create or redirect the Runtime-selected Session path.

## Engineering loop

[`prompts/episode.md`](prompts/episode.md) contains the complete optimization Attempt loop:
reconstruct the incumbent, profile progressively, query focused external knowledge, plan one
falsifiable direction, edit and repair, execute one or more exploratory Evaluates, record every
decisive experiment immediately, and publish one terminal report. A terminal outcome is
`candidate_ready`, `pivot`, or `blocked`; Runtime applies its trusted retention policy to the
nominated exact Kernel—either ordinary A/B Evaluate or same-allocation ABBA. The Candidate side of
that comparison becomes the final Kernel Evaluation and also decides retention.

[`prompts/framework_baseline.md`](prompts/framework_baseline.md) defines the narrower
framework-baseline phase.

## Configuration

Edit `atrex-agent.json` in a candidate Revision to change Core behavior. Example:

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

Backend credentials and executable availability are deployment concerns. They are passed only
through Runtime's explicit environment allowlist. Core's live quota observer terminates a backend
when reported provider tokens reach the Runtime budget; missing or inconsistent usage is reported as
an invalid/incomplete accounting outcome rather than guessed.

## Repository layout

```text
.
├── atrex-bundle.json                 # Runtime-facing Bundle and entrypoint contract
├── atrex-agent.json                  # evolvable backend and Prompt configuration
├── src/
│   ├── main.py                       # single Runtime-launched dispatcher
│   ├── agent_config.py               # evolvable Agent configuration reader
│   ├── runtime_tools.py              # trusted Gateway/Wiki/report bindings
│   ├── contexts/                     # strict Runtime manifest and workspace readers
│   ├── sessions/                     # phase prompts, execution, trace, and token reports
│   └── backends/                     # Claude, Codex, Pi, and Qoder adapters
├── prompts/                          # phase instructions
├── tests/                            # Core-owned unit and protocol-client tests
├── pyproject.toml                    # standalone Ruff, mypy, and pytest policy
└── docs/                             # Core design and Runtime-oriented usage
```

There is deliberately no local Gateway, GPU Wiki corpus, benchmark/reference checkout, Git-worktree
campaign engine, or Runtime state store in this Bundle. Those responsibilities are external so that
Core revisions can evolve without acquiring control-plane authority.

See [Design](docs/design.md) for trust and lifecycle details and [Runtime usage](docs/quickstart.md)
for configuration and launch expectations.

## Upstream and citation

This Bundle is derived from the open-source Atrex Kernel Agent project and keeps its GPU-kernel
engineering prompts, Agent adapters, and structured profiling workflow where they remain compatible
with the Runtime contract.

Please cite the [Atrex paper](https://arxiv.org/abs/2607.14541) when appropriate:

```bibtex
@misc{atrex2026,
  title         = {Are LLM-Generated GPU Kernels Production-Ready? A Trace-Driven Benchmark and Optimization Agent},
  author        = {Lingyun Yang and Yuxiao Wang and Shenghao Liang and Linfeng Yang and Daocheng Ying and Chunbo You and Rui Zhang and Luping Wang and Yinghao Yu and Guodong Yang and Liping Zhang},
  year          = {2026},
  eprint        = {2607.14541},
  archivePrefix = {arXiv},
  primaryClass  = {cs.AI},
  url           = {https://arxiv.org/abs/2607.14541}
}
```

Licensed under the [Apache License 2.0](LICENSE).
