# JobSlayer 交互设计与前后端协作指南

## 1. 文档定位

本文把《AI Collaboration Platform Interaction Framework Design Guide》转化为 JobSlayer 的项目级交互设计和开发规则。外部文章提供 Web-first 工作台、结构化执行反馈、制品审查和成熟前端组件复用等设计方向；本文结合本仓库已存在的控制平面、治理不变量和阶段退出条件，决定哪些建议现在采用、如何采用，以及哪些能力继续后置。

规则优先级为：

1. `AGENTS.md` 的产品不变量与开发规则；
2. 已接受的 `docs/adr/` 架构决策；
3. 本指南；
4. 页面原型与组件实现细节。

原型不能通过视觉暗示改变更高层规则。模型、Agent 和浏览器可以提出动作、生成展示状态与制品，但 JobSlayer 的确定性代码始终拥有工作流状态、权限、重试政策、验证要求、审计与完成判定。

## 2. 产品交互定义

JobSlayer 的图形界面应是一个工程工作台，而不是聊天窗口或独立 CRUD 页面集合。主要心智模型是：

```text
工程工作台 = 工作流编辑器 + 运行检查器 + 验证/审查界面
           + 制品浏览器 + 可观测性控制台
```

Web 是首要人机界面，但不是唯一客户端：

```text
Web Workbench          CLI                 SDK / API
human interaction     automation / CI     integration
       \                 |                 /
        +----------------+----------------+
                         |
              Control Plane contracts
                         |
       JobSlayer-owned workflow / evidence truth
```

Web、CLI 与 SDK 应使用同一领域对象和应用服务语义。不能为了图形界面另建一套状态、权限或完成规则。

## 3. 首要使用循环与信息架构

早期产品重点只有一条连续循环：

```text
Author workflow
      ↓
Request execution
      ↓
Observe structured progress
      ↓
Inspect deterministic validation
      ↓
Review artifacts and provenance
      ↓
Submit an authorized decision intent
      ↓
Control Plane decides the transition
```

推荐一级模块：

| 模块 | 核心问题 | 当前优先级 |
|---|---|---|
| Overview | 当前有哪些需要注意的项目、运行和人工门？ | 次要，保持简洁 |
| Task Orchestration | 用户目标如何通过讨论变成可确认、可修订的任务拓扑？ | 最高，初步纵向切片 |
| Workflow Studio | 要执行的 canonical Workflow IR 是什么？ | 最高 |
| Runs / Run Inspector | 正在发生什么、阻塞在哪里、为什么重试？ | 最高 |
| Artifacts / Review | 产生了什么、如何验证、由谁批准？ | 最高 |
| Agents | 能力、模型、工具授权和性能是什么？ | 后续管理面 |
| Workers / Resources | 执行节点健康、能力和租约是什么？ | 后续管理面 |
| Observability | 成功、失败、成本、延迟和人工干预有什么模式？ | 逐步增强 |
| Settings | 平台配置和权限管理 | 远程产品化时设计 |

总览页是索引和态势入口，不承担深层配置。执行前上下文先在 Task Orchestration 中通过
讨论与 revision 固化；只有单独的受治理编译/创建动作才能把 finalized plan 转换为
Workflow IR 或 `TaskSpec`。执行上下文继续在 Workflow Studio、Run Inspector 和
Artifact Review 之间传递。

## 4. 不可破坏的界面边界

### 4.1 GUI 只控制 Control Plane

禁止：

```text
React component -> shell / Git / Codex / Python process
React state     -> task.state mutation
React Flow JSON -> agent runtime
```

要求：

```text
UI command intent
      ↓
authenticated application API
      ↓
permission + freshness + budget + validation checks
      ↓
WorkflowKernel.transition
      ↓
append-only audit / transactional truth / outbox event
```

界面按钮表达命令意图，不表达命令已成功。发出请求后至少区分 `requested`、`accepted`、`applied`、`rejected`；只有服务端返回并能关联权威记录时，才展示工作流已改变。

### 4.2 图编辑器不是 Workflow IR

React Flow 节点位置、选中态、缩放与折叠属于 presentation metadata。领域定义必须保持提供方无关：

```text
React Flow view model
       ↕ adapter
UI workflow model
       ↕ serializer / validation
Canonical Workflow IR
       ↓
JobSlayer validator / compiler / Kernel-owned execution
```

同一 IR 应能被图形界面、YAML/JSON、CLI、SDK、模板或受治理的 AI 提案读取。外部库对象不得出现在 `jobslayer.domain`。

### 4.3 UI 不能判定完成

页面必须明确区分：

- Agent invocation completed；
- artifact produced；
- deterministic verification passed；
- authorized approval recorded/applied；
- integration evidence produced；
- task completed by Kernel transition。

