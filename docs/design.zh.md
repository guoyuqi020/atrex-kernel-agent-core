# Atrex Kernel Agent Core 设计

[English](design.md) | 中文

## 1. 定位

Core 是一个相对 Runtime 不可信、可以自进化的 Optimizer Bundle。Runtime 把完整仓库封存在一个
Content Digest 下，并在全新 Sandbox 进程中启动唯一入口。Core 决定 Agent 如何推理和修改
Kernel，但不持有 Campaign 生命周期。

| Core 负责 | Runtime 负责 |
| --- | --- |
| Backend、Prompt、Workflow、Tool 展示和 Report 编写 | Campaign/Lineage/Epoch/Attempt 状态与 Fencing |
| `work/kernel` 下的 Candidate 修改 | Workspace、隔离、资源限制和清理 |
| 通过规范客户端发起聚焦 Gateway/Wiki Request | Capability 签发、外部 Credential、配额、幂等和外部 Client |
| Provider Usage 观测、未脱敏 Session 捕获和规范化用量索引 | Token Budget 校验和不可变 Artifact |
| 优化假设和解释 | 正确性/性能权威、保留、晋升和回滚 |

Evolver 位于 Parent/Candidate 仓库之外，可以在私有 Candidate Copy 中修改任何 Tracked Core
文件。Runtime 校验完整仓库、记录 Changed Path、独立评估 Challenger，并控制晋升。

## 2. 可执行仓库

`atrex-bundle.json` 是导入边界。Runtime 只接受严格的版本 1 Manifest、固定 Bundle Format 和
安全 Regular-file Entry；在封存前拒绝 Link、Special File、Git Metadata、未批准 Submodule、
不安全路径和超限内容。

`atrex-agent.json` 是可进化行为配置，版本 2 包含 Backend、Reasoning Effort、Backend-specific
Session Settings、三个阶段的精确 Prompt Path，以及协议型可复用段落的精确 Prompt Fragment
Path。优化方法论位于 `prompts/episode.md`；Agent 可见的准确 CLI、JSON 请求示例、错误修复与
终态校验位于独立可进化的 `prompts/attempt-tools.md`。Session 代码只严格渲染 `DSL` 和
`RUNTIME_TOOL` 两个占位符，并拒绝缺失、未知或未解析占位符。这些 Backend 字段是独立运行默认值；托管
Runtime 会为所有阶段提供不可拆分、权威的 Backend/Effort/Settings Binding。Core 校验并记录
该 Binding，但不能覆盖它，因此 Active 与 Challenger 在同一可比较 Provider Policy 下运行。
Core 不选择第二入口，也不启动嵌套控制面。

## 3. 阶段 Dispatcher

`src/main.py` 要求显式 `ATREX_CORE_PHASE`：

```text
problem_generalization -> sessions/problem_generalization.py
framework_baseline     -> sessions/lineage_bootstrap.py
optimization_attempt   -> sessions/attempt.py
```

每个阶段在启动 Backend 前严格校验 Runtime Manifest、固定路径、Report/Token 目标和仓库身份；
未知字段、版本、路径、缺失 Capability 或矛盾环境都会失败关闭。

### 3.1 Problem Generalization

只有此阶段能看到评测私有 Reference、Input、Shape 和可选 Aggregate Metadata。它没有 Gateway/
Wiki 网络权限。Agent 生成一个有界公开 JSON，Session Wrapper 注入 Controller-owned Schema，
Runtime 再独立执行 Schema 与隐私校验并封存 Artifact。

### 3.2 Framework Baseline

Runtime 提供一个 DSL Seed、公开 Agent Problem 及 Pre-Lineage Gateway/Wiki Capability。Core 只修改
`work/kernel`，并发布与准确、正确探索 Evaluate 绑定的 Baseline Report。Runtime 封存被提名的
最终 Tree，执行一次新的 Runtime-final 评测，只根据该权威结果创建 Baseline Kernel Revision 和
Ready Lineage。

### 3.3 Optimization Attempt

Runtime 提供 Incumbent、Agent Problem、单一晋升 Lineage Evidence View 和不可变 Core Revision。
该 View 对所有 Epoch 统一使用 `trajectories/<ordinal>/attempts/<ordinal>`：同一 Trajectory 的
Attempt 按保留 Kernel 串行承接，不同 Trajectory 从同一 Epoch 起点独立并行搜索。它按 Epoch
组合已晋升历史与当前所选 Trajectory 的更早 Attempt，不暴露 Active/Challenger
Role。Agent 测试一个可归因工程方向，
Optimizer 的 Epoch 目录不暴露聚合 Summary、Lessons 或 Measurements；可信控制状态与精确结果
保留在 Runtime 中，并通过专用工具按需解析。
所有 GPU/Wiki 操作使用 Runtime Protocol Client，
实验即时写入 Journal，最后只发布一个终态 Report。Candidate Publication 只是 Evidence，不是
晋升决定。

