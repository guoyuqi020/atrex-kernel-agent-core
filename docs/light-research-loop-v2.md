# Light Research Loop v2

状态：核心闭环已实现（2026-10-31）

当前实现覆盖 Research Board、有序 Direction、Runtime 派生 Experiment 事实、Agent 私有
Git、过滤后的 Epoch Git 共享、持久 island lineage、前 5 个公开 Shape 与完整 workload
评测。每条 trajectory 现在持久化自己的 Claude Provider Session，并在下一个 Episode 的
首次调用中执行真实 resume；trajectory 级稳定 Session Home 保证 per-Episode 投影工作区
变化时仍能找到原会话。Research Board、Agent Git 和 Kernel Artifact 仍是可审计、可恢复
的权威工程状态，conversation 只用于减少重复探索。独立的 dispatcher 边界扰动审计保留
为后续增强，不影响本次闭环的权威测量与恢复。

## 概要

Light 应保留可信评测边界，同时吸收 Ruibo 工作流中适合长期算子工程的部分：持久且有序的研究队列、低成本精确回滚、对大型重构的连续推进，以及对“当前最佳实现”和“下一项实验”的紧凑呈现。

本方案不复制 Ruibo 可修改的评测 Harness，也不向 Agent 暴露正式 workload 的精确内容。测量记录、源码快照、Production Review 和最终 ABBA 仍由 Runtime 与 Supervisor 掌握。主要变化是：以更适合工程工作的方式向 Agent 呈现已有事实，并让搜索协调器保留不同研究路线。

目标闭环是：

```text
有序 Direction
  → 最小判别实验
  → Runtime 自动记录测量事实
  → 不可变 Kernel 快照
  → 更新 Direction 队列
  → 继续、转向或精确回滚
```

## 背景与问题

Light 已保存绝大多数必要事实，但这些事实分散在 Direction、Experiment、Gateway Record、Kernel Artifact、Pareto frontier 和 Episode Report 中。新的 Episode 为恢复完整工程状态，通常需要多次执行 list/load/read，才能回答：

1. 当前经过认证的最佳 Kernel 是哪个？
2. 每条 trajectory 正在实现什么？
3. 下一步最应该做哪个 Direction，为什么？
4. 接受或否定这个 Direction 所需的最小实验是什么？
5. 实验失败后应该恢复哪一份精确源码？

Ruibo 使用 Git、原始评测 receipt 和人工维护的 `PROGRESS.md` 回答这些问题。这种方式工程效率很高，但不能原样迁移到 Light：

- Agent 可以修改 Harness 和测量脚本；
- 完整 fixed/domain workload 会逐渐变成训练反馈；
- 人工维护的进度文档可能与真实源码和测量记录发生漂移；
- 超长 Session 会保留已经失效的因果解释；
- 单条顺序 lineage 缺少独立重复和搜索多样性。

Light 应把同样有用的工程视图做成可信状态的只读投影，而不是再增加一套可编辑的真相源。

## 设计目标

- 保留 Runtime 对 evaluator 输入、测量、源码身份、正确性、Production Policy 和晋升的控制权。
- 每个 Episode 开始时给 Agent 一份紧凑的研究全景。
- 让“下一项实验”成为显式、有序、持久的决策。
- 允许 major redesign 跨越多个 Epoch，而不必立即成为 incumbent。
- 给 Agent 一个私有本地 Git 仓库，用标准 Git 完成日常工程操作，同时隐藏 Controller Git 和晋升权限。
- 保留并行 trajectory 的差异，同时在 Epoch 边界共享已完成事实。
- 暴露足够的 workload 结构，使 Agent 能设计 Kernel family，但不暴露精确隐藏 workload。
- 减少重复复述，避免未经验证的分析变成测量事实。
- 保持 Direction 和 Experiment 模块可独立开关。

## 非目标

- 向 Agent 暴露 Controller 的真实 Git 仓库。
- 把 Agent 私有仓库中的 Commit ID 或 ref 当作测量和晋升证据。
- 允许 Agent 修改正式 evaluator 或晋升策略。
- 暴露隐藏 Shape 的精确参数、参数与权重映射，或可逆地编码参数的 Shape ID。隐藏 Shape 可以使用与参数无关的 opaque ID。
- 在 trajectory 之间共享实时草稿、scratch 或 conversation。
- 用 Agent 编写的总结替代 Measurement Record。
- 用一个自动计算的数字分数替 Agent 决定优化方向。

## 真相源划分

系统保留四层权威状态，每类事实只有一个所有者。

| 层次 | 所有者 | 内容 |
| --- | --- | --- |
| Measurement Record | Runtime | 请求身份、精确源码摘要、公开结果、故障分类、标准化测量结果 |
| Kernel Artifact | Runtime | 不可变的已提交源码树和对应 Kernel ID |
| Direction Journal | Agent 通过 Runtime 的受校验操作写入 | 有序研究意图、生命周期、下一步和关闭决定 |
| Promotion Record | Supervisor | 被选择的 Kernel、完整 Evaluate 证据、Production 决策和最终 ABBA |

Experiment 是对一个或多个 Measurement Record 的语义标注，不重复保存测量内容。Research Board 是上述权威状态的派生视图，不是权威状态，也不能被 Agent 直接修改。

## Research Board

每个 Episode 开始时，Runtime 生成一份有大小限制的 Research Board，并自动放入 Prompt。Agent 也可以通过只读命令刷新它。该视图必须足够短，保证每次 Episode 都能完整读取。

示例：

