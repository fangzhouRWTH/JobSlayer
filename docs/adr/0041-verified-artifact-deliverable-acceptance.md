# ADR-0041：无源码差异的阶段性交付物使用验证后 Reviewer 接受终态

- 状态：Accepted
- 日期：2026-08-19
- 关联：ADR-0001、ADR-0004、ADR-0037、ADR-0039、ADR-0040

## 背景

TaskManager DAG 同时包含源码实现节点和勘察、设计等阶段性交付物节点。既有 `Completed` 语义要求
通过验证、review、merge decision 和 source integration，适合源码变更；但只读勘察没有 patch，
伪造 integration result 会破坏工程真相。另一方面，provider 退出码为零或自然语言自述不能直接满足
DAG 依赖，否则外部 Agent 会事实拥有完成决定。

## 决定

1. `TaskState` 增加终态 `DeliverableAccepted`。它表达“无源码差异的阶段性交付物已通过结构化验证并
   由授权 reviewer 接受”，不表达源码集成、整个 run 完成或最终验收。
2. executor adapter 只收集 provider-neutral 的结构化事实：provider run、源码 commit、workspace
   inspection、changed paths、可选 patch hash 和不可变 evidence IDs；adapter 不决定 pass/fail。
3. TaskManager 的确定性 verifier 重新验证 task/run evidence，检查 provider terminal success、workspace
   binding、允许/禁止路径和制品完整性，形成 `VerificationReport`。只有 passing report 才经
   `WorkflowKernel` 从 `Verifying` 进入 `Reviewing`。
4. `DeliverableAccepted` 只允许 human/policy 从 `Reviewing` 进入，必须同时提交 passing report 和
   immutable review evidence。review 记录绑定 plan id/revision/hash、node、report、交付物、验收标准、
   reviewer 和理由。
5. artifact-only acceptance 仅允许 task/milestone，且 verification evidence 必须同时满足：没有 changed
   paths、没有 source patch hash、working tree clean。任何源码差异都失败关闭，继续要求既有
   review/merge/integration 路径；validation 与 human gate 仍使用各自专用路径。
6. DAG 依赖可把 `Completed`、`GateApproved` 或 `DeliverableAccepted` 视为已满足，但只有所有节点进入
   合法终态时 run 才能成为 completed。最终 human gate 不复用本路径。

## 后果

- 勘察和设计类节点不需要伪造空 patch 或 source integration，同时仍不能由 Agent 自行宣告完成。
- API/UI 明确分为 `verify` 与 `accept-review` 两步；前者要求执行权限，后者要求独立 reviewer 权限。
- 当前实现只解决无源码差异的阶段性交付物。源码变更节点的 TaskManager source integration 以及
  validation node 专用执行仍是后续工作，不因本 ADR 被弱化。