## 4. Workspace 与权限

Attempt Manifest v9 固定以下布局：

```text
.runtime/attempt.json
.runtime/agent-problem.json
.runtime/evidence-manifest.json
.runtime/evidence-instructions.md
input/kernel/
input/evidence/
input/evidence/bootstrap/report.json
input/evidence/bootstrap/conversation.jsonl
input/evidence/epochs/
agent/optimizer/
work/kernel/
prompts/README.md
memory/README.md
knowledge/README.md
skills/README.md
tools/README.md
hooks/README.md
sessions/
scratch/
```

Bootstrap 被视为 Epoch 之前的一次特殊 Attempt；Agent 可见 Evidence 只保留终态报告和最新封存的
后端无关会话记录，与普通 Attempt 的精简约定一致。它使用和普通 Attempt 相同的 Journal/查询工具
与终态 Attempt Report Schema，但采用 Bootstrap 专用方法论且没有更早的 Lineage 历史。其 Journal、
Kernel Trial 和 Gateway Result 会成为后续 Optimizer Attempt 继承的根历史。

Agent Problem 是 Core 内部输入，由 Core 投影进最终 Agent Prompt；Optimizer 不会获知其工作区
路径。Agent 可以写入 `work/kernel`、`prompts/`、`memory/`、`knowledge/`、`skills/`、`tools/`、`hooks/` 与 `scratch/`；`sessions/` 由 Core 和
Provider 管理，其余声明输入均只读。Runtime 通过 Bubblewrap 与 cgroup v2 约束挂载、进程和资源。
Evaluation Contract 只暴露 Digest。

`prompts/`、`memory/`、`knowledge/`、`skills/`、`tools/` 与 `hooks/` 是可写 State，并在同一 Epoch 的串行 Attempt 之间复用。Runtime 按
Lineage、Agent Revision 和 Trajectory 隔离，避免并发写冲突。进入下一 Epoch 时，每条 Active
Trajectory 都从上一 Epoch 获胜分支最佳 Kernel Trajectory 的终态 State 获得独立副本；Challenger
从 Evolver 封存的 Revision State 开始。Bootstrap 会发布 Revision 级初始 Seed，再复制给每条新
Trajectory。每个可复用工具
都必须在 `tools/README.md` 中说明用法。各目录都必须有随内容变化同步更新的 README 索引，分别存放
搜索记忆、知识、技能流程、工具脚本和 Claude/Codex Hooks。没有继承 State 时，从固定 Core Revision 复制六目录初始内容；
重置 State 的消融臂每个 Attempt 和重试都恢复到该种子。

`runtime_tools.py` 是规范 Core 协议客户端，而不是 Credential 隔离边界。Runtime 签发的短期
Attempt Capability 对不可信 Worker 可见，因此即使 Agent 直接构造 Proxy Request，Runtime 也必须
重新校验身份、操作、配额、幂等、Candidate 和 Outcome。上游 Agate/Wiki Credential 始终留在
Runtime。规范客户端还负责 Candidate 打包、Request/Response 大小限制、连续 Journal 以及原子
终态 Report。

最终 Optimizer Prompt 采用互不重叠的分层：`episode.md` 只负责优化方法；公开算子契约提供任务
语义；Controller 注入片段负责实际 Workspace、Evidence 范围与测量可信边界；Trusted Context
提供当前 DSL 和位置；动态 Session-tools 段负责准确 CLI、JSON Schema 与校验规则。
`episode.md` 不再复制环境事实或传输协议。

## 5. Session 与 Token

每个阶段创建全新 Provider Session。`src/backends/` 把 Claude、Codex、Pi、Qoder 统一成 Session
Event 与 Token Usage；启动层使用显式环境、隔离 Backend Home、进程组、Timeout/Reaping 和有界
stdout/stderr Capture。

