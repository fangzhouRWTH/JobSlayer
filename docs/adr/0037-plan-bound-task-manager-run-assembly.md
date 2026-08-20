# ADR-0037：TaskManager 运行精确绑定固化计划并以证据反馈推进

- 状态：Accepted
- 日期：2026-08-19

## 背景

ADR-0036 把主产品面收紧为 TaskManager，并把第一阶段停在“用户固化精确计划 revision”。下一步
需要让 DAG 成为可追踪的运行事实，而不能把 finalized 直接解释为 running，也不能让外部 Agent
凭自然语言输出拥有节点状态、重试或完成决定。

现有 `TaskExecutionController` 面向代码任务，要求固定 repository、base commit、允许路径和验证
profile。通用 `TaskPlanNode` 目前没有这些执行绑定；把标题和描述直接转换成可写 Codex 任务会绕开
工作区、命令、验证和权限边界。因此本阶段需要先建立 provider-neutral 的运行装配与反馈接口，
并对未满足的真实执行条件失败关闭。

## 决定

1. 一个 TaskManager run 永久绑定一个 active finalized plan 的 `plan_id + revision + record_hash`。
   同一固化 revision 只能装配一个 run；后续规划调整产生新的 revision，不改变既有 run 输入。
2. 装配时为每个计划节点派生稳定 `workflow_task_id`，并逐节点调用 `WorkflowKernel.transition` 完成
   `Draft -> Planned`。run snapshot 保存完整 Kernel transition history，不能直接改写节点状态。
3. run revision 使用独立的追加式 JSONL hash chain 和原子 generation publish。计划绑定、创建者和
   创建时间在后续 append 中不可变；stale revision 写入必须拒绝。
4. executor 只通过 provider-neutral `start_or_locate(request)` 与 `observe(reference, cursor)` 协议
   接入。每次尝试必须在 provider side effect 前持久化稳定 `provider_start_key`；启动响应丢失后只
   能用同一 key 定位，显式 retry 才创建新 key。
5. provider reference 和每次 observation 必须携带非空 artifact ID。应用层在接受反馈前复核制品
   的 task/run binding 与内容哈希；保留 provider raw log 是 adapter 的职责。
6. executor 的 terminal success 只允许把节点从 `Implementing` 推进到 `Verifying`。失败进入
   `Failed`，取消进入 `Blocked`；executor 无权进入 `Completed`。完成仍要求确定性 verification
   report、授权 approval、集成证据和既有 Kernel 门禁。
7. TaskManager read facade 把 run truth 投影为 DAG、Backlog 和总 Log：无依赖的 planned 节点为
   ready，依赖未完成的节点为 waiting，provider 状态映射为 running/failed/verifying。UI 不自行
   推导或写入工作流状态。
8. 默认 CLI 启用本地 run persistence 和装配能力，但不配置具体 executor。session 分别公布
   `run_assembly` 与 `task_execution`；节点 start/retry/observe 还要求 `EXECUTE_TASK` 权限。
9. 本阶段不把同步 Codex planning adapter 冒充成 execution adapter，也不从通用 DAG 推断仓库、
   写路径、验证命令或预算。接入真实 Codex 前必须增加显式 execution binding/编译门禁。

## 后果

- TaskManager 已经从“计划已固化”推进到可持久、可恢复、可审计的运行骨架，重启后仍能读取精确
  节点状态和执行反馈。
- 外部 executor 可以替换，但只能返回引用和证据；JobSlayer 继续拥有状态、依赖、权限、重试和
  完成真相。
- `start_or_locate` 缩小了授权落盘与 provider 启动之间的崩溃窗口，但本地 JSONL 与制品 registry
  仍是两个事务边界，不宣称 exactly-once。
- 当前默认产品可创建 run，但不能真正 dispatch。下一阶段需定义 plan-to-execution binding，把
  repository/base/allowed paths/validation profile/预算显式固化后，再实现首个真实 executor adapter
  和 verifier/reviewer 推进路径。