人工批准不是 `Completed`。未通过验证或没有授权 actor 时，界面不能开放会暗示完成的主操作。

### 4.4 原始日志不是执行模型

原始 stdout/stderr 继续作为制品保留，但界面默认展示归一化层级：

```text
Run
├─ Task
│  ├─ AgentInvocation
│  │  ├─ model events
│  │  ├─ tool invocations
│  │  └─ raw-log artifacts
│  ├─ Validation
│  └─ Retry attempt
├─ HumanReview
└─ Final artifacts / integration evidence
```

终端是证据查看器，不是浏览器直接执行命令的入口。

## 5. 共享前端契约意图

本节是 UI/backend 协作草案，不是当前已经存在的生产 API。

### 5.1 稳定对象

前端至少理解以下提供方无关对象：

```text
Project, Workflow, WorkflowVersion,
Run, Task, AgentInvocation, ToolInvocation, Validation,
Agent, Worker, Artifact, Event, Metric,
HumanReview, Policy, ApprovalAuthority
```

列表用轻量 read model；详情可扩展，但标识符、版本、时间、状态和完整性字段必须稳定。不能把某个模型 SDK 的 response 对象直接传给 UI。

### 5.2 统一事件包络

建议事件投影：

```json
{
  "event_id": "evt_10205",
  "run_id": "run_ui_028",
  "task_id": "task-implement",
  "agent_id": "coder-02",
  "timestamp": "2026-08-12T14:04:23.114Z",
  "type": "validation.started",
  "sequence": 18,
  "payload": {},
  "raw_log_artifact_id": "artifact_raw_092"
}
```

最低事件类别：

```text
run.started / completed / failed
task.created / started / completed / failed
agent.started / progress / message / completed
tool.called / completed / failed
validation.started / completed
artifact.created / updated
human.approval_requested / approved / rejected
worker.assigned / disconnected
```

事件顺序必须可重放；未知事件类型应保留、标识为 unsupported，而不是静默丢弃。原始提供方日志以制品关联，归一化事件不能覆盖原始证据。

### 5.3 Query 与 Command 分离

概念接口：

```text
GET  /projects/{id}/workflows
GET  /workflows/{id}/versions/{version}
GET  /runs/{id}
GET  /runs/{id}/tasks
GET  /runs/{id}/events?after_sequence=...
GET  /runs/{id}/artifacts
GET  /artifacts/{id}/metadata
WS   /events?run_id=...

POST /workflows/{id}/run-requests
POST /tasks/{id}/retry-requests
POST /reviews/{id}/decision-requests
```

命令体应携带期望版本/状态、幂等键、签名 authority 或相应引用。服务端响应应返回命令 ID、受理状态和关联的权威事件/记录，不允许前端乐观地伪造最终状态。

### 5.4 快照、流与恢复

Run Inspector 先读取带版本的快照，再从 `sequence + 1` 订阅事件。断线后应从最后确认序列恢复；检测到序列缺口时重新取快照。页面刷新或多标签页不能造成重试、审批等写动作被重复提交。

## 6. 关键界面行为

### 6.1 Workbench shell

- 使用 IDE 式导航、工作区、上下文 Inspector 和底部证据面板；
- 支持面包屑、全局搜索、命令面板和键盘导航；
- 模块切换保留 run/task/artifact 上下文；
- 清晰展示环境、数据来源、实时/暂停/离线和只读状态；
- 原型、模拟数据和真实控制面数据必须有持久可见的区别。

### 6.2 Task Orchestration

- 用户输入先形成版本化 plan，不直接创建执行任务；
- 多轮讨论中的 Agent 输出是 `pending_proposal`，图上必须明确区分 proposed/applied；
- 应用前展示节点与语义边的新增、修改和删除；拒绝 proposal 不改变 applied graph；
- 用户应用/拒绝 proposal、节点/边 CRUD、支线、子任务、归档或历史派生时必须携带 `expected_revision`；
- 节点用结构化字段表达验收标准、交付物、约束、风险、验证要求和人工决策点；
- 完整度面板区分 blocker/warning/info；blocker 阻止 plan finalization，但不冒充执行验证；
- finalization 记录 actor、revision、时间和 hash，只表示确认了计划设计；
- 定稿后的修改创建新 draft revision，不覆盖之前的 finalized revision；
- 历史比较和派生只追加新 draft；归档/恢复也不删除或覆盖 revision；
- React Flow 坐标属于 presentation metadata，可按 plan 保存于浏览器，但系统只保存 provider-neutral 节点和语义边；
- Codex 和其他模型必须在 `PlanningAgent` adapter 后接入，返回非权威 proposal draft；provider
  失败或原始制品不能改变用户应用边界。当前 Codex adapter 需要显式 opt-in/model，并把成功
  invocation 的 prompt、JSONL、stderr 与 final JSON 绑定到待应用提案。