启用 Session 捕获时，Core 在启动前创建 Runtime 选定的 Trace 目录，保存精确 Prompt，把
`session.json` 标记为 `running`，持续写入 Provider stdout/stderr，并在选择 Codex 时周期性镜像
原始 Rollout。这份 Workspace 视图可实时查看，但还不是权威封存结果。Agent 进程回收后，Core
删除实时投影，并从有界捕获重建终态目录；保留的文件不做脱敏或文本改写。Core 仅省略高频
Claude `system/thinking_tokens` 估算事件，且在 `session.json.provider_event_filters` 明确记录；
最终权威 Usage 仍保存在 `events.jsonl`。封存后的 `conversation.jsonl` 是阅读视图：Claude 优先使用原生内容，省去已被完整覆盖的 stdout 消息副本，保留不同的 thinking/text/tool 内容块、未被覆盖的 stdout 内容、诊断、压缩边界和终态结果。重复的初始 Prompt，以及原生队列、标题、文件历史等内部管理事件只从阅读视图中省去。封存前的实时视图仍跟随 stdout。原始 Provider 文件及规范化 usage 索引不变。CLI 未导出的 Provider 内置 System Prompt 会被
明确标记为不可获取，不会伪造内容。`events.jsonl` 是供 Runtime 投影使用的独立规范化用量索引，
不能替代原始文件；终态
`session.json` 记录捕获完整性和可信进程诊断。输出溢出、Codex Rollout 缺失、不安全的原始文件
路径或预建/重定向 Trace 路径都会 Fail Closed。安全上限用于阻止无界捕获，但不完整数据绝不会被
标记为完整。

Input、Output、Cache Read、Cache Write Token 等权计入配额。达到 Budget 会终止完整进程组。
Core 总是写严格 Token Report，Runtime 拒绝缺失、不完整或内部不一致的计量。Core 不包含嵌套
Plan Reviewer，所有规划发生在主 Session 内。

## 6. Evidence 与记忆

Core 没有持久 Campaign 数据库。每次 Attempt 从不可变输入重建历史：统一晋升 Lineage Evidence、
当前所选 Trajectory 中串行完成的更早 Attempt、公开 Agent Problem 和 Runtime 选择的精确 Incumbent。
Epoch 本身串行承接：Bootstrap 初始化 Epoch 1；每个已完成 Epoch 会分别选择下一 Active Agent
Revision 与下一起点 Kernel，因此两者的生产者可以不同；没有更优 Candidate 时沿用原起点 Kernel。
`scratch/directions.json` 与 `scratch/experiments.json` 是绑定本 Attempt 的 append-only 增量，只包含
本 Attempt 新增的记录，不复制历史 Attempt 内容；终态后由 Runtime 封存。冻结 Journal 历史保留在
Runtime Registry 与 Artifact Store 中，按需解析后只由 list/load 视图与当前增量合并；Workspace
中不存在历史 Journal 投影。已完成 Epoch 的所有搜索路径 Journal 均可通过对应工具查询，但未获胜
路径不会变成当前 Agent/Kernel 路线。
不存在第二套本地 Memory Manager。

## 7. 知识与 GPU 执行

Core 不携带 Wiki Corpus 或本地 Gateway。`wiki-query` 通过 Runtime 返回带 Source/Snapshot 身份的
冻结响应；`gateway-execute` 只执行当前 Capability 授权的 Agate 等价操作。一个 Attempt 可进行
多次 Evaluate，Runtime 会保留每一对准确的 Kernel/Result。只有 Runtime 配置的普通比较或 ABBA
结果可以进入 Kernel 保留或 Agent 晋升比较；两者都使用已封存的 Candidate 测量，不会无条件追加
一次单边终评。

Bubblewrap 的 `host` 网络模式不提供目标过滤；需要同时访问 Agent Provider 与 Runtime Service 的
生产部署必须在 Bubblewrap 之外实施 Egress Policy。`isolated` 模式完全无网络。不能把网络隔离
寄托在 Prompt 指令上。

## 8. 进化边界

Core Revision 是完整 Tracked Repository 的 Digest。Evolution 可以修改 Prompt、Backend、Adapter、
Workflow 和 Helper，但不能修改 Runtime、Sandbox Mount、Capability/Quota、Registry/Artifact/
Gateway/Wiki 状态、Parent Revision、Evidence 或 Runtime 的比较与晋升决策。Kernel 是否保留与
Agent Revision 是否晋升相互独立。

## 9. 必须保持的不变量

1. 一次 Runtime Launch 只执行一个受支持阶段和一个全新主 Agent Session。
2. Core 只读声明输入，只写声明输出根。
3. 精确评测 Case 只存在于 Problem Generalization 和 Gateway。
4. 每个 GPU/Wiki 操作都经过 Runtime 签发的 Scope Authority。
5. 每个决定性实验在终态 Report 前记录。
6. Agent Report 不覆盖正确性、Latency、保留或晋升事实。
7. Token 只根据 Provider Evidence 计量，不估算。
8. Git Branch、Worktree、本地 Daemon 和进程内 Memory 都不能作为跨 Attempt Handoff。