```yaml
incumbent:
  kernel_id: kernel_0123
  latency_us_geomean: 95.80

lineages:
  T1:
    working_kernel_id: kernel_0456
    lineage_best_kernel_id: kernel_0345
    active_direction_id: direction_aaaa
  T2:
    working_kernel_id: kernel_0789
    lineage_best_kernel_id: kernel_0678
    active_direction_id: direction_bbbb

open_queue:
  - rank: 1
    direction_id: direction_bbbb
    name: Split KV across CTAs
    change_scope: major_redesign
    status: in_progress(T2)
    next_step: 实现一个能够编译且正确的 KSPLIT=2 原型
    evidence_summary: Profile 显示低 CTA waves 下存在无法隐藏的访存延迟
  - rank: 2
    direction_id: direction_aaaa
    name: Reduce TMEM readout work
    change_scope: local
    status: proposed
    next_step: 使用相同对照测试 64-column readout

recent_experiments:
  - experiment_id: experiment_cccc
    direction_id: direction_aaaa
    conclusion: refuted
    latency_delta_percent: 3.2
    kernel_id: kernel_0456

closed_directions:
  - direction_id: direction_dddd
    status: abandoned
    reopen_condition: 寄存器使用降到 160 以下时重新考虑
```

Research Board 不得包含隐藏输入、私有 evaluator 输出、conversation 原文或无上限的测量正文。完整 Direction、Experiment、Measurement 和 Kernel 数据仍通过对应的 load/read 操作按需读取。

### 队列排序

队列顺序是显式状态，不由 Runtime 根据公式自动计算。新 Direction 默认追加到队尾。Agent 查看完整列表后，可以原子地调整顺序：

```text
direction-prioritize D7 --before D2
```

Prompt 要求 Agent 综合考虑预期总收益、workload 覆盖范围、证据可信度、信息增益、实现成本、验证成本和正确性风险。这些因素用于帮助判断，不要求模型为了满足公式而虚构数值。

Agent 可以不选择队首 Direction，但必须记录简短原因，使持续回避高价值工作成为可见、可审计的行为。

## Direction 契约

Direction 是研究意图的基本单位，不重复记录测量结果。

```yaml
direction_id: direction_...
name: Split KV across CTAs
status: proposed | in_progress | deferred | completed | abandoned | blocked
change_scope: local | major_redesign
hypothesis: 多 CTA 可以在低 wave Shape 上隐藏 KV 访存延迟。
plan:
  - 证明 KSPLIT=2 源码结构可以编译。
  - 在公开 Shape 上快速验证 reduction，再用完整 workload Evaluate 验证正确性。
  - 在相同 allocation policy 下比较 KSPLIT=1/2/4。
stop_conditions:
  - 如果 reduction 开销大于 Profile 推导出的延迟收益，则终止。
next_step: 证明 KSPLIT=2 源码结构可以编译。
reopen_condition: null
relationship: independent | refinement | alternative | reimplementation | contradiction
derived_from_direction_ids: []
derived_from_experiment_ids: []
```

`next_step` 是 `plan` 中第一个尚未完成的步骤，Runtime 可以自动推导，不必重复存储。对于假设仍未解决的 `deferred`、`abandoned` 或 `blocked` Direction，必须记录 `reopen_condition`。

Direction 关闭时记录 hypothesis status 和引用证据；完整的 Experiment 与 Measurement 关联由 Runtime 自动推导。

### 所有权

每条 trajectory 同时只能拥有一个 active Direction；不同 trajectory 可以同时拥有不同 Direction。Agent 看到的状态写成 `in_progress(T2)`，不要求 Agent 单独填写 owner 字段。

所有权是一种持久 lease：

- trajectory 继续研究时自动续期；
- `major_redesign` 可以跨 Episode 保留；
- trajectory 被永久停止或恢复状态被丢弃时失效；
- 其他 trajectory 如需独立实现同一想法，应创建 `reimplementation` Direction，而不是静默重复工作。

Direction 身份和队列顺序在 Epoch 边界共享。实时所有权变化由 Runtime 原子协调。

## Experiment 契约

Agent 只提交 Runtime 无法自动推导的研究意图和证据引用：

```yaml
name: KSPLIT=2 first complete implementation
direction_id: direction_...
intent: 判断 split KV 是否能隐藏足够延迟以抵消 reduction 开销。
gateway_record_ids:
  - gateway-...
interpretation: 结果不确定，因为只有 short-KV regime 得到改善。
```

Runtime 自动补充：

```yaml
before_kernel_id: kernel_...
after_kernel_id: kernel_...
source_diff_summary: 增加 split partial output 和二阶段 reduction。
operations: [check, run, profile]
correctness: pass
latency_summary: {...}
per_regime_summary: {...}
failure_class: null
measurement_digests: [...]
```

自动生成的字段是事实；`intent` 和 `interpretation` 是 Agent 的可修正判断。两者在存储和展示中必须明确区分。

Direction 和 Experiment 同时启用时，如果 trajectory 只有一个 active Direction，Experiment 默认自动关联该 Direction；如果请求明确指定其他可见 Direction，则使用显式值。只启用 Experiment 时不要求 `direction_id`，由 `intent` 表达本次局部假设。关闭 Experiment 时，Measurement Record 仍完整保留执行事实。

Episode Report 只选择最终 Kernel 或 Experiment 并总结 Episode 结果，不重复 Direction plan 和测量正文。

## Agent 私有 Git 工作区

Light 不需要重新实现 `diff`、`restore`、branch、tag 和历史查询，而应给每条 trajectory 一个真实的私有 Git 仓库。该仓库位于 Agent draft 中，不包含 Controller 的 Git 元数据。

Agent 直接使用标准本地命令：

```text
git status
git diff
git log --all --oneline --graph
git switch -c split-kv
git add kernel.py implementation/
git commit -m 'Try KSPLIT=2 reduction'
git tag experiment/ksplit2
git revert <commit>
git restore --source <commit> -- kernel.py implementation/
```

