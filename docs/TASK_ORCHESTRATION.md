# 协作式任务编排

## 当前能力

任务编排纵向切片实现以下循环：

```text
用户任务描述
      ↓
多轮讨论 ──→ PlanningAgent proposal
      ↓              ↓
讨论记录        待应用拓扑图与差异
      └── 用户显式应用或拒绝 ────┐
                                  ↓
                         已应用计划 revision
                                  ↓
             节点/语义边 CRUD / 支线 / 子任务
                                  ↓
                    确定性完整度检查与修订
                                  ↓
                         用户固化最终路径
                                  ↓
                 append-only revision + hash
```

Agent 不能直接修改计划。讨论产生的图以 `pending_proposal` 展示；只有用户应用后才进入
`snapshot.nodes/edges`。定稿后仍可编辑，但系统会创建新 draft revision，并保留之前的
`latest_finalized_revision`，不覆盖历史确认结果。

Workbench 还提供多计划搜索/切换/归档、proposal 与 applied graph 差异、revision 对比和“从历史
版本派生新草案”。archive、restore 和 derive 都追加新 revision，不删除或改写原历史。

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
| `TaskPlanProposalDraft` | Agent 返回的非权威完整候选图内容与可选证据引用 |
| `TaskPlanProposal` | JobSlayer 分配身份、绑定 revision/adapter/时间后的待应用候选图 |
| `TaskPlanRevisionRecord` | actor、operation、snapshot、previous hash 和 record hash |
| `PlanningAgent` | 可替换的提案端口；不能读取 store 或决定应用/定稿 |
| `TaskOrchestrationService` | expected revision、图校验、CRUD、proposal 应用和定稿规则 |
| `LocalTaskPlanStore` | 单进程本地 JSONL 原子追加与哈希链验证 |
| `TaskPlanAssessment` | 对 blocker/warning/info 的确定性规划完整度评估 |
| `PlanningArtifactQuery` | 对 Codex 规划制品进行 plan-bound、去 URI、哈希验证的只读投影 |

节点还可结构化保存验收标准、交付物、约束、风险、验证要求和人工决策标记。字符串项有数量与
长度上限；它们描述规划意图，不授权执行器或修改工作流状态。

当前完整度评估会检查：待处理 proposal、空图、归档状态、缺少 validation/human gate、验证
节点缺少可执行要求、节点缺少验收标准或交付物，以及孤立节点。blocker 会拒绝 finalization；
warning/info 用于提示用户继续完善，不冒充执行验证结果。

React Flow 的坐标、缩放和选择态只存在于浏览器。系统计划只保存节点和语义边，因此将来可由
Web、CLI、SDK 或另一个布局器共同读取。Workbench 可以按 plan 在浏览器本地保存拖动坐标，
但这只是可丢弃的 presentation metadata，不进入计划 revision。

## 本地 API

查询：

- `GET /api/orchestration/session`
- `GET /api/orchestration/plans`
- `GET /api/orchestration/plans/{plan_id}`
- `GET /api/orchestration/plans/{plan_id}/history`
- `GET /api/orchestration/plans/{plan_id}/assessment`
- `GET /api/orchestration/plans/{plan_id}/artifacts`
- `GET /api/orchestration/plans/{plan_id}/artifacts/{artifact_id}`

写命令：

- `POST /api/orchestration/plans`
- `POST /api/orchestration/plans/{plan_id}/messages`
- `POST /api/orchestration/plans/{plan_id}/proposals/apply`
- `POST /api/orchestration/plans/{plan_id}/proposals/reject`
- `POST /api/orchestration/plans/{plan_id}/nodes`
- `PATCH|DELETE /api/orchestration/plans/{plan_id}/nodes/{node_id}`
- `POST /api/orchestration/plans/{plan_id}/nodes/{node_id}/split`
- `POST /api/orchestration/plans/{plan_id}/edges`
- `PATCH|DELETE /api/orchestration/plans/{plan_id}/edges/{edge_id}`
- `POST /api/orchestration/plans/{plan_id}/revisions/{revision}/derive`
- `POST /api/orchestration/plans/{plan_id}/archive`
- `POST /api/orchestration/plans/{plan_id}/finalize`

所有写命令要求 `X-JobSlayer-Session`、精确字段和最新 `expected_revision`。过期 revision 返回
`409`；过期身份或错误 token 返回 `403`。服务仅绑定 loopback、无 CORS，并设置 `no-store`、
`nosniff`、`DENY` frame 和拒绝所有内容的 CSP。

规划 provider 启动、超时、非零退出或协议校验失败返回 `502`，且不会创建/追加计划 revision。

