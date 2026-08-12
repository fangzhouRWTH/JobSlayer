# ADR-0020：执行恢复以不可变 intent/outcome 双制品为自动边界

- 状态：Accepted
- 日期：2026-08-12

## 背景

execution 是本地闭环的第一个业务动作。此前 run directory 创建后，控制器会建立
worktree、推进 workflow、启动 Agent、收集日志和补丁、执行验证，最后才追加第一
条 run record。任何中途退出都会留下空 ledger；恢复器既无法重构原 context/outcome，
也无法判断 Agent 是否启动，因而只能笼统要求人工处理。

自动重跑 Agent 不可接受：它可能重复外部工具调用和源码副作用，且重试策略必须由
JobSlayer 明确拥有。要安全补写首记录，需要在 Agent 前持久化授权输入，并在业务
动作完成后持久化严格 outcome；两者之间的模糊窗口必须停止而不是猜测。

## 决定

1. 定义 provider-neutral `TaskExecutionIntent`，完整绑定唯一 intent/run id、
   `TaskSpec`、`AgentInvocation`、`ValidationProfile`、
   `TaskExecutionAuthorization` 和准备时间，并在模型层验证 task/run/profile/
   authority 及授权窗口。
2. 本地 adapter 使用 `LocalExecutionIntentEnvelope` 追加 source-controlled runbook
   path/hash、`TestbedSpec` 与通过的 `TestbedInspection`。在调用 controller/Agent
   前，协调器先注册 run-bound `task-execution-intent` 内容寻址制品。
3. `TaskExecutionController` 返回严格 `TaskExecutionOutcome` 后，协调器在追加首条
   ledger 前注册 run-bound `task-execution-outcome` 制品。正常 execution record
   同时引用 intent/outcome manifests，并保留与此前兼容的 context/outcome payload。
4. `ArtifactRegistry` 增加 provider-neutral `list_manifests` 查询，恢复器可按 type、
   task 和 run 查找经过内容校验的持久 manifest；本地实现不跳过损坏 manifest。
5. 空 ledger 只有在唯一 intent 和唯一 outcome 均存在、内容及 producer/task/run
   binding 有效、workflow 最终状态等于 outcome、全部 outcome 制品有效、worktree
   与 persisted patch 相同时，才可执行 `resume_execution_record`。
6. 只有 intent 而没有 outcome 时，无论 workflow/Agent 可能推进到何处，均返回
   `manual_intervention`，原因明确说明不会重跑 Agent。多个 intent/outcome、内容
   篡改、manifest 绑定不一致、journal 或 workspace 漂移均为 `invalid_evidence`。

## 理由

- intent 是“获准尝试一次”的证据，不是“可以任意重试”的许可；outcome 才是可
  自动补写首记录的提交后事实。
- 内容寻址 outcome 保存控制器返回的完整严格模型，避免从零散日志和 manifest
  猜测状态、错误类型或验证结论。
- 把 source/testbed 信息留在本地 envelope，保持 domain intent 不依赖具体测试床
  或文件系统 adapter。

## 后果

- execution outcome 已持久化、ledger 未发布的崩溃窗口可以在重启后幂等恢复，
  且不再次调用 executor。
- Agent 运行中或 final workflow transition 后、outcome artifact 前退出仍需人工
  处理；这是刻意保守的提交边界，不是自动恢复缺陷。
- 新 execution records 引用两个额外 manifest；reader 兼容此前没有这些字段的旧
  Phase 0 记录。
- 本地 manifest 查询是目录扫描，适合 Phase 0；事务数据库 adapter 需要实现等价
  metadata query 和唯一约束。

## 未选择的方案

- 看到 intent 就自动重跑：拒绝，会把 retry policy 交给恢复器并重复副作用。
- 仅从 workflow 最终状态重建 outcome：拒绝，状态不包含 Agent logs、patch、验证和
  failure 证据的完整绑定。
- 在 controller 内部每一步写可变 checkpoint：暂不选择；应先用 intent/outcome
  明确最小提交协议，再由真实长任务需求证明细粒度 checkpoint 的必要性。
