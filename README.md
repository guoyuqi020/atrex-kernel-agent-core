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
- live provider-token observation and normalized Session Trace production;
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
- [`atrex-agent.json`](atrex-agent.json) is evolvable Agent configuration. It selects `claude`, `codex`,
  `pi`, or `qodercli`, sets reasoning/session options, and maps all supported phases to prompts.

Runtime sets `ATREX_CORE_PHASE` and the corresponding strict environment/manifest protocol. The
entrypoint supports exactly three fresh-process phases:

| Phase | Purpose | Writable result |
| --- | --- | --- |
| `problem_generalization` | Convert evaluator-private operator inputs into a bounded public Agent Problem. | `work/output/agent_problem.json` |
| `framework_baseline` | Turn one DSL seed into a correct authoritative baseline through Runtime tools. | Kernel tree plus baseline report under `scratch/` |
| `optimization_attempt` | Test one attributable optimization direction from immutable Evidence and an incumbent Kernel. | Candidate Kernel plus terminal Attempt report under `scratch/` |

Every phase runs in a new Agent session and produces a Runtime-validated token report. Core never
resumes process memory between Attempts. Historical experience is supplied explicitly through
immutable Epoch and same-branch Attempt Evidence.

## Runtime workspace

Core validates the Runtime-owned manifest before launching an Agent. A normal Attempt exposes:

```text
<attempt>/
├── attempt.json
├── input/
│   ├── kernel/                 # immutable incumbent
│   ├── evidence/               # immutable cross-Epoch history
│   ├── attempt-evidence/       # immutable same-branch history
│   └── agent-problem/          # public operator contract
├── agent/optimizer/            # immutable materialized Core Revision
├── work/kernel/                # writable candidate
├── sessions/                   # normalized Agent-session output
└── scratch/                    # requests, experiment journal, reports, token usage
```

The private Evaluation Contract is referenced only by digest and remains inside Runtime/Gateway.
Gateway and Wiki access use short-lived, Attempt-scoped capabilities issued by Runtime. The Worker
can read those delegated capabilities, while upstream Agate/Wiki credentials remain outside the
sandbox. Core writes operation requests under `scratch/` and uses
[`src/runtime_tools.py`](src/runtime_tools.py) as the canonical bounded protocol client; Runtime
still enforces identity, operation allowlists, quotas, idempotency, and result authority.

## Engineering loop

[`prompts/episode.md`](prompts/episode.md) contains the complete optimization Attempt loop:
reconstruct the incumbent, profile progressively, query focused external knowledge, plan one
falsifiable direction, edit and repair, execute an authoritative Evaluate, record every decisive
experiment immediately, and publish one terminal report. A terminal outcome is `candidate_ready`,
`pivot`, or `blocked`; Runtime independently decides whether a correct candidate is retained and
whether its Kernel Agent Revision is promoted.

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
