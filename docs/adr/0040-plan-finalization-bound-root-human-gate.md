# ADR-0040：根范围门禁以独立 Kernel 终态绑定显式计划固化

- 状态：Accepted
- 日期：2026-08-19
- 关联：ADR-0001、ADR-0004、ADR-0037、ADR-0038、ADR-0039

## 背景

真实 BraveNewWorld DAG 的第一个节点是 `human_gate`，用于确认固定 target、模型契约、路径边界和
验收范围。TaskManager run 原先把所有节点都映射到通用代码任务生命周期；若把人工门禁交给 Codex，
会违反授权边界；若伪造 `Completed`，又会绕过“通过验证报告与 source integration 后才能完成”的
不变量。

## 决定

1. `TaskState` 增加终态 `GateApproved`。它不是 `Completed`，不代表代码实现、验证、集成或整个 run
   完成；只表达一个受治理的人工门禁已被批准。
2. `WorkflowKernel.transition` 只允许从 `PlanReview` 进入 `GateApproved`，且 actor 必须是 human 或
   policy，并必须携带非空 immutable evidence。Agent 自批和无证据批准均被 Kernel 拒绝。
3. TaskManager 装配时，`human_gate` 从 `Draft -> Planned -> PlanReview`；其他节点仍从
   `Draft -> Planned`。依赖只把 `Completed` 或 `GateApproved` 视为已满足。
4. 当前只开放根范围门禁的 `confirm-scope` 命令。它要求节点是无依赖 human gate、调用者拥有
   `MANAGE_TASK_PLAN`、提供非空理由，并把 plan id/revision/record hash、actor、node 与理由登记为
   task/run-bound 内容寻址制品，再经 Kernel 进入 `GateApproved`。
5. 该命令绑定的是用户已经显式固化的精确计划，不适用于 DAG 尾部的最终验收门禁。最终门禁仍必须
   等所有实现、验证、review 与候选差异证据就绪后，由独立 approver 路径决定。

## 后果

- 根门禁不再阻塞第一个普通 task node，同时没有削弱代码任务的验证/集成完成门禁。
- 旧 run 中尚处于 `Planned` 的根 human gate 可在同一命令中先经 Kernel 进入 `PlanReview`，再登记
  批准；不会改写历史 revision。
- UI 要求输入范围确认理由；validation node 与非根 human gate 继续禁止 Agent dispatch。
