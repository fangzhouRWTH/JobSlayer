# ADR-0058：先用一任务一 Run 与终态优先投影闭合 TaskManager

- 状态：Accepted
- 日期：2026-09-04
- 依据：[ADR-0036](0036-focused-task-manager-product-surface.md)、
  [ADR-0037](0037-plan-bound-task-manager-run-assembly.md)、
  [ADR-0055](0055-persistent-single-step-task-manager-coordinator.md)、
  [ADR-0057](0057-governed-human-decision-controls-and-assistance.md)

## 背景

真实 Life Game Run 已在 revision 17 完成，但完成后的规划对话留下了 revision 18/19 草稿记录。
TaskManager 原先只用“最新计划 revision/hash”查找 Run，并让 pending proposal 先于执行终态决定任务
stage，导致同一任务被错误显示为“规划中、未装配 Run、存在 Backlog”。精简后的 UI 又没有完整暴露
目标绑定、固化和装配路径，人工证据区要求逐项勾选无法直接打开的 ID，用户无法判断唯一下一步。

修复不能改写既有追加日志，也不能通过放宽权限或完成门禁掩盖投影冲突。

## 决定

1. 当前 TaskManager 产品面采用“一任务一 Run”。任务一旦装配任意 Run，讨论、proposal 应用/拒绝、
   target 重绑和再次固化全部失败关闭；新的目标或调整创建新任务。底层历史规划 API 暂保留兼容，
   但不作为 TaskManager 正常写入口。
2. 写操作继续要求 Run 与 finalized plan revision/hash 精确匹配。读操作先查精确绑定；为兼容已存在的
   异常记录，只允许退回同 `plan_id` 的最新终态 Run。这个兼容路径只修复投影，不创建记录、不重放
   副作用、不允许继续写旧 Run。
3. archived 状态仍最高优先；其后 completed/cancelled Run 先于 pending proposal 或最新草稿决定任务
   stage。存在 Run 时 DAG 和依赖来自冻结的 execution binding；终态 Run 的 Backlog、target blocker
   和 human actions 不得被较新的规划记录重新打开。
4. 编排页增加单一“闭环下一步”：无 Run 时依次只显示绑定目标、固化计划或装配 Run；有 Run 时显示
   冻结 plan/run binding 和执行页入口，并锁定规划输入。后端 blocker 原样显示，能力未连接时不提供
   虚假的执行按钮。
5. 执行页对 completed Run 只显示终态摘要与全部已满足节点，不显示“coordinator 未初始化”或继续推进。
   节点满足计数包含 `completed`、`deliverable_accepted` 和 `gate_approved`。
6. 人工决定区保留机器 evidence reference 供审计，但折叠显示；前端只要求一次明确确认：操作者已经
   检查可见验证摘要和真实交付物，并理解 task/run/revision 边界。后端 artifact hash、verification、
   RBAC、独立 actor 和 `WorkflowKernel.transition` 门禁完全不变。
7. 发布并激活 SUID `focused-task-graph@9`，逐字保留 v8 的全部 stable 单元。

## 后果

- 已完成任务不会因后写规划记录在 UI 中“复活”，既有哈希链也无需迁移或重写。
- 当前主路径更窄、更容易验证；同任务重新规划、多 Run 比较和计划回开明确后置，需要新的状态模型与
  迁移 ADR，不能绕过本决定临时加入。
- 默认桌面入口仍不自动启用 durable executor、validator 或 integrator；能力缺失与 target blocker
  必须诚实显示。源码 Reviewer/Approver 独立性保持不变。
- 兼容旧终态 Run 是只读恢复规则；非终态孤儿 Run 不做猜测性回接，需要显式诊断与恢复流程。

## 未采用方案

- 把 plan revision 19 回写成 finalized 或删除 revision 18/19：破坏 append-only 审计事实。
- 让最新草稿覆盖 completed Run：继续产生双重真相。
- 自动把规划修改派生为第二个 Run：在当前闭环尚未稳定时扩大状态空间。
- 取消 evidence/RBAC/独立审批约束以减少点击：会让 UI 便利性拥有完成判定。