这个仓库只是工程工具，不是可信测量记录。每次测量前，Runtime 仍按实际源码字节生成不可变快照，并分配 Kernel ID 和 Gateway Record ID。晋升使用被选中 Measurement Record 所绑定的 Runtime 快照，绝不使用 Agent 提供的 Commit ID，也不直接读取当前 `HEAD` 作为证据。

### 仓库内容

Git 仓库根目录可以仍是 Agent workspace，但 evaluator 和 promotion 只接受：

```text
kernel.py
implementation/**
```

`scratch/`、链接的工具、Skills、公开说明、Session 文件和 Runtime 投影全部忽略，不进入晋升。无论 Agent 是否把它们加入 Git，Source Policy 仍会拒绝 symlink、路径逃逸、受保护文件修改和非法源码布局。

私有 `.git/` 随 trajectory 跨 Episode 保留，不挂载给其他 trajectory，也不进入 Kernel 源码压缩包。

### 初始 refs

Runtime 用可信 Kernel Artifact 初始化本地 refs：

```text
main                         trajectory 创建时的 official incumbent
work                         当前 trajectory 工作分支
refs/tags/atrex/incumbent    当前 official incumbent
refs/tags/atrex/global-best  所有可见 trajectory 中最快的正确 Kernel
refs/tags/atrex/lineage-best 当前 trajectory 历史最佳正确 Kernel
refs/atrex/shared/T2/e0004/head  Epoch 边界导入的同伴密封源码历史
```

Runtime 生成的 commit message 包含 Kernel ID、源码摘要、来源 trajectory 和 Epoch。这些 refs 在 trajectory 准备或恢复时由 Runtime Artifact 重建。Agent 可以自由创建、移动或删除自己的 branch/tag；任何私有 Git ref 都不是权威证据。

即使 Agent rewrite history，Runtime Measurement 仍然有效，因为 Runtime 保存完整源码树。Agent 仓库执行 GC 也不能删除 Runtime 的 Kernel Artifact。

### Git Commit、Kernel 与测量的三层身份

Git Commit ID 不能直接替代 Kernel ID。三种身份分别描述不同对象：

| 身份 | 描述对象 | 主要用途 |
| --- | --- | --- |
| Agent Git Commit ID | 某个 trajectory 私有仓库中的版本及历史关系 | branch、diff、revert、cherry-pick 和人工审计 |
| Kernel ID | Runtime 规范化后的实际评测源码内容 | 跨仓库去重、源码绑定、共享和晋升 |
| Gateway Record ID | 某个 Kernel 在特定请求条件下的一次逻辑评测 | 正确性、性能、Profile 和诊断证据 |

Git Commit ID 还包含 parent、author、时间和 message。同一份源码在两个 trajectory 中可以有不同 Commit ID；rebase、amend 和路径过滤也会改变 Commit ID。反过来，同一个 Commit 可以在不同 evaluator、硬件、Shape 子集或 repetition policy 下产生多条 Gateway Record。因此三者不能合并。

Runtime 按如下关系维护映射：

```text
Agent Commit / Dirty Worktree
    ↓ 读取并规范化 kernel.py + implementation/**
Kernel ID
    ↓ 绑定 operation、evaluator、reference、hardware、Shapes 和 options
Gateway Record ID
```

Kernel ID 建议定义为规范化源码 manifest 与文件内容的 SHA-256：

```text
kernel_<sha256(canonical_source_tree)>
```

`canonical_source_tree` 至少包含按相对路径排序的文件名、文件类型、受支持的 mode 和完整文件字节。symlink、路径逃逸和非法文件类型在计算 ID 前拒绝。同样的评测源码无论来自哪个 branch、Commit 或 trajectory，都得到同一个 Kernel ID。

例如同一份源码在共享历史导入前后可以是：

```text
T1 原 Commit:       a111...
导入 T2 后 Commit: b222...
Kernel ID:          kernel_c333...  # 保持不变
```

Agent 日常主要使用 Git ref 和 Commit，不需要手工管理 Kernel ID。Research Board 可以优先显示 `atrex/global-best`、`atrex/lineage-best` 和对应 Commit subject；Git note 与 Runtime 结果同时显示 Kernel ID 和 Gateway Record ID。Kernel ID 主要服务于 Runtime 去重、跨仓库关联和 Promotion 审计。

Git tree ID 也不能取代 Kernel ID：它可能包含评测范围之外的文件，依赖仓库对象格式，而且不能表达 Runtime 的源码规范化规则。

### 测量绑定

执行带源码的 Runtime 操作前，Light 记录：

```yaml
agent_git:
  head: <commit or null>
  clean: true | false
  tree: <Git tree ID or null>
source_digest: <Runtime canonical digest>
kernel_id: kernel_...
```

`source_digest` 和 Kernel ID 是权威身份；Git 身份只用于工程回溯和人工审计。Dirty worktree 仍可测量，但 Runtime 必须先保存不可变 Kernel Artifact，并明确说明结果没有绑定到 clean Agent commit。

如果 clean Commit 的规范化源码此前已出现，Runtime 复用已有 Kernel ID；如果同一个 Kernel 使用不同请求条件执行，则创建或复用对应的不同 Gateway Record。Commit 被删除或改写不会改变任何已有 Kernel/Record 身份。

Prompt 建议 Agent 在正式实验前 commit，使 `git log`、Experiment 和 Measurement 自然对应。更严格的可选模式可以要求 full Evaluate 和 ABBA 前工作区 clean，但继续允许 dirty source 执行 `dev`、`check` 和编译探针。无论是否 clean，晋升始终密封 Runtime 实际测量的快照。

### 受控 Git 环境

Agent 获得正常的本地 Git 能力，但没有远端仓库权限和 Git 凭据：

