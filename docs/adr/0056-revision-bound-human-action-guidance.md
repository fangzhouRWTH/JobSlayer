# ADR-0056：以 revision-bound 指导明确所有人工交互停顿

- 状态：Accepted
- 日期：2026-09-03
- 依据：[ADR-0031](0031-versioned-collaborative-task-orchestration.md)、
  [ADR-0040](0040-plan-finalization-bound-root-human-gate.md)、
  [ADR-0042](0042-independent-source-review-and-isolated-run-checkpoint.md)、
  [ADR-0044](0044-evidence-bound-final-completion-gate.md)、
  [ADR-0055](0055-persistent-single-step-task-manager-coordinator.md)

## 背景

TaskManager 已能在 proposal、计划固化、run 装配、review、checkpoint approval、最终 human gate、失败和
阻塞处保守停顿，但过去主要通过状态名和 coordinator reason 告知操作者。操作者仍需查阅文档或理解
内部状态机，才能知道由谁处理、先审什么证据、执行哪条受治理命令、完成后检查什么，以及哪些动作不在
授权范围内。小时级任务在多次停顿和身份切换后尤其容易失去上下文。

提示文案不能成为第二状态机，也不能因为“显示了按钮或命令”而产生权限。

## 决定

1. TaskManager detail 新增 provider-neutral `human_actions` 只读投影。每项指导包含稳定 kind、可选
   node ID、标题、摘要、允许 actor type、所需 capability、处理要求、编号步骤、待审证据、允许决定、
   禁止动作以及精确 plan/run revision。
2. 指导由当前已校验的 plan assessment、run snapshot 和 Kernel node state 确定性派生；不另行持久化，
   不修改 task/run/coordinator state，也不取代 append-only 审计记录。plan 或 run revision 变化后，旧
   指导自然失效，调用者仍必须提交 command 自身要求的 `expected_revision`。
3. 当前投影覆盖候选图决定、计划补齐、计划固化、run 装配、根范围确认、无源码交付物接受、源码技术
   review、独立源码 checkpoint approval、最终完成批准、失败恢复和 blocker 处置。只有当前确实需要
   人工/政策输入的项才返回。
4. 每个步骤先要求核对绑定版本和原始证据，再描述允许的受治理 command，最后要求刷新并核对状态与
   审计结果。源码 checkpoint 和最终完成明确排除 main merge、push、deploy、release 等未授权外部动作。
5. 任务图在相关节点显示“需要人工处理”标识；右侧节点详情显示紧凑指导；执行页显示完整处理卡。计划级
   proposal/finalize/run assembly 指导在任务编排详情显示，不伪造一个 run node。
6. 发布并激活 SUID `focused-task-graph@7`，新增人工指导 region、与任务图/执行页的关系、处理旅程和
   `must` 要求；保留 v6 的全部 stable 单元。

## 后果

- 操作者无需从内部状态名猜测下一步，任务图和反馈本身就是可执行的交接说明。
- API 使用者也能消费同一结构化指导，而不必解析中文 coordinator reason。
- 指导中的 command 是对既有应用命令的解释，不是可执行授权。RBAC、独立 Reviewer/Approver、证据
  绑定和 `WorkflowKernel.transition` 仍可拒绝不合法调用。
- 当前 UI 仍未为每个决定提供一键写控件；某些步骤需要操作者使用相应身份和 API/CLI。后续可以基于
  同一指导契约逐项增加写入口，但不能把 guidance 本身当作权限或完成证据。
- 当前没有浏览器 push 通知；长任务到达人工停顿后需要刷新或由上层轮询读取最新 detail。

## 未采用方案

- 只扩写 coordinator 的自由文本 reason：无法稳定表达 actor、revision、证据和多个决定，也不便于 UI
  分区展示或测试。
- 将人工步骤作为新的可执行 DAG 节点：会重复既有 human gate/review Kernel 状态并产生第二状态所有者。
- 让 Agent 自动解释任何停顿：解释会随模型变化，不能保证覆盖禁止动作或绑定当前 revision。
- 指导出现后自动批准或重试：可读提示不是授权，不能绕过受治理 command。