规划制品两个 GET 也要求 `X-JobSlayer-Session`。列表只包含 prompt、raw JSONL、stderr 和 final
JSON 四类 `task_plan.agent.*` 制品，并按 `plan_id` 隔离；响应不会返回宿主文件 URI。内容读取
会先验证完整对象大小与 SHA-256，再返回最多 1 MiB 的 UTF-8 文本预览，并明确标记 COMPLETE
或 TRUNCATED。该入口没有修改、删除、HTML 渲染或执行能力。

## 可选 Codex 规划器

默认命令使用离线、确定性的 `LocalPlanningAgent`。只有操作者明确允许外部调用时，才可启动
Codex adapter：

```bash
./jobslayer orchestration-api \
  --identity-session .jobslayer/identity/planner-session.json \
  --identity-key .jobslayer/identity/planner-key.json \
  --planning-agent codex \
  --allow-external-planning-agent \
  --codex-model <explicit-model> \
  --codex-reasoning-effort <explicit-effort>
```

Windows 使用相同参数和 `jobslayer.cmd`。可另外设置 `--codex-binary`、
`--codex-timeout-seconds` 与 `--planning-artifact-root`。reasoning effort 被作为隔离的 Codex CLI
config override 传递；省略时使用模型默认值，不继承 ambient user config。启动 API 不会调用
模型；创建计划或发送讨论消息才产生一次外部调用。省略 opt-in 或显式 model 会失败关闭。

adapter 固定使用 Codex `exec` 的 JSONL、结构化输出 Schema、最终消息文件、ephemeral 会话和
`read-only` sandbox。它不继承 ambient `OPENAI_API_KEY`；认证只能来自显式可用的 `CODEX_HOME`
登录上下文。每次调用登记 prompt、raw events、stderr 和 final output 四类不可变制品，并把成功
调用的 invocation/artifact ID 写入待应用提案。模型输出仍需通过字段边界、枚举、引用和 DAG
校验，之后由用户应用或拒绝。Workbench 顶部的“规划证据”打开只读查看器，可在作出决定前
复核这四类内容、invocation ID、大小和哈希；本地 fixture 没有这些制品，也不会生成假证据。

当前适配器无自动重试，并限制单次超时和 I/O 大小；Codex CLI 接口没有提供本项目可预先强制的
美元/token 上限。2026-08-19 的显式授权 smoke 使用本机 ChatGPT 登录、`codex-cli 0.148.0`、
`gpt-5.6-sol`、`xhigh` 和 300 秒 timeout 成功生成待应用提案：15,254 input、9,402 output
tokens（其中 4,142 reasoning），未应用提案且权威图保持为空。该实测只证明一次受限路径，不把
真实外部调用加入离线自动门禁，也不代替账户侧预算与凭据/保留策略。

小时级工作的通用控制面已另行按
[ADR-0035](adr/0035-provider-neutral-resumable-long-running-control-plane.md) 落实持久 start key、
多维预算、lease、checkpoint 和保守恢复契约。但当前 `CodexPlanningAgent` 仍是同步 CLI 调用，
尚未实现该契约要求的 `start_or_locate`，因此不能把提高 timeout 宣称为跨重启续跑。下一次真实
prompt→任务图验证只验证 pending proposal、制品、DAG 和人工应用边界；adapter 可恢复性单独验收。

## 当前限制与扩展点

- `LocalPlanningAgent` 仍是默认的确定性协议 fixture；Codex adapter 已有一次显式授权的真实
  smoke 证据，但不会默认启用或进入离线测试；
- 本地 store 只支持单进程 writer；远程或多人并发应实现事务 `TaskPlanStore`；
- 当前 proposal 仍按完整候选图应用或拒绝，没有逐项合并与多人冲突解决；
- Vite 是开发工作台，API 尚未提供生产静态资产或远程部署；
- finalized 只表示用户确认了计划 revision，不表示任务已规划、验证、批准或完成。
- 当前没有 plan-to-Workflow IR 编译器；浏览器布局也不会进入未来 IR。
- 当前查看器只发现已有 plan ID 的制品；外部调用在首个 revision 前失败形成的孤立证据仍需
  通过 registry 查询，尚无 operator-wide failure index。
- 长任务控制面已通过本地确定性契约测试，但尚未装配进 orchestration API、后台 scheduler 或
  现有 Codex planning adapter。

Codex adapter 已把上下文与对话归一化为 `TaskPlanProposalDraft`，并保留原始交互制品。后续
provider 仍必须复用同一端口和人工应用边界，不能让 SDK response、模型置信度或聊天文本进入
领域契约。
