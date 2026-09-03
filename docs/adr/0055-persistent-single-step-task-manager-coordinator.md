# ADR-0055：以持久单步 coordinator 收束 TaskManager 串行执行

- 状态：Accepted
- 日期：2026-09-03
- 依据：[ADR-0023](0023-fail-closed-long-running-development-safety.md)、
  [ADR-0035](0035-provider-neutral-resumable-long-running-control-plane.md)、
  [ADR-0037](0037-plan-bound-task-manager-run-assembly.md)、
  [ADR-0045](0045-focused-serial-task-manager-and-anygine-app-testbed.md)、
  [ADR-0050](0050-semantic-elastic-ui-design-contract.md)

## 背景

TaskManager 已具备 plan revision 固化、target/source hash 绑定、run 装配、持久 Codex worker、
确定性 validation、源码 review/checkpoint 和最终人工门禁，但每个命令仍需操作者自行判断并逐一调用。
小时级任务中，浏览器循环、内存队列或模型自己决定下一步都无法提供可靠的重启恢复、排他执行和
完成治理。

本阶段只需要证明单 run 的成功路径和保守停止边界，不引入分布式调度器，也不建立第二套 task
状态机。

## 决定

1. 新增 provider-neutral coordinator cursor/intent 契约和本地追加式 JSONL store。每条 cursor revision
   使用 SHA-256 前向哈希链、前缀保持的原子 generation 发布和严格单调的 run revision 绑定。
2. coordinator 只读取 finalized DAG 与 TaskManager run 投影，并调用现有
   `TaskManagerExecutionService` commands；所有节点状态仍只能经 `WorkflowKernel.transition` 改变。
3. 一个 tick 最多执行一个 application command。外部副作用前先持久化确定性 intent；如果进程在
   command 后、outcome 前终止，下一次 tick 以更高的 append-only run revision 对账并清除 intent，
   不重复启动 provider。provider start key 和 source integration key 继续承担下层幂等语义。
4. 每个 run 使用既有 SQLite worker lease 取得单活权，live lease 冲突失败关闭；orphan lease 可按
   既有恢复协议回收。当前本地 lease 与 cursor 尚未和 run journal 形成同一数据库事务，因此 run
   revision 是恢复时唯一权威事实。
5. 稳定 DAG 顺序选择第一个 dependency-ready 节点：普通 task/milestone 路由到 executor，validation
   路由到 finalized profile runner；implementing、verifying、integrating 分别路由 observe、verify、
   integrate。human gate、review/merge review、failed、blocked、repairing 和 cancelled 一律停顿并显示
   原因，coordinator 不代替 review、approval、retry、repair 或完成决定。
6. loopback API 新增 revision-bound `coordinator/tick`，仍要求 executor authority；session 显式公布
   `serial_coordinator` capability。具体执行页只增加 cursor、下一动作、原因和“推进一步”，不在
   浏览器运行自动循环。
7. 源码技术 review 可由独立 human 或 agent 形成 `ReviewReport`，但 exact source checkpoint 仍必须由
   独立 human approver 批准。无源码差异的确定性 validation deliverable 可由 human 或明确命名的
   policy 接受；agent 不得接受这类 deliverable，最终 completion gate 仍要求授权且独立的 human。
8. 发布并由产品负责人激活 SUID `focused-task-graph@6`。保留 v5 的全部 13 个 stable 单元，新增
   execution coordinator region、关系、单步旅程和控制面真实性要求。

## 后果

- API/UI 重启后可从持久 cursor 与 run journal 继续，不会因为页面刷新丢失下一动作。
- 单击不是“一键完成”：长 provider turn 会持续由 worker 运行，后续 tick 只观察；到 review、人工门
  或故障时必须由有权限的 actor 作出显式动作。
- 当前 coordinator 是同步、operator-triggered tick，不是后台 scheduler。多次观察与验证仍需显式
  点击或上层受控轮询；机器重启后的 worker 进程恢复和自动 repair 是后续退出条件。
- validation failure 会进入 `Repairing` 并保守停顿。首个真实 Life Game 用例先验证成功闭环；有界
  repair command、取消和预算驱动重试继续后置，不能把手工改工作树冒充自动修复。

## 未采用方案

- 让模型读取 DAG 后自行循环到完成：模型不能拥有状态、重试、验证或完成决定。
- 在 React 中用定时器连续调用所有命令：刷新即丢状态，也无法可靠排除并发副作用。
- 为本地纵向切片立即引入 Temporal/Dagger：当前退出条件尚不需要新的基础设施依赖。
- 自动接受 passing source patch 或最终 gate：passing report 是证据，不是授权决定。