- 不配置 Controller remote；
- 不挂载 credential helper、签名密钥或用户全局 Git 配置；
- 禁止交互式凭据提示；
- 在受控 Agent 环境中禁用 remote transport、submodule 和 Git LFS；
- Runtime 和 Supervisor 不使用 Agent 可控的 hook、filter、alias、diff driver 或仓库配置执行 Git；
- Controller 导入经过校验的 Kernel Artifact，不导入 Agent 任意提供的 Git object。

实现上可以在 Bubblewrap 内提供标准 Git CLI、隔离 Git 配置，并对具备远端能力的子命令增加很薄的策略限制。Light 无需重新实现本地版本管理语义。

### 跨 trajectory 共享和采用

如果只导入最终源码快照，Agent 可以 `diff`，但无法理解实现过程，也无法只 `cherry-pick` 某个独立步骤。反过来，直接复制同伴的整个 `.git/` 会带入私有配置、hooks、reflog、未完成 branch 和无关文件。Epoch 共享应导出一条**源码范围受控的 Git 历史**。

每个 trajectory 在 Epoch 结束时密封一个 `epoch_head`。Runtime 从共同的 Epoch seed 到该 `epoch_head` 导出可达历史，但只保留：

```text
kernel.py
implementation/**
```

推荐使用标准 Git 的 `fast-export` / `fast-import` 完成路径过滤和对象导入，而不是自行实现版本图。导出内容不包括：

- `.git/config`、hooks、alias、filter 和 credential 配置；
- reflog、stash、remote、submodule 和 Git LFS 对象；
- scratch、工具、Skills、Session、conversation 和 Runtime 私有文件；
- `epoch_head` 不可达的临时 branch；
- Epoch 密封之后产生的实时 WIP commit。

路径过滤会重写 Commit SHA。Runtime 在审计记录中保存 `original_agent_commit → imported_commit` 映射。共享导出把 Agent 编写的 commit message 替换为固定的 Runtime 文案，避免把对话摘要或凭据带入其他 trajectory；原始消息仅保留在所属 trajectory 的私有 Git 中。非源码私有路径不进入共享历史。

接收方将历史导入独立命名空间：

```text
refs/atrex/shared/T2/e0004/head
refs/atrex/shared/T2/e0004/base
```

Agent 可以直接：

```text
git log --graph --oneline refs/atrex/shared/T2/e0004/head
git diff work refs/atrex/shared/T2/e0004/head
git switch -c inspect-t2 refs/atrex/shared/T2/e0004/head
git cherry-pick <imported-peer-commit>
```

如果一个提交依赖前置提交，Agent 应按顺序 cherry-pick，或从同伴 head 建立新 branch；Runtime 不把任意中间 commit 伪装成可独立应用的 patch。

#### 测量标记

共享历史中的 commit 不一定都经过测量。Runtime 为导入 commit 生成独立 Git notes：

```text
refs/notes/atrex-measurements
```

测量过的 commit note 包含：

```yaml
measurement_status: measured
kernel_id: kernel_...
gateway_record_ids: [gateway-...]
experiment_id: experiment_...
correctness: pass
latency_us_geomean: 95.8
```

没有精确 Measurement Record 绑定的 commit 显示：

```yaml
measurement_status: unmeasured
```

Agent 可以使用：

```text
git log --show-notes=atrex-measurements refs/atrex/shared/T2/e0004/head
git notes --ref=atrex-measurements show <commit>
```

Git note 只是 Measurement Record 的紧凑索引；Kernel ID 和 Gateway Record 仍是权威证据。未测量 commit 可以帮助理解实现步骤，但不能支持性能、正确性或因果结论。

#### 共享范围与增量更新

默认共享密封 head 的完整可达源码历史，包括未测量但已 commit 的中间工程步骤；不共享尚未 commit 的 worktree。对于需要跨多个 Epoch 保密推进的 major redesign，可以只发布最近正确 checkpoint，并把 WIP branch 保持私有，直到 Direction owner 主动发布或到达配置的 milestone。

历史按 Epoch 增量导入：接收方已拥有共同 base 时，只传输新 commit 和 Git object。系统应设置源码字节、commit 数量和 commit message 大小上限。超过上限时不能静默截断；应明确降级为：

1. 共同 base；
2. 有 Measurement Record 的中间 checkpoint；
3. 最终 head 的 squash snapshot；
4. 一条说明被省略 commit 数量和原因的审计记录。

同伴的真实 `.git/`、当前 checkout、实时 branch、scratch 和 conversation 始终保持私有。采用同伴 commit 会记录为研究事件，但在正常 Evaluate 和 promotion 通过前不会改变 official incumbent。

### 当前最佳指针

Research Board 和 Runtime 重建的 `atrex/*` refs 展示四种不同状态：

| 指针 | 含义 |
| --- | --- |
| `official_incumbent` | 已通过完整晋升、用于正式验收的 Kernel |
| `global_best` | 所有可见 trajectory 中最快的完整正确 Kernel |
| `lineage_best` | 当前 trajectory 中最快的完整正确 Kernel |
| `working_head` | 当前 Agent branch 与 worktree，可能尚不正确 |

Runtime 只在 trajectory 准备、恢复或 Epoch 边界显式移动生成的 `atrex/*` refs，不能切换 Agent 当前 branch，也不能丢弃本地 commit。

## Episode 生命周期

### Episode 开始

Runtime 注入 Research Board，并要求 Agent：

1. 检查 incumbent、trajectory best、active Direction 和 open queue；
2. 只加载与当前决策最相关的详细证据；
3. 继续自己拥有的 major redesign，或选择一个 open Direction；
4. 选择能够实质改变判断的最小实验；
5. 在高风险修改前 commit 或 tag 一个已知正确状态。

### Episode 进行中

局部改善是 checkpoint，不是强制终止条件。只要当前 Direction 仍有高价值未完成步骤，Agent 就可以继续推进。普通本地工作通过 Git 恢复；每个已评测源码还可以通过 Runtime Kernel Artifact 恢复，因此后续失败不会损害最佳候选。

