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

Before a managed Core launch (including dev-shell), Runtime projects the effective `agent_backend`,
`model`, `reasoning_effort`, and `session_settings` into the workspace's `atrex-agent.json`.
Prompt mappings are preserved, with `prompt_root: "workspace"`: `prompts/...` resolves against the
session workspace. Standalone runs default to `prompt_root: "repository"`. This read-only deployment projection does not modify the Git
repository or sealed Source Artifact; environment binding remains authoritative at execution.

Runtime sets `ATREX_CORE_PHASE` and the corresponding strict environment/manifest protocol. The
entrypoint supports exactly three fresh-process phases:

| Phase | Purpose | Writable result |
| --- | --- | --- |
| `problem_generalization` | Convert evaluator-private operator inputs into a bounded public Agent Problem. | `work/output/agent_problem.json` |
| `framework_baseline` | Run the special pre-Epoch Attempt that turns one DSL seed into a correct authoritative baseline. | Candidate Kernel plus Direction/Experiment journals and terminal Attempt report under `scratch/` |
| `optimization_attempt` | Test one attributable optimization direction from immutable Evidence and an incumbent Kernel. | Candidate Kernel plus Direction/Experiment journals and terminal Attempt report under `scratch/` |

Every phase runs in a new Agent session and produces a Runtime-validated token report. Core never
resumes process memory between Attempts. Historical experience is supplied through one immutable,
single-lineage Evidence view that combines the promoted cross-Epoch history with only earlier
Attempts from the currently selected revision. Active/Challenger roles are not exposed.

## Runtime workspace

The repository's `prompts/`, `memory/`, `knowledge/`, `skills/`, `tools/`, and `hooks/` contain initial Runtime State,
including an Agent-maintained README index in each. Runtime copies these from the pinned Core
Revision into writable workspace directories when there is no inherited State. Bootstrap deposits
and later checkpoints take precedence; they are not overwritten by these defaults. Reset-state
ablation arms restart from the Core seeds. The engineering documentation in `docs/` is not State.

Core validates the Runtime-owned manifest before launching an Agent. A normal Attempt exposes:

```text
<attempt>/
├── .runtime/                   # immutable Runtime-to-Core control inputs
│   ├── attempt.json
│   └── agent-problem.json
├── input/
│   ├── kernel/                 # immutable incumbent
│   └── evidence/               # immutable unified, Epoch-organized Evidence view
│   │   ├── bootstrap/
│   │   └── epochs/             # trajectories/<n>/attempts/<n>/{report,conversation}
├── agent/optimizer/            # read-only implementation/config; initial State copies omitted
├── work/kernel/                # writable candidate
├── prompts/                    # writable, inherited phase prompts
├── memory/                     # writable, inherited search memories
├── knowledge/                  # writable, inherited knowledge
├── skills/                     # writable, inherited procedures
├── tools/                      # writable, inherited scripts
├── hooks/                      # writable, inherited hook definitions
├── sessions/                   # unredacted Agent-session artifacts
└── scratch/                    # writable state owned by this Attempt
    ├── directions.json         # only Direction events added by this Attempt
    ├── experiments.json        # only Experiments added by this Attempt
    ├── directions-index.json   # generated visible history + current summary
    ├── experiments-index.json  # generated visible history + current summary
    └── ...                     # Agent-facing requests, reports, and recovered files
```

The Source workspace copy omits the six initial State directories; only the root-level State is
used by Optimizer. Edits to `prompts/` affect later fresh Sessions, not the already sent Prompt.
The sealed Source Artifact remains complete.

