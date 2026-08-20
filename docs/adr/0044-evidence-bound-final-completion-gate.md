# ADR-0044：证据绑定且独立授权的最终完成门禁

- 状态：Accepted
- 日期：2026-08-19

## 背景

TaskManager 已用根 human gate 确认 finalized scope，但该接口按设计只接受无依赖根节点。真实悬架 DAG
完成源码检查点和两轮 validation 后，尾部 `authorized-completion-approval` 因而没有合法决策路径。
复用根范围确认、把 human gate 交给 Agent，或因所有前序节点终态而自动完成，都会混淆“范围已确认”
与“最终证据已验收”，并违反完成必须由授权主体决定的约束。

## 决定

1. 增加独立 `approve_completion_gate` application command 和 `approve-completion` HTTP command。它只
   接受有依赖、没有下游消费者的 terminal human gate；根门禁和中间人工门禁不能复用此路径。
2. 决策前要求运行中除该 gate 外的所有节点均处于受治理终态。每个直接依赖必须同时拥有 passing
   `VerificationReport`、验证 artifact，以及 reviewer acceptance 或 source integration artifact。
3. 最终 Approver 必须与直接依赖的最后 Reviewer 主体不同。HTTP 命令要求 `APPLY_DECISION`，本地
   `approver` role 可执行；planner、executor、reviewer 或 Agent 均失败关闭。
4. decision artifact 绑定 plan id/revision/hash、run、gate、actor、理由，以及直接依赖的 workflow
   state、report、verification 与 acceptance artifact。随后且仅随后由 `WorkflowKernel.transition` 将
   gate 推进为 `GateApproved`；run 的 `Completed` 仍由全部节点终态的确定性投影派生。
5. 最终完成只声明该隔离 TaskManager run 的证据闭环。它不授予主干 merge、push、deployment、release
   或远端变更权限，也不把隔离分支候选误报为已发布产品。

## 后果

- 根范围确认和尾部完成批准具有不同 API、RBAC、artifact type、校验条件与 UI 文案，审计时无需靠
  自然语言猜测 gate 语义。
- 不完整依赖、缺少 passing report/acceptance、非 sink gate、自批或空理由均不写新 run revision。
- 第一阶段不实现通用的任意中间 human decision gate；出现该需求时需定义自己的 decision contract，
  不能把完成批准接口扩展成万能跳转。