Runtime 继续负责长时间等待、重试、去重和基础设施故障分类。基础设施故障不能形成负面 Direction 证据。编译失败、正确性失败和 Source Policy 拒绝可以记录为 Experiment，但不能单独证明整个 Direction 错误。

### Episode 结束

以下情况可以提交 `episode-report`：

- 当前 Direction 的计划已完成；
- 获得稳定且值得晋升的候选；
- 已达到 stop condition；
- 被明确的外部依赖阻塞；
- 研究预算耗尽，且下一步已经保存。

找到第一个局部更快候选本身不足以结束 Episode。`major_redesign` 可以跨 Episode 延续，不需要把 WIP 源码晋升为 incumbent。

### Session 连续性

Provider Session 是有用的工作上下文，但不是权威记忆：

- 同一 trajectory 的新 Episode 首次调用恢复上一 Episode 的 Claude Session；
- Supervisor 注入上一 Episode 的接受/拒绝、候选、ABBA 和下一方向等有界增量结算，避免重新推导已有结论；
- per-Episode Agent 工作区可以变化，但 trajectory 级 Session Home 保持稳定；
- output-limit recovery 在同一 Session 中注入缩小步骤的提示；
- 必须更换 Session 时，新 Session 仍可从 Research Board、active Direction、受控 Git 历史和可按需读取的 Measurement 恢复；
- trajectory 之间不共享 conversation。

## Major redesign 生命周期

major redesign 的中间源码可能更慢或不完整，因此需要受保护的 lineage：

```text
能力原语已证明
  → 可以编译
  → 部分正确性通过
  → 公开 Shape 正确性通过
  → 完整 workload 正确性通过
  → 获得初步性能
  → 进入正式候选评测
```

协调器分别保存最近 WIP checkpoint 和最近正确 checkpoint。WIP 只对拥有它的 trajectory 可见；只有完成的 Measurement Record 和不可变 Kernel Artifact 会共享。

major redesign 在达到 stop condition、被明确 refute/abandon、milestone 预算耗尽且无进展，或产生稳定候选时关闭。不能因为同一 Epoch 中另一个 trajectory 找到更快的局部优化，就丢弃未完成 major redesign。

### Major redesign 完成后的状态转换

`T2` 表示当前搜索职责，不是永久绑定某一个 Direction。一个 major redesign 结束时，本 Episode 不应立即混入另一个无关 Direction，而应先完成以下事务：

1. 把 Direction 标记为 `completed`、`abandoned`、`blocked` 或 `deferred`；
2. 绑定最终 Experiment、Measurement Record、Kernel ID 和 Agent Commit；
3. 密封本 Direction 的 Git head 与可共享历史；
4. 保存 lineage best、最终 WIP 和 closure/reopen condition；
5. 释放 `in_progress(T2)` ownership lease；
6. 提交 Episode Report，由 Supervisor 独立执行候选验收和晋升；
7. 在 Epoch 边界发布 Direction、证据和允许共享的源码历史。

Direction 与 Promotion 是两个不同结论：

- `completed + supported` 表示研究计划已经完成且假设得到证据支持；
- 候选是否成为 official incumbent，仍由完整 Evaluate、Production Review 和 ABBA 决定；
- 假设成立但候选没有整体收益时，Direction 可以正常完成，同时不晋升 Kernel；
- 假设只在部分 regime 成立时，可以关闭原 Direction，并创建范围更窄的 `refinement` 子 Direction；
- 因实现成本暂时停止时使用 `deferred`，并写清 `reopen_condition`，不能伪装成 refuted。

下一个 Episode 开始时，协调器按顺序为 T2 选择职责：

1. 如果仍有 T2 拥有且未结束的 major redesign，则继续恢复；
2. 否则选择 Research Board 中优先级最高、状态为 ready 且没有其他 owner 的 major redesign；
3. 如果没有现成 major redesign，则启动一个 discovery Episode：读取 global best 的 Profile、Pareto frontier 和开放证据，提出并排序新的结构性 Direction；
4. 如果没有可信的新结构性方向，T2 可以创建显式 `reimplementation`，独立验证一个高价值 Direction，但不能静默复制 T1/T3 的工作；
5. 如果所有高价值结构性方向都已关闭，协调器可以暂时把 T2 调整为普通 exploration trajectory，而不是让它空闲。

新 Direction 的起点也应明确：

- 与刚完成 Direction 直接相关的 `refinement` 或 `alternative` 默认从该 lineage best 建 branch；
- 无关的新 Direction 默认从当前 global best 建 branch；
- Agent 可以选择其他可见 Kernel 作为起点，但必须记录原因；
- 协调器不能覆盖刚完成 Direction 的 Git branch 和归档历史。

当前实现以 trajectory、而不是 Episode 作为 Claude Provider Session 边界。无关的新 Direction
也会先在同一 trajectory Session 中收到明确的状态转换和增量结算；历史推理仍是可质疑信息，
Research Board、测量记录和 Git 历史始终是跨 Session 的权威工程上下文。依赖策略恢复等必须
替换原生会话的情况会更新该 trajectory 的 Session ID，下一 Episode 从替换后的会话继续。

## 并行搜索拓扑

Pooled search 应采用 island model，不能每轮都用 Epoch winner 替换所有 trajectory。

每条 trajectory 保留：

- 自己的 editable working source；
- lineage best；
- active Direction 和 ownership lease；
- 私有 WIP checkpoint；
- 开启连续性时的 Provider Session。

Epoch 边界只共享最终 Direction 状态和队列调整、Experiment 标注、完成的 Measurement Record、被引用的 Kernel Artifact、密封且经过源码范围过滤的 Git 历史，以及全局 Pareto frontier。不共享实时草稿、scratch、conversation 或未验证分析。共享历史中的未测量 commit 只作为工程过程，不作为证据。

