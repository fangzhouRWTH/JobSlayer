# ADR-0031：版本化协作式任务编排与 Agent 提案边界

- 状态：Accepted
- 日期：2026-08-17

## 背景

使用者需要从一段自然语言任务开始，与 Codex 类 Agent 多轮讨论，将任务逐步细化为可执行
节点，并同步看到拓扑变化。最终路径应由用户确认并由系统固化；之后仍可进行节点 CRUD、
支线和子任务调整，同时保留原定稿版本。

如果把聊天内容或 React Flow JSON直接作为工作流真相，模型或浏览器就会拥有计划状态；如果
原地覆盖最终方案，则无法证明用户当时确认的路径。Task plan 也不能冒充已经进入执行阶段的
`TaskState` 或绕过 `WorkflowKernel`。

## 决定

1. 新增 provider-neutral `TaskPlanSnapshot`：由任务描述、DAG 节点、带语义的边、讨论消息、
   待应用提案和 revision 元数据组成。节点支持 task、milestone、validation、human gate；
   边支持 sequence、dependency、branch、subtask。外部 Agent/React Flow 对象不进入契约。
2. `PlanningAgent` 只能返回完整的 `TaskPlanProposal`。讨论会把用户消息、Agent 回复和待应用
   图记录为新 revision，但不会改变已应用 nodes/edges；只有认证用户显式应用 proposal 后，
   系统才把提案图写入下一 revision。
3. 节点创建、读取、更新、删除、支线和子任务均由 `TaskOrchestrationService` 校验
   `expected_revision` 后执行。图必须引用存在节点、ID 唯一且无环；过期并发命令失败关闭。
4. 用户 finalization 追加一个 `finalized` snapshot，绑定 actor、时间、revision 和哈希链。
   定稿不等于 `TaskState.PLANNED`，也不触发 Agent、命令、Git 或执行工作流。定稿后的编辑会
   追加新的 draft revision，并保留 `latest_finalized_revision` 指向上一个确认版本。
5. 本地 adapter 采用每计划一份 JSONL、SHA-256 previous-hash 链和原子 generation 发布；
   不原地覆盖历史。首版只承诺单进程 writer，多进程/远程协作需要事务 store adapter。
6. 新增 `planner` RBAC role 与 `manage_task_plan` action。写 API 只绑定 loopback，要求启动时
   已验证的短期签名 session、进程随机提交 token 和未过期 principal；不设置 CORS。
7. React Workbench 通过 Vite same-origin proxy 调用该 API，展示讨论、proposal/applied 图、
   CRUD、分裂和 revision/hash。画布位置仍是展示元数据，不写入 provider-neutral plan。
8. 首版 `LocalPlanningAgent` 是明确标识的确定性 fixture，用于证明协议和多轮交互。真实 Codex
   只能作为新的 `PlanningAgent` adapter 接入，保留原始日志/用量，并继续由用户应用提案；
   本 ADR 不授权外部模型调用。

## 后果

- 使用者可以运行一条真实的“描述 → 讨论 → 拓扑提案 → 用户应用 → 定稿 → 再修订”纵向切片，
  且定稿证据不会被后续编辑覆盖。
- 任务计划是执行前的版本化设计制品，不是第二套执行状态机。把某个 finalized revision 编译为
  canonical Workflow IR 或创建 `TaskSpec` 仍需单独的受治理 application service。
- 当前本地 JSONL store 不提供多进程锁、远程同步、合并冲突 UI 或垃圾回收；这些能力必须在
  有真实协作需求时通过同一 store/proposal 契约扩展。