`.runtime/` is an internal Runtime-to-Core control surface. Core locates it through the launch
environment; the Agent Prompt does not advertise it or require the Optimizer to read it. Core
projects the validated Agent Problem directly into the final Prompt as a concise public operator
contract. The stored Artifact remains complete, while the Prompt projection omits generator and
range-evidence provenance and null construction fields. `shape_domain` is the sole parameter-domain
source: fixed parameters are direct JSON values, while variable parameters use range or multi-value
Domain objects. Operation/category labels are supplied by the objective. The remaining
`operator_contract` contains only non-Shape ABI semantics such as
constructor arguments, mutation, layout, and return behavior. Simple implied invariants are omitted;
cross-field and semantic invariants remain visible. Migration-only `atrex.agent_problem.v1` inputs
use their flat `operator_contract` as legacy fixed-parameter data, so Core folds all of those fields
into the same `shape_domain` before rendering the Prompt.
The two Journal files are Attempt-local append-only deltas. Runtime resolves prior-Attempt Journals
on demand from its Registry and Artifact Store; only the generated indexes and `load-*` tools
combine that history with
the current Attempt.
Runtime loads the role-specific Prompt Fragment from its packaged `templates/evidence/` resources.
The Evidence scope manifest and rendered Prompt Fragment are internal `.runtime/` control files;
they are not part of the Agent-facing Evidence tree. `token-usage.json` is likewise not an Agent
interface or live counter. The Core session runner tracks Provider usage in memory and atomically
writes that Core-to-Runtime terminal report only after the Agent process exits. Runtime then
validates its unit, budget, internal totals, completeness, and exhaustion flag.

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
├── provider/codex-rollout.raw-jsonl   # Codex only
├── provider/claude-session.raw-jsonl  # Claude native main session
├── provider/claude-subagents/         # Claude native child sessions, if any
├── events.jsonl                      # normalized usage index
└── session.json                      # capture status and diagnostics
```

`conversation.jsonl` starts with the exact Runtime-supplied user Prompt, projects Provider conversation
content and diagnostics, and ends with Runtime's capture status. It explicitly records that a Provider-managed system Prompt is unavailable when
the CLI does not export it. Prompt and retained Provider files are captured without redaction or
text rewriting. The high-frequency Claude `system/thinking_tokens` estimate event is omitted from
both stdout and the conversation; `session.json.provider_event_filters` records that selection and
the final authoritative Provider usage remains in `events.jsonl`;
reasoning, tool arguments/results, command output, and any sensitive values emitted by the Provider
remain present. Core does not proactively copy credentials that the Provider never emitted. A
bounded-output overflow or incomplete native Claude/Codex transcript capture fails the phase instead of silently
claiming a complete Trace. Before launch, Core creates this fixed directory with `session.json`
marked `running`, then streams stdout/stderr and mirrors native Claude/Codex transcripts while the process is
alive. This live view is explicitly unsealed. After reaping the process, Core discards it and
rebuilds the complete final directory from the captured files before Runtime seals the Artifact.
The Coding Agent may not pre-create or redirect the Runtime-selected Session path.

Claude uses a fresh session ID with native persistence enabled; it never resumes prior context. Its native main/child JSONLs are retained under `provider/claude-session.raw-jsonl` and `provider/claude-subagents/`, including on timeout or failure. `events.jsonl` contains one latest usage record per response, with `message_id` and `source_path` for joining back to tool calls. Print-stream counters are provisional; repeated updates replace earlier counters. `session.json.response_usage_complete` is true only when native response counters reconcile with the terminal bill. Gaps remain partial with diagnostics; the terminal bill is not replaced with estimates. Do not sum native and stdout copies, or add the terminal bill to response usage.

The sealed `conversation.jsonl` is a reading view: Claude native content takes precedence over duplicate stdout messages. Distinct thinking/text/tool blocks remain intact; uncovered stdout content, diagnostics, compaction boundaries, and terminal results remain visible. Duplicate initial prompts and native queue/title/file-history bookkeeping are omitted from this view only. The live view still follows stdout until sealing. Raw Provider files and the normalized usage index are unchanged.

## Engineering loop

[`prompts/episode.md`](prompts/episode.md) contains the optimization methodology, while
[`prompts/attempt-tools.md`](prompts/attempt-tools.md) contains the exact CLI, request examples,
repair guidance, and terminal validation contract for an optimization Attempt. Together they cover:
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
  },
  "prompt_fragments": {
    "attempt_tools": "prompts/attempt-tools.md"
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
├── prompts/                          # phase methodology and protocol templates
├── memory/                           # initial search memories and README index
├── knowledge/                        # initial knowledge and README index
├── skills/                           # initial reusable procedures and README index
├── tools/                            # initial tool scripts and README index
├── hooks/                            # initial Claude/Codex hooks and README index
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
