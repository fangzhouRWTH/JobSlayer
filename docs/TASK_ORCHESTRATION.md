# 协作式任务编排

## 当前能力

任务编排纵向切片实现以下循环：

```text
用户任务描述
      ↓
多轮讨论 ──→ PlanningAgent proposal
      ↓              ↓
讨论记录        待应用拓扑图
      └────── 用户显式应用 ──────┐
                                  ↓
                         已应用计划 revision
                                  ↓
                  节点 CRUD / 支线 / 子任务
                                  ↓
                         用户固化最终路径
                                  ↓
                 append-only revision + hash
```

Agent 不能直接修改计划。讨论产生的图以 `pending_proposal` 展示；只有用户应用后才进入
`snapshot.nodes/edges`。定稿后仍可编辑，但系统会创建新 draft revision，并保留之前的
`latest_finalized_revision`，不覆盖历史确认结果。

该计划是执行前设计制品，不是 `TaskState`，不会启动 Agent、调用 shell、改变 WorkflowKernel、
合并 Git 或把任务标记为完成。

## 启动

首次使用先准备一个短期 planner 身份：

```bash
./jobslayer create-local-identity-key .jobslayer/identity/planner-key.json
./jobslayer issue-local-identity-session \
  --key .jobslayer/identity/planner-key.json \
  --subject-id local-planner \
  --display-name "Local Planner" \
  --role planner \
  --output .jobslayer/identity/planner-session.json
```

终端一启动 loopback 计划 API：

```bash
./jobslayer orchestration-api \
  --identity-session .jobslayer/identity/planner-session.json \
  --identity-key .jobslayer/identity/planner-key.json
```

终端二启动 Workbench：

```bash
sh ./init.sh -- npm --prefix ui-framework run dev
```

打开 `http://127.0.0.1:4173/#/orchestration`。Vite 将 `/api/orchestration` 同源代理到默认
`127.0.0.1:8780`；修改 API 端口时需要同步调整本地 Vite proxy。

Windows 对应使用 `jobslayer.cmd`、`init.cmd` 和相同参数。

## 计划契约

| 对象 | 责任 |
|---|---|
| `TaskPlanSnapshot` | 某一 revision 的任务描述、已应用图、讨论和待应用提案 |
| `TaskPlanNode` | 稳定节点 ID、标题、描述、类型和 provider-neutral executor hint |
| `TaskPlanEdge` | sequence、dependency、branch 或 subtask 关系 |
| `TaskPlanProposal` | Agent 针对某 revision 提出的完整候选图，不自动应用 |
| `TaskPlanRevisionRecord` | actor、operation、snapshot、previous hash 和 record hash |
| `PlanningAgent` | 可替换的提案端口；不能读取 store 或决定应用/定稿 |
| `TaskOrchestrationService` | expected revision、图校验、CRUD、proposal 应用和定稿规则 |
| `LocalTaskPlanStore` | 单进程本地 JSONL 原子追加与哈希链验证 |

React Flow 的坐标、缩放和选择态只存在于浏览器。系统计划只保存节点和语义边，因此将来可由
Web、CLI、SDK 或另一个布局器共同读取。

## 本地 API

查询：

- `GET /api/orchestration/session`
- `GET /api/orchestration/plans`
- `GET /api/orchestration/plans/{plan_id}`
- `GET /api/orchestration/plans/{plan_id}/history`

写命令：

- `POST /api/orchestration/plans`
- `POST /api/orchestration/plans/{plan_id}/messages`
- `POST /api/orchestration/plans/{plan_id}/proposals/apply`
- `POST /api/orchestration/plans/{plan_id}/nodes`
- `PATCH|DELETE /api/orchestration/plans/{plan_id}/nodes/{node_id}`
- `POST /api/orchestration/plans/{plan_id}/nodes/{node_id}/split`
- `POST /api/orchestration/plans/{plan_id}/finalize`

所有写命令要求 `X-JobSlayer-Session`、精确字段和最新 `expected_revision`。过期 revision 返回
`409`；过期身份或错误 token 返回 `403`。服务仅绑定 loopback、无 CORS，并设置 `no-store`、
`nosniff`、`DENY` frame 和拒绝所有内容的 CSP。

## 当前限制与扩展点

- `LocalPlanningAgent` 是确定性协议 fixture，不是 Codex，也不会发起外部模型调用；
- 本地 store 只支持单进程 writer；远程或多人并发应实现事务 `TaskPlanStore`；
- 当前没有图上拖放持久化、edge CRUD、冲突合并或 plan-to-Workflow IR 编译器；
- Vite 是开发工作台，API 尚未提供生产静态资产或远程部署；
- finalized 只表示用户确认了计划 revision，不表示任务已规划、验证、批准或完成。

真实 Codex 接入应新增 adapter：把上下文和对话交给 Codex、将输出归一化为
`TaskPlanProposal`、把原始日志另存为制品，并继续要求用户显式应用。不能让 SDK response、
模型置信度或聊天文本进入领域契约。

