# ADR-0036：收紧为 TaskManager 产品面并复用既有治理真相

- 状态：Accepted
- 日期：2026-08-19

## 背景

JobSlayer 已经分别具备协作式任务编排、版本化 DAG、执行控制面、长任务恢复、制品、验证与人工
门禁，但原 Workbench 同时展示 Overview、Workflow Studio、Run Inspector、Artifact Review 和
Observability 等多个实验页面。继续围绕整个框架完善细节，会让首个产品闭环的入口、状态来源和
验收范围过宽。

当前要优先验证的具体问题是：用户与 Agent 多轮讨论任务，实时审查候选 DAG，固化一个 revision，
随后让受治理 Agent 按这个固定输入执行并反馈，同时能切换多任务、Backlog、总日志和历史。这个
产品面需要独立、清晰，但不能复制或夺取 `TaskOrchestrationService`、`WorkflowKernel`、执行器、
验证器和审计存储已经拥有的工程真相。

## 决定

1. 新增 `TaskManager` 产品面，但第一阶段作为同一进程内的 application facade 和独立 HTTP/UI
   路由实现，不拆微服务、不增加数据库或消息队列。只有出现独立部署、容量或故障边界证据时，
   才评估物理拆分。
2. TaskManager 不创建第二份可写计划状态。规划命令继续进入 `TaskOrchestrationService`，revision
   继续由追加式哈希链 journal 拥有；未来执行命令仍必须进入执行 application service 和
   `WorkflowKernel.transition`，不能由浏览器或 Agent 直接改状态。
3. 定义 provider-neutral TaskManager read contracts，把计划 snapshot、assessment、节点状态、
   Backlog 和完整历史日志投影成一个 revision-bound detail。契约预留 running、verifying、
   completed 等阶段，但 projector 只输出已有证据可以证明的阶段，不能因 UI 需要伪造 run。
4. 产品真相按 planning、execution、feedback 三层组合。每次执行必须绑定用户已固化的精确计划
   revision；运行中的规划变更形成新的候选/revision，不静默改写已经启动的 run 输入。
5. Milestone 1 只闭合“创建/讨论 → 候选 DAG → 应用/拒绝 → 完整度检查 → 固化 → 多任务、
   Backlog、总日志读取”。执行 adapter 尚未装配，因此 API 返回 `task_execution=false`、detail
   返回明确 blocker，UI 的“开始执行”保持禁用。finalized/ready 不等于 running 或 completed。
6. 新增认证的 `/api/task-manager` 高层接口。除用于取得进程随机 session token 的 session bootstrap
   外，任务列表、详情、证据读取和全部写命令都要求有效本地身份与 token。原
   `/api/orchestration` 作为兼容/高级规划接口保留。
7. Web 主入口收紧为 TaskManager：左侧主面板切换 DAG、Backlog、总 Log，右侧切换节点/任务信息
   和完整 Agent 对话。原实验页面不删除，仍可通过直接 hash route/命令面板访问，但不再占据主导航。
8. 第一阶段 UI 的图调整优先通过带 selected node context 的 Agent 讨论和 proposal 决定完成。
   原编排实验室的直接 node/edge CRUD 继续保留，等 TaskManager 使用数据证明需要精细手工编辑时，
   再把最小编辑能力提升到主产品面。

## 后果

- 用户现在有一个边界清楚的入口，可以真实创建、讨论、审查和固化多个任务，而不必理解整个框架。
- Facade 只组合现有领域/应用真相，domain 不暴露 Codex 或 UI 对象，也没有引入新的基础设施依赖。
- read model 中的运行阶段是稳定的未来兼容面，不是当前已实现声明；明确 blocker 使执行缺口可见。
- 下一阶段必须实现“finalized revision → governed run assembly → normalized feedback/read projection”，
  并用允许/拒绝转换、验证与授权门禁证明，之后才能启用“开始执行”。