三条 trajectory 的推荐默认职责：

| Trajectory | 默认职责 |
| --- | --- |
| T1 | 围绕 global best 做有边界的局部 exploitation |
| T2 | 持续推进最高优先级 major redesign；完成后重新选题或发现新的结构性方向 |
| T3 | 做能力探针、Profile 或独立探索 |

职责只引导任务分配，不限制 Agent 的具体实现。trajectory 可以在 Epoch 边界主动采用同伴 Kernel，但不能仅因为对方赢得上一轮就被自动迁移。

协调器仍只发布一个 official incumbent。保留 lineage 只影响研究状态，不改变 Production 权限。

## Workload 信息边界

Light 必须防止直接拟合完整 workload，但完全模糊的 Agent Problem 也会阻碍合理的 Kernel-family 设计。当前 Light 的广义 Agent Problem 隐藏全部真实 evaluator Shape，只暴露语义范围和 synthetic development cases；历史上的 valid/test 与当前的 `shape_ids` 分片主要解决评测执行。

这里不再把 Shape 分成不同的开发和终态评测集合。**所有标准 Evaluate 和 ABBA 始终运行同一套完整 workload**，只对 Shape 参数设置不同的可见性：

| 可见性 | 是否参与普通 Evaluate | 是否参与终态 Evaluate/ABBA | Agent 可见信息 |
| --- | --- | --- | --- |
| `public_shapes` | 是 | 是 | 精确 Shape 参数、稳定公开 ID、逐 Shape 正确性、延迟和诊断 |
| `hidden_shapes` | 是 | 是 | 不公开参数；进入完整 workload 的正确性与性能聚合，可使用不携带参数的 opaque ID 返回受限结果 |

V0、普通 Episode Evaluate、终态 Evaluate、终态 ABBA 和最终报告必须使用相同的 Shape 集合、频率权重、正确性策略和性能聚合方式。普通 Evaluate 不能只测公开 Shape；否则 Agent 优化目标与终态门禁仍会发生偏移。

Agent Problem 同时保留语义维度和全局粗粒度分布：

```yaml
dimensions:
  head_dim: [64, 128, 256]
  key_length: {min: 128, max: 16384, buckets: [short, medium, long]}
  query_length: {buckets: [decode, mtp, prefill]}
  causal_ratio: 0.6
  sliding_window_ratio: 0.2
  padding_ratio_buckets: [low, medium, high]
  cta_wave_buckets: [sub_wave, one_to_two_waves, multi_wave]
```

该摘要不能包含精确隐藏 Shape 组合。`public_shapes` 显式给出一部分真实 Shape，使 Agent 能围绕 `D`、group count、CTA waves、span、rows、padding ratio、window mode 等有物理意义的变量实现 Kernel family，而不必猜测实际工作负载是否包含 decode、MTP、prefill 或不同 window regime。

### 可见性划分规则

可见性由 Runtime 在 Campaign 创建时一次性生成并固化：

- 按 evaluator 的规范 Shape 顺序固定公开前 5 个 Shape；恢复 Campaign 时不得重新选择或重排；
- 如果完整 workload 少于 5 个 Shape，则公开全部 Shape；
- 公开 Shape 使用 `public-*` ID；隐藏 Shape 可以使用 opaque ID，但 ID 不能编码或映射回参数；
- Runtime 保存完整私有 visibility manifest，Agent 工作区只物化公开 Shape 参数；
- Agent 可以看到公开 Shape 的精确频率权重，以及完整 workload 的频率桶摘要；隐藏 Shape 的精确参数与参数—权重映射不公开。

例如 90 个 Shape 固定公开规范顺序中的前 5 个，其余 85 个参数保持隐藏。全部 90 个 Shape 仍参与每次标准 Evaluate；公开数量只控制 Agent 能看到多少精确参数，不改变评分样本。

Agent Problem 可以扩展为：

```yaml
schema_version: atrex.agent_problem.v2
shape_visibility: partial
dimensions: ...
public_shapes:
  - case_id: public-001
    shape: {...}
    frequency_weight: 0.08
  - case_id: public-002
    shape: {...}
  - case_id: public-003
    shape: {...}
  - case_id: public-004
    shape: {...}
  - case_id: public-005
    shape: {...}
synthetic_development_cases: [...]
hidden_summary:
  count: 85
  regime_coverage: {...}
```

`hidden_summary` 只说明数量与粗粒度覆盖，不包含精确 Shape。现有 `shape_ids` 请求机制继续保留：Agent 可以按公开或 opaque Shape ID 发起诊断性子集 Evaluate、Profile 或其他探针；子集结果必须明确标记为非终态证据，不能用于晋升或更新 official incumbent。

### 评测与反馈

- 不带 Shape 选择器的普通 full Evaluate 和终态 Evaluate/ABBA 固定运行全部 Shape；
- Agent 可以显式传入 Shape ID 做子集 Evaluate，以缩短开发反馈或定位性能问题，但该结果不能满足 Episode Report、晋升或终态门禁；
- `profile`、`check` 和 `disassemble` 可以按公开或 opaque Shape ID 运行；只有前 5 个公开 Shape 能关联具体参数，隐藏 Shape 仍只返回不含参数的诊断结果；
- 公开 Shape 可以返回逐 Shape 结果；隐藏 Shape 默认只进入全量聚合，并按稳定的粗粒度 regime 返回有大小限制的摘要；
- 如果保留隐藏 Shape 的逐 Shape 延迟，必须使用 opaque ID，且 Kernel 接口和 dispatcher 不得接收该 ID；
- 普通 Evaluate 与终态评测的区别只应是重复次数、审查和晋升权限，不能是 Shape 集合、权重或容差策略；
- dispatcher 边界附近必须增加 synthetic jitter，并用 workload-mixture shift 检查阈值稳定性。

