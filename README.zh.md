# Atrex Kernel Agent Core

[English](README.md) | 中文

Atrex Kernel Agent Core 是由 Atrex Kernel Agent Runtime 执行的完整、可进化 Optimizer
Bundle。本仓库不负责调度、Benchmark Harness、Gateway、GPU Wiki、Sandbox 管理或晋升控制。
Runtime 导入精确 Git Commit，把整个 Tracked Tree 封存成一个 Kernel Agent Revision，准备隔离
Workspace，并启动 [`atrex-bundle.json`](atrex-bundle.json) 声明的唯一命令。

Core 负责 Agent 可见的优化行为：

- Agent Backend 的选择与启动；
- 分阶段 Prompt 和 GPU Kernel 工程流程；
- Provider Token 的实时观测与标准化 Session Trace；
- Runtime Gateway 与外部 Wiki 的协议客户端；
- Experiment Journal 与终态 Report；
- 对结构化 Profiling Evidence 的解释。

Runtime 继续负责 Campaign/Lineage 状态、Sandbox、Credential、Token 配额、Evaluation Contract、
Gateway Outcome、Evidence、Kernel 保留、Agent 晋升、回滚和 Evolver 执行。除非某个阶段明确物化，
Core 不能读取 Runtime 状态或评测私有输入。

## 仓库契约

根目录两个 Manifest 刻意分离：

- [`atrex-bundle.json`](atrex-bundle.json) 是 Runtime 导入契约，声明 Bundle 格式及唯一入口
  `src/main.py`；
- [`atrex-agent.json`](atrex-agent.json) 是可进化行为配置，选择 `claude`、`codex`、`pi` 或
  `qodercli`，设置 Reasoning/Session 选项，并映射三个阶段的 Prompt。

Runtime 必须显式设置 `ATREX_CORE_PHASE`。每次进程只执行一个全新 Agent Session：

| 阶段 | 用途 | 可写结果 |
| --- | --- | --- |
| `problem_generalization` | 把评测私有输入归纳成受限的公开 Agent Problem | `work/output/agent_problem.json` |
| `framework_baseline` | 通过 Runtime Tool 把某个 DSL Seed 建成正确的权威 Baseline | Kernel Tree 与 `scratch/` Report |
| `optimization_attempt` | 基于不可变 Evidence 和 Incumbent 测试一个可归因优化方向 | Candidate Kernel 与终态 Attempt Report |

每个阶段都会生成 Runtime 校验的 Token Report。Core 不跨 Attempt 恢复进程内存；历史经验只通过
不可变 Epoch Evidence 和同分支 Attempt Evidence 提供。

## Runtime Workspace

普通 Attempt 的固定结构为：

```text
<attempt>/
├── attempt.json
├── input/
│   ├── kernel/                 # 不可变 incumbent
│   ├── evidence/               # 不可变跨 Epoch 历史
│   ├── attempt-evidence/       # 不可变同分支历史
│   └── agent-problem/          # 公开算子契约
├── agent/optimizer/            # 不可变 Core Revision
├── work/kernel/                # 可写 candidate
├── sessions/                   # 标准化 Agent Session 输出
└── scratch/                    # Request、Journal、Report、Token Usage
```

私有 Evaluation Contract 只以 Digest 出现，具体内容留在 Runtime/Gateway。Gateway 与 Wiki 使用
Runtime 签发的短期 Attempt-scoped Capability。Worker 能读取该委托 Capability，但拿不到上游
Agate/Wiki Credential。Core 把 Request 写在 `scratch/`，并以
[`src/runtime_tools.py`](src/runtime_tools.py) 作为规范、受限的协议客户端；即使 Agent 直接构造
Proxy Request，Runtime 仍独立执行身份、操作白名单、配额、幂等和权威结果校验。

## 工程循环

[`prompts/episode.md`](prompts/episode.md) 包含完整 Attempt 循环：重建 Incumbent、渐进
Profile、查询聚焦外部知识、规划一个可证伪方向、修改与修复、执行权威 Evaluate、立即记录每个
决定性实验，并发布一个终态 Report。终态为 `candidate_ready`、`pivot` 或 `blocked`；Runtime
独立决定 Kernel 是否保留以及产生它的 Kernel Agent Revision 是否晋升。

[`prompts/framework_baseline.md`](prompts/framework_baseline.md) 定义更窄的 Framework Baseline
流程。

## 配置

在 Candidate Revision 中修改 `atrex-agent.json` 即可改变 Core 行为：

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

Backend Credential 和二进制可用性属于部署责任，只能通过 Runtime 的显式环境白名单传入。Core
实时 Token Observer 在 Provider 报告的消耗达到 Runtime Budget 时终止完整进程组；缺失或不一致
的 Usage 会成为无效/不完整计量，而不会被估算。

## 目录结构

```text
.
├── atrex-bundle.json                 # Runtime Bundle/入口契约
├── atrex-agent.json                  # 可进化 Backend 与 Prompt 配置
├── src/
│   ├── main.py                       # 单次阶段 Dispatcher
│   ├── agent_config.py               # 行为配置读取
│   ├── runtime_tools.py              # Gateway/Wiki/Report 规范客户端
│   ├── contexts/                     # 严格 Manifest/Workspace Reader
│   ├── sessions/                     # Prompt、执行、Trace 与 Token Report
│   └── backends/                     # Claude、Codex、Pi、Qoder Adapter
├── prompts/                          # 分阶段指令
├── tests/                            # Core 单元与协议客户端测试
├── pyproject.toml                    # Ruff、mypy、pytest 策略
└── docs/                             # 设计与 Runtime 使用文档
```

Bundle 中刻意没有本地 Gateway、GPU Wiki Corpus、Reference Checkout、Git Worktree Campaign
Engine 或 Runtime State Store。Core Revision 因而可以进化而不会获得控制面权限。

详见[设计](docs/design.zh.md)与[Runtime 使用](docs/quickstart.zh.md)。

## 上游与引用

本 Bundle 来源于开源 Atrex Kernel Agent 项目，并在兼容 Runtime 契约的前提下保留其 GPU
Kernel 工程 Prompt 与 Agent Adapter。适当情况下请引用
[Atrex 论文](https://arxiv.org/abs/2607.14541)。

本项目使用 [Apache License 2.0](LICENSE)。
