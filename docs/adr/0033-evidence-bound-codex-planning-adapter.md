# ADR-0033：证据绑定的 Codex 规划适配器

- 状态：Accepted
- 日期：2026-08-17

## 背景

ADR-0031/0032 已稳定协作计划的 revision、完整图提案、人工应用和确定性完整度边界。下一步需要
证明真实外部规划器可以接入，但不能让模型分配权威提案身份、写计划 store、决定重试、绕过
图校验或把自然语言当作完成证据。

Codex CLI 已提供非交互 `exec`、JSONL 事件、最终结构化输出 Schema 和最终消息文件。项目现有
Codex 执行器也已经有最小环境继承、只读/workspace-write sandbox 映射、进程组监督和原始日志
保留经验，因此规划接入不应再引入 SDK 对象或第二套领域状态。

## 决定

1. `PlanningAgent` 返回 `TaskPlanProposalDraft`，只包含摘要、完整候选 DAG 和可选证据引用。
   `TaskOrchestrationService` 分配 `proposal_id`、绑定 `based_on_revision`、记录 adapter/时间并生成
   `TaskPlanProposal`。模型不拥有权威 envelope。
2. 新增 `CodexPlanningAgent` adapter，使用 `codex exec --json --output-schema
   --output-last-message`，固定 `read-only` sandbox、ephemeral 会话、单次调用、显式模型、超时、
   prompt/output 字节上限和最小环境继承。它不修改工作区、不自动重试、不调用 WorkflowKernel。
3. Codex 输出 Schema 只允许规划内容。节点和边重新构造成 provider-neutral 领域对象，并通过
   长度、枚举、稳定 ID、引用完整性和 DAG 无环校验；已有节点的 JobSlayer attributes 按 ID 保留。
4. 每次已启动调用登记精确 prompt、原始 JSONL、stderr 和最终 JSON 四类不可变
   `ArtifactManifest`。成功提案保存 invocation ID 与四个 artifact ID；失败制品仍可按 plan/run
   查询，但不会追加计划 revision。
5. `orchestration-api` 默认继续使用离线、确定性的 `LocalPlanningAgent`。Codex 仅在同时选择
   `--planning-agent codex`、提供显式 `--codex-model` 并设置
   `--allow-external-planning-agent` 时启用。adapter 不继承 ambient API key，只允许显式
   `CODEX_HOME` 登录上下文；启动服务器本身不调用模型，创建/讨论计划时才调用。
6. provider 启动、超时、非零退出、JSONL 或结构化输出失败统一为 `PlanningAgentError`。loopback
   API 返回 `502`，且不把失败响应转换成用户 revision。JobSlayer 不根据模型置信度判断应用或定稿。

## 后果

- 同一 UI/API 可以在不改变领域契约的情况下选择本地 fixture 或 Codex；模型仍只能产生待人工
  应用的完整图。
- 真实交互输入、事件、诊断和最终输出有内容寻址证据，故障不会因只返回一条 HTTP 错误而丢失。
- 本轮契约测试只运行假 CLI，没有发起真实或付费模型调用，也没有新增 Python/npm 依赖。
- 当前 CLI 只能强制模型选择、单次调用、时间和 I/O 上限，不能从 Codex CLI 预先强制美元/token
  上限。生产启用前仍需账户侧预算、短期凭据/授权、保留策略和一次明确批准的真实 smoke test。
- 多人事务 plan store、逐项提案合并、制品浏览 API 和 plan-to-Workflow IR 仍是后续工作。