这样 Agent 每次优化看到的总分与终态总分来自同一个 workload，避免开发—终态目标错位；同时又只能根据部分代表 Shape 和全局粗粒度分布设计 Kernel family，无法直接枚举完整 workload 并拟合 `2.5`、`48`、`160` 等偶然边界。

这种设计防止的是**直接参数拟合**，不是严格意义上的统计 holdout。因为 Agent 会反复看到完整 workload 的聚合分数，仍可能进行黑盒适配。若需要衡量真正的 workload 泛化，应另设一次性、不可用于继续优化的外部分布审计集；它不参与当前 Campaign 的日常分数和终态晋升。

## 诊断自由度

Light 应允许 Ruibo 风格的能力和机制探针，但不允许修改正式 evaluator：

- `dev` 可以在有边界的沙箱中执行 Agent 编写的无状态探针；
- `profile`、`check`、`disassemble` 仍是 Runtime 操作，保存不可变请求和结果；
- 自定义脚本可以读取公开 Kernel Artifact 和诊断输出；
- 正式输入生成、正确性、分数聚合和晋升策略不挂载给 Agent。

Probe 可以支持实现可行性和机制判断，但不能单独支持 Production 正确性或性能晋升。

## Prompt 修改

Episode Prompt 应明确：

- 提出新工作前先阅读 Research Board；
- propose 前检查是否已有重复 Direction；
- 优先执行能区分竞争解释的最小实验；
- Runtime 测量是事实，Agent 分析可以被推翻；
- 高风险修改前保存本地 Git checkpoint；
- 不能因为一个局部改善可晋升就立即停止；
- major redesign 要持续到 milestone、stop condition、blocker 或预算边界；
- 不得反复使用隐藏或正式评测调整 dispatcher；
- 使用公开 workload descriptor 和 public Shape 设计 Kernel family；不得推断、枚举或重建隐藏 Shape。

Prompt 不要求 Agent 在 Direction、Experiment 和 Episode Report 中重复完整测量结果。

## 故障与恢复语义

- Runtime 等待运行中的 Gateway job，并根据持久 job ID 恢复状态；
- 基础设施故障按策略重试，不能成为正确性或 hypothesis 证据；
- 重复请求按配置返回缓存公开结果或可操作的 Record ID；
- retryable failure 不消耗 Direction milestone 次数；
- Kernel 错误返回具体的编译、正确性、timeout 或 Source Policy 原因；
- 测量完成后崩溃不能丢失 Kernel Artifact 或 Measurement Record；
- 队列调整和 ownership 转移必须原子化；
- 搜索恢复要分别恢复每条 lineage，不能静默用 global incumbent 覆盖 WIP。

## 可选模块行为

| Directions | Experiments | 行为 |
| --- | --- | --- |
| off | off | 保留基础工作流；Board 只展示 Kernel 和 Measurement 摘要 |
| on | off | 提供有序 Direction 队列；关闭 Direction 时直接引用 Gateway Record |
| off | on | Experiment 记录局部 intent，不包含 `direction_id` |
| on | on | 完整研究循环；Experiment 自动关联当前拥有的 Direction |

长期 pooled search 默认启用 Direction。无论模块配置如何，Runtime Measurement Record 都必须存在。关闭可选模块只改变 Agent 标注和工具可用性，不能改变测量和晋升语义。

## 审计模型

Runtime 保存 append-only 研究事件流，至少记录：

- Direction propose、reorder、ownership、update 和 closure；
- Experiment 创建和证据绑定；
- 测量时观察到的 Agent Git 身份；
- Runtime 生成 ref 的刷新；
- 共享历史的原始 Commit 到导入 Commit 映射及测量 notes；
- 同伴 Kernel adoption；
- Episode 选题决定；
- lineage migration；
- promotion 和 rejection。

Research Board 和 Markdown 导出均从事件流与不可变存储重新生成，不能被读回作为权威状态。

## 实施计划

### 代码映射

实现按以下模块边界进行，避免把 Agent Git、研究状态和搜索调度耦合进一个不可恢复的大事务：

| 设计能力 | 主要实现位置 | 改动性质 |
| --- | --- | --- |
| Research event store、queue、lease 与 Board | `aka/light/research/` | 新增 append-only 权威状态与只读投影 |
| Direction/Experiment 契约 | `workflow/journal_service.py`、`runtime/client/sandbox.py` | Journal API 改为 Research Store 的校验入口；保留旧 Journal 只读兼容 |
| 持久 island lineage | `workflow/search.py`、`workflow/search_driver.py` | 搜索 checkpoint 升级为 per-trajectory working/best/WIP，取消 winner 覆盖 |
| Agent 私有 Git | `workspace/agent_git.py`、`workspace/episode.py` | Episode draft 初始化受控本地 Git；Git 身份只用于工程审计 |
| Epoch 共享源码历史 | `workspace/shared_history.py`、`workflow/search_driver.py` | 过滤导出 `kernel.py` 与 `implementation/**`，导入 peer refs 和测量 notes |
| 前 5 个公开 Shape | `task/problem/layout.py`、`workflow/bootstrap.py`、`workflow/session_io.py` | Runtime 确定性生成只读 `public_shapes.json`；完整 evaluator 输入继续私有 |
| Prompt 与上下文恢复 | `workflow/prompts/`、`workflow/episode.py`、`agent/home.py` | Episode 开始自动注入 Board 与上一轮结算；同一 trajectory 跨 Episode 恢复 Claude Session，并以 Board 与 Git 保持权威恢复边界 |
| 审计与恢复 | Research event store、search checkpoint、Measurement Store | 先写事件再更新投影；所有生成视图均可重建 |

