# 面向 Runtime 的使用方式

[English](quickstart.md) | 中文

Core 不是独立 Campaign CLI。Runtime 导入精确 Core Commit，准备 Workspace 和 Capability，再为每个
阶段启动一次 `src/main.py`。缺少 Runtime Manifest 时入口必须失败关闭。

## 1. 前置条件

部署需要：

- 已配置 Registry、Artifact Store、Agate、Sandbox 和 Token Limit 的 Runtime；
- Runtime Approved Git Base 能按完整 Commit 获取本仓库；
- Core Worker Image 中的 Python 3；
- `atrex-agent.json` 选择的 `claude`、`codex`、`pi` 或 `qodercli`；
- 通过 Runtime 显式白名单传递的 Backend Credential；
- 可达的 Gateway 和可选 Wiki Runtime Service。

## 2. 选择 Backend

在提交 Core Revision 前修改 `atrex-agent.json`：

```json
{
  "schema_version": 2,
  "agent_backend": "claude",
  "reasoning_effort": "max",
  "session_settings": "",
  "prompts": {
    "problem_generalization": "prompts/generalize_agent_problem.md",
    "framework_baseline": "prompts/framework_baseline.md",
    "optimization_attempt": "prompts/episode.md"
  }
}
```

Runtime 不选择第二套 Optimizer Framework。修改 Backend 会产生新的 Core Revision 并接受独立
评估。Credential 不得写进 Manifest。

## 3. 发布精确 Commit

Runtime 配置固定批准的仓库 URL 与 Git Executable，Campaign Bootstrap 只提供完整 Commit SHA：

```json
{
  "base_revision": {
    "commit": "0123456789abcdef0123456789abcdef01234567"
  }
}
```

Runtime 校验 Commit/Tree，拒绝不安全内容及未解析或未批准 Submodule，在不执行仓库代码的前提下
归档，校验 `atrex-bundle.json` 并封存 Bundle。当前 Core Tree 没有 Submodule。

## 4. Bootstrap Campaign

使用 Campaign Bootstrap v2。公共字段只写一次，DSL Seed 与初始 Evidence 放在 `lineages`：

```json
{
  "schema_version": 2,
  "creation_key": "vector-add-h100",
  "operator": "vector_add",
  "hardware_target": "nvidia-h100",
  "evaluation_contract": "/trusted/inputs/evaluation.json",
  "base_revision": {
    "commit": "0123456789abcdef0123456789abcdef01234567"
  },
  "attempts_per_branch": 8,
  "dsls": ["triton"],
  "lineages": {
    "triton": {
      "baseline_kernel": "/trusted/inputs/triton-kernel",
      "initial_evidence": "/trusted/inputs/triton-evidence"
    }
  }
}
```

先启动 Runtime Service，再由受监督进程执行 Bootstrap：

```bash
atrex-kernel-agent-runtime serve --config /etc/atrex/runtime.json
atrex-kernel-agent-runtime bootstrap \
  --config /etc/atrex/runtime.json \
  --spec /trusted/inputs/bootstrap.json
```

未指定 `dsls` 时使用配置中的默认 DSL，代码默认顺序为 CUDA、Triton、CuteDSL。新 Campaign 只
运行一次 `problem_generalization`，每条选中 Lineage 运行一次 `framework_baseline`。只有 Core
Report 与正确的权威 Gateway Outcome 对账后，Lineage 才 Ready。

## 5. 运行 Epoch

```bash
atrex-kernel-agent-runtime run-campaign \
  --config /etc/atrex/runtime.json \
  --campaign campaign_0123456789abcdef0123456789abcdef \
  --target-epoch 10
```

每个 Epoch 从同一 Checkpoint 创建 Active/Challenger Branch，每条 Branch 获得固定数量的全新
Attempt Session。保留 Kernel 成为同分支下一 Attempt 的 Incumbent；另一分支中间结果不可见。
Kernel 保留与 Agent 晋升都使用普通权威 Evaluate。

## 6. Attempt 可见内容

| 用途 | 路径 |
| --- | --- |
| Incumbent Kernel | `input/kernel` |
| Candidate Kernel | `work/kernel` |
| 统一的晋升 Lineage/当前 Attempt Evidence View | `input/evidence` |
| 公开算子契约 | `input/agent-problem` |
| Core Revision | `agent/optimizer` |
| Request、Plan、Journal、Report | `scratch` |
| 未脱敏 Agent Session Artifact 与规范化用量索引 | `sessions` |

Agent 使用 Prompt 声明的精确命令：

```bash
python agent/optimizer/src/runtime_tools.py gateway-execute --request scratch/evaluate.json
python agent/optimizer/src/runtime_tools.py wiki-query --request scratch/wiki.json
python agent/optimizer/src/runtime_tools.py record-experiment --request scratch/experiment.json
python agent/optimizer/src/runtime_tools.py attempt-report --request scratch/report.json
```

Request 必须是 `scratch/` 下受限的 Regular JSON File。新内容使用新 Idempotency Key；只有完全相同
的 Request 才能重放同一个 Key。

Worker 能读取短期 Scoped Capability，但拿不到上游 Agate/Wiki Credential。直接请求与规范客户端
请求都必须通过 Runtime Authorization。Bubblewrap `host` 网络没有目标过滤，生产部署必须在网络
层限制 Egress。

## 7. 本地检查

Core 自己持有单元测试和静态策略：

```bash
python -m pytest -q
ruff check src tests
mypy src tests
```

Runtime 仓库另外持有跨仓协议和 Worker Integration Test。轻量语法检查：

```bash
PYTHONPYCACHEPREFIX=/tmp/atrex-core-pycache \
  python -m compileall -q src tests
```

不要伪造 Runtime 环境把 `src/main.py` 当作独立 Optimizer，这会绕过设计中的系统边界。

## 8. 常见失败

- 缺少 Runtime Environment：入口没有在已准备 Workspace 中启动。
- Manifest 版本、字段或路径不一致：Core/Runtime 协议不一致或 Workspace 被修改。
- Gateway/Wiki Capability 被拒绝：过期、耗尽、撤销或操作未授权。
- Token Report 不完整：Backend 没有暴露可靠 Usage；Runtime 不会估算。
- 缺少终态 Report：Agent 退出、超时、耗尽 Budget 或输出无效。
- Candidate 被拒绝：Report、Candidate Digest 或权威 Gateway Outcome 不一致。