### 6.3 Workflow Studio

- 图与 canonical IR 是同一模型的两个视图；
- 节点显示类型、执行状态、验证、owner 和风险，而不只显示 Agent 名称；
- validation 和 human gate 是显式节点；
- 图形错误和 IR schema 错误在提交前显示；
- 修改重试、权限、超时等治理字段时说明其权限来源，不能由浏览器自行生效；
- 运行按钮先形成可审查的 execution intent。

### 6.4 Run Inspector

必须快速回答：

- 哪个任务正在运行、等待或失败？
- 哪个 Agent/worker 负责，租约和预算如何？
- 当前工具/验证是什么？
- 为什么发生重试，新的 attempt 与之前有什么差异？
- 哪些制品和原始日志可供查看？
- 花费了多少时间、token 和费用？

默认使用执行层级、事件表和 trace waterfall。无限文本流只作为次级终端视图。

### 6.5 Artifact Viewer 与人工审查

制品是一级对象，至少显示：

- ID、类型、版本、大小、内容哈希；
- producer、相关 workflow/run/task；
- 验证状态和使用的 profile；
- Markdown/文本/JSON/Diff 预览；
- 原始制品下载或导出入口；
- 多版本比较和血缘。

人工审查把摘要、Diff、制品、验证、风险、来源上下文和 actor authority 放在一个页面。决定选项是结构化命令；rationale 不能代替选择、权限或验证。

### 6.6 Observability

采用分布式系统的 trace、metric、log、event 概念，同时增加 artifact、decision、validation、cost、token 与 human intervention。仪表板只消费经过完整性校验的 read model；稳定遥测字段不包含 prompt、凭据和原始日志。

通知只覆盖可行动事件，例如人工审批、验证失败、worker 断线和预算触顶。低层事件留在 Run Inspector。

## 7. 视觉、响应式与无障碍基线

- 默认是高信息密度的工程界面，但每个面板只有一个清晰职责；
- 状态不能只靠颜色，必须同时有文本、图标或形状；
- 所有主交互可使用键盘，焦点样式明显，命令面板支持 `Ctrl/Cmd+K`；
- 小屏不缩成不可读的桌面图，应把三栏顺序重排为当前任务/制品、导航、上下文；
- 支持 `prefers-reduced-motion`，动态执行状态不能造成持续大面积动画；
- 表格、图、Diff 和终端必须提供可理解的标题/标签；
- 模拟、缓存、实时、断线和错误状态使用固定文案规范，不依赖装饰性视觉推断。

推荐状态文案：

| 场景 | 展示 |
|---|---|
| 固定样例 | `PROTOTYPE · MOCK DATA` |
| 快照读取 | `SNAPSHOT · as of <time>` |
| 事件订阅正常 | `LIVE · sequence <n>` |
| 用户暂停渲染 | `LIVE PAUSED · events buffered` |
| 连接断开 | `OFFLINE · retrying` |
| 完整性失败 | `INVALID EVIDENCE · excluded from aggregate` |

## 8. 外部库采用矩阵

通用交互复用成熟库，JobSlayer 自己拥有平台语义：

| 能力 | 选择 | 当前状态 | 边界 |
|---|---|---|---|
| Web 基础 | React + TypeScript + Vite | Stage 0 原型采用 | 不进入 Python domain |
| Workflow graph | React Flow (`@xyflow/react`) | 原型采用 | 只处理 view model |
| Code / IR / Diff | Monaco | 原型采用 | 不拥有 IR schema |
| Terminal rendering | xterm.js | 原型采用，只读样例 | 不直接启动进程 |
| Charts | Apache ECharts（按需组件） | 原型采用 | 只消费 read model |
| Markdown | `react-markdown` | 原型采用 | 内容按不可信输入处理 |
| PDF | PDF.js | 有真实 PDF 示例时引入 | 当前不提前增加依赖 |
| Desktop | Tauri 2 | 本地原生集成需求成立后引入 | 不能绕过 Control Plane |
| Panel docking | 待验证 | 用户任务证明需要后选择 | 不提前锁定布局框架 |

依赖安装和升级必须保留 lockfile，并在原型目录运行 TypeScript + production build。新增库需要对应的实际示例或已批准阶段需求。

## 9. 当前代码面与 Stage 0 原型的关系

现有 `src/jobslayer/supervision/ui` 和 `src/jobslayer/management/ui` 是 loopback、认证、最少依赖的真实本地界面，分别服务决定记录与只读 Dashboard；它们有既有安全和部署边界。