新搜索 checkpoint 使用 schema v2：

```json
{
  "schema_version": 2,
  "official_incumbent": {},
  "global_best": {},
  "lineages": {
    "T1": {
      "working_kernel": {},
      "lineage_best": {},
      "wip_checkpoint": null,
      "last_correct_kernel": {},
      "active_direction_id": null,
      "session_id": null
    }
  }
}
```

读取 schema v1 的旧 pooled-search checkpoint 时，协调器会把原先广播给所有 trajectory
的共享 seed 安全迁移为每条 lineage 的初始 working/best 状态；运行中的旧 Epoch 也保留
原 job 身份和阶段，然后按新的 per-lineage 选择逻辑继续。

标准完整 Evaluate、终态 Evaluate 和 ABBA 始终使用私有完整 workload。`--shape-id` 仍可选择公开或 opaque ID 做诊断；任何子集请求都标成 non-promotion evidence，不能成为 Frontier、Episode candidate 或 Promotion 证据。

实现以八组可独立验证的提交推进：Research Store 与契约、Board 与 Journal、Experiment 事实投影、persistent island、Agent Git、共享历史、公开 Shape、Prompt/恢复与端到端测试。每一组都必须保留 Measurement Record、Kernel Artifact 和 Promotion Record 的既有权威边界。

### 第一阶段：投影与 Prompt

- 从现有存储生成有大小限制的 Research Board；
- 在 Episode 开始时自动注入，并提供刷新命令；
- 增加显式 Direction 队列顺序和原子 reorder；
- 增加 `next_step` 与 `reopen_condition` 语义；
- 修改 Prompt 和终态引导，避免第一个局部改善立即触发 handoff。

该阶段不改变 promotion 和搜索选择语义。

### 第二阶段：Agent 私有 Git

- 为每条 trajectory 初始化独立本地 Git 仓库；
- 评测和晋升范围只包含 `kernel.py` 与 `implementation/`；
- 定义稳定的规范化源码 manifest，并以其 SHA-256 生成 Kernel ID；
- 建立 Agent Commit/tree、Kernel ID 和 Gateway Record ID 的可审计映射；
- 初始化和刷新 incumbent、global-best、lineage-best 与同伴源码历史 refs，且不改变 Agent 当前 branch；
- 使用受限 `fast-export` / `fast-import` 共享源码范围内的密封历史，并为测量过的 commit 生成 notes；
- 在权威 source digest 和 Kernel ID 旁记录可选 Agent Commit/tree 身份；
- 隔离 Git 配置，禁用凭据和远端能力；
- 由 Runtime 生成源码 diff 与测量摘要；
- 精简 Agent 必填 Experiment 字段；
- 即使 Agent rewrite history 或 GC，也保持 report 与 promotion 的精确 Kernel 身份。

### 第三阶段：持久研究 lineage

- 把 active Direction ownership 限制从全局改为每条 trajectory；
- 跨 Epoch 保留 lineage best 和 WIP；
- 停止使用 global winner 覆盖所有 trajectory seed；
- 增加主动 peer adoption 和 major-redesign milestone budget；
- 在 Epoch 边界共享完成的研究状态。

### 第四阶段：Workload 边界

- 生成公开语义 workload descriptor；
- 按 evaluator 规范顺序生成“前 5 个公开、其余隐藏”的稳定 visibility manifest；
- 在 Agent 工作区只物化 public Shape 参数；Agent 可以按公开或 opaque Shape ID 做子集诊断，但子集结果不能晋升；
- 默认 full Evaluate、终态 Evaluate 和 ABBA 固定使用完整 workload，同时保留显式、非晋升的诊断性子集 Evaluate；
- 隐藏 Shape 只返回全量聚合、opaque ID 或有大小限制的 per-regime 摘要；
- 增加 dispatcher 边界扰动和 workload-shift 检查。

## 评估方案

在模型、token、Gateway job 和墙钟预算一致的条件下比较：

1. 当前 Light；
2. Light + Research Board + Agent 私有 Git；
3. Light + Direction 连续性；
4. 完整 island search。

同时使用当前 benchmark 分布和发生变化的隐藏分布。统计：

- 相同 token/job 预算下当前完整 workload 的最佳 latency，以及一次性外部审计 workload 的 latency；
- 首个有效 major redesign 所需 Episode 数；
- 排名第一 Direction 在下一 Episode 被执行的比例；
- 相同 Direction 被重复发现的比例；
- major redesign 完成率和放弃率；
- 每百万 token 产生的有效实验数；
- Harness 故障损失的时间和 token；
- 回滚成功率和重复测量率；
- workload shift 后的性能保持率；
- dispatcher 复杂度和边界稳定性。

不能只看最终 latency。只有在不削弱正确性、可审计性和隐藏集泛化的前提下提高研究效率，设计才算成功。

## 必须保持的不变量

1. 只有 Runtime Measurement Record 能声明实际执行内容和测量结果。
2. 只有不可变 Kernel ID 能标识被评测源码。
3. Research Board 是只读派生视图。
4. Git checkout、restore、reset、merge、cherry-pick 或 tag 都不能晋升 Kernel。
5. trajectory 不能读取其他 trajectory 的实时草稿和 conversation。
6. major-redesign WIP 未通过正常完整评测与晋升前，不能成为 official incumbent。
7. 隐藏输入、参数与可逆映射不能进入 Agent 可见记忆；隐藏 Shape 的有界结果可以使用 opaque ID 展示。
8. 基础设施故障不能 refute Direction，也不能把 Kernel 标成 incorrect。
9. 队列顺序、ownership、Runtime ref 刷新和 peer adoption 必须支持崩溃恢复。
10. 可选研究模块不能改变测量和晋升语义。
11. Agent Git Commit ID 和 ref 永远不能作为权威测量或晋升证据。
