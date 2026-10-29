## Execution boundary

- Target remote hardware: **{{HARDWARE}}**{{ENDPOINT}}. Official GPU evaluation goes through
  `python3 tools/sandbox.py`. Do not bypass the scoped Runtime or obtain Agate credentials.
  The local Agent workspace intentionally has no GPU, GPU driver/toolchain, or GPU Python runtime.
  Do not run candidate code or probe for local GPU support with `import torch`, `import cuda`,
  `nvidia-smi`, `nvcc`, `ptxas`, `ncu`, `rocprofv3`, `compute-sanitizer`, or equivalent commands.
  Limit local execution to source/text processing that does not import or execute the Kernel. Submit
  compilation, execution, profiling, and GPU diagnostics through the permitted Runtime operation.
- Typed operations such as `--kind run` and `--kind profile` select their inputs automatically and
  take no command after `--`. For custom Dev commands, use `--kind dev --input <path> ... -- <command>`;
  declare every extra local file/directory with repeatable `--input`. Uploads are allowlist-only. Each
  Dev call runs in a fresh, stateless remote job: files, processes, installed packages, compiled output,
  environment changes, and the working directory from an earlier Dev call are not retained. Supply all
  required inputs again on every call and explicitly synchronize any output you need to keep.
  A trivial Dev probe does not establish evaluator health, and diagnostics/custom-input checks do
  not replace official correctness/performance evidence. The current phase may further restrict GPU use.
- Git is Supervisor-only: do not run Git commands or access `.git`. Keep evaluator/public-contract
  inputs (`definition.json`, `reference.py`, `workload.jsonl`, `input.py`, `shapes.json`,
  `agent_problem.json`, `metadata.json`, `roofline.json`), `CLAUDE.md`, `README.md`, and canonical
  `memory/v*.json` unchanged. Never search outside the workspace for private cases or source operators,
  recreate private evaluator drivers, or delete/move existing tracked files and historical evidence
  to shrink uploads. Do not upload `memory/` as worker state. The Supervisor records measurements,
  optionally generates memory summaries, commits source, and manages acceptance.
- Use preinstalled dependencies only. Do not install packages or build third-party libraries locally
  or remotely (pip, uv, conda, setup.py, ninja, cmake, or package managers). GPU packages may JIT on
  import or first use, including `flashinfer`, `flash_attn`/`flash-attn`, `xformers`, and `vllm`.
  Static source inspection is allowed; run imports/probes only through permitted remote GPU operations.
  This execution boundary is separate from production candidate review: remote availability does not
  authorize using a prebuilt operator as the candidate's computation. Production reviews judge actual
  use, not package names; compiler/header/ABI/launch plumbing for self-authored kernels is not banned
  merely because it comes from one of these packages.
  Production enables a process guard that terminates a session on a confirmed local
  dependency-policy violation. Leaderboard disables this dependency-policy guard by default;
  shared Gateway and tracked-workspace protections remain active in both modes.
  If required tooling is unavailable, choose supported tooling or report a blocker.
- Shared Gateway infrastructure is Supervisor-owned. Do not start, stop, signal, reconfigure, or
  replace its services, screen/SSH sessions, state, database, logs, or jobs. Report infrastructure
  failure; do not repair the service yourself.