`ui-framework/` 是长期交互方向工作台；当前 TaskManager 和高级 Task Orchestration 已接通受限本地
API：

- Workflow Studio、Run Inspector、Artifact Review 与 Observability 使用固定 mock data；
- Task Orchestration 调用 loopback 计划 API，权威 revision 位于 Python store；
- 计划列表、proposal diff、完整度、edge CRUD、revision compare/derive 和 archive 均通过同一应用服务契约；
- Codex 规划证据通过 plan-bound ArtifactQuery 进入只读查看器，浏览器不接收 backing URI；
- 不导入 `src/jobslayer`；
- React 源码不注册 CLI、数据库或事件消费者；API/CLI 位于独立 Python application/adapter；
- 不取代 ADR-0007/ADR-0027 的现有本地界面；
- mock 页面写按钮仍只更新浏览器组件状态；编排写按钮提交有 revision 前置条件的计划命令；
- finalized plan 不直接拥有执行状态，也不能直接驱动 Agent runtime；TaskManager 只在用户显式创建
  plan-bound run 后投影 Kernel 节点历史，未配置 executor 时继续失败关闭。

从原型进入真实接线前必须另立实施任务和 ADR，先定义 provider-neutral application API 与认证/幂等/并发语义，再由 adapter 把现有控制面投影给 UI。不能从 prototype component 反向塑造领域状态机。

## 10. 分阶段实施

### Stage 0：Interaction Prototype（当前）

- React 工作台外壳与总索引；
- Workflow Studio mock graph + canonical IR；
- Run Inspector structured events + trace + terminal artifact；
- Artifact Review Markdown + Diff + metadata + local decision demo；
- Observability mock dashboard；
- 无 Agent、无后端、无状态机修改。

退出证据：前端类型检查/production build 通过，仓库完整 `jobslayer check` 通过，页面可在本地 loopback 预览，设计评审确认核心信息层级。

### Stage 1：Read-only vertical slice

- 定义有版本的 read model；
- 由既有完整性检查后的 run/artifact/event 真相生成快照；
- 建立断线恢复的 event stream；
- 保持所有 command UI 禁用；
- 证明原型不成为第二控制平面。

补充进展：ADR-0031 已为执行前 Task Orchestration 建立一个独立的本地 governed command
slice，使用签名 planner 身份、expected revision、追加历史和 Agent proposal/application
分离；ADR-0032 在同一边界内补齐结构化节点、计划评估、语义边、历史派生与归档。它不替代
本节尚未完成的 run/artifact/event read-only slice，也不把 plan 解释为执行工作流状态。
ADR-0033 进一步接入显式启用的 Codex planning adapter；ADR-0034 已把当前 plan 的四类原始
制品接入认证、去 URI、哈希验证且有界的只读查看器。它仍不是通用 run Artifact Viewer read
model，模型调用和制品内容也不获得工作流命令权限。2026-08-19 的一次
`gpt-5.6-sol/xhigh` 真实 smoke 进一步证明：成功响应只形成 pending proposal，权威图保持为空，
并由确定性 assessment 阻止在提案未处理时定稿。

### Stage 2：Governed command slice

- 只选择一个有明确业务价值的命令（例如 review decision request）；
- 使用签名身份、RBAC、expected state/version、幂等键和 create-only 语义；
- 状态变化仍由应用服务与 `WorkflowKernel.transition` 完成；
- 同时测试允许与拒绝路径、并发和重放。

### Stage 3 及以后

按真实需求增加 worker 管理、远程部署、PDF、桌面壳、持久布局和高级 observability。Tauri、对象存储或远程平台不得由 Stage 0 视觉原型自动触发。

## 11. 设计与开发检查表

提交交互变更前回答：

1. 页面展示的是权威状态、read model 还是本地临时状态？是否可见？
2. 操作是导航、编辑草稿还是控制面命令？是否可能被误认为已应用？
3. 是否把完成、验证、批准和集成错误合并成一个状态？
4. 是否保留稳定 ID、版本、时间、actor、worker、验证和制品血缘？
5. 是否把外部 UI/Agent SDK 对象泄漏进 provider-neutral contract？
6. 是否能从快照和事件序列恢复，而不是靠页面内存猜测？
7. 是否处理离线、完整性失败、权限不足、过期状态与重复提交？
8. 新依赖是否有真实使用点、lockfile 和可重复验证？
9. 是否保持既有 loopback UI、Kernel、审计链和验证门禁不受影响？
10. 是否在 `docs/DEVELOPMENT_LOG.md` 记录决策、变更、命令、结果、限制与下一步？
