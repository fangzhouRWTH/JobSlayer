# JobSlayer

JobSlayer 是一个面向复杂工程项目的 AI 协同开发控制平面。它把 AI 执行器视为可替换的劳动力，把任务状态、权限、验证、证据与完成判定保留在确定性代码中。

仓库已完成 **Phase 0 退出证据** 和 **Phase 1 必要基础架构**。当前已经提供：

- 提供方无关的任务、执行、事件、制品和验证契约；
- 由代码控制的任务状态机；
- 带哈希链校验、partial-write 安全原子发布的追加式 JSONL 审计日志；
- 从固定 commit 创建、检查和安全清理的任务级 Git worktree；
- 按任务路径策略收集并哈希化补丁；
- 按可信命令规则执行、超时终止并生成输出证据的本地 runner；
- 版本化 `ValidationProfile` 和把失败也保留下来的验证引擎；
- 内容寻址、持久清单并在读取时复核哈希的本地制品注册表；
- 基于同一结构化决策卡的人工监督 CLI 和 loopback 极简可视化界面；
- 带事件序列、取消和原始日志证据的 `AgentExecutor`/Codex CLI adapter；
- 串联工作区、Agent、补丁、验证与合并决策卡的应用控制器；
- 统一源码、UI、完整开发验证与安装后 CLI 的 POSIX/Windows 根入口；
- manifest 驱动的跨平台开发初始化、固定校验的项目 Node LTS 和 lockfile 前端依赖；
- 单命令 Windows/Linux 桌面入口：自动检测/初始化依赖、启动 API/UI、验证代理健康状态，并在独立
  WebView2/Qt 窗口关闭时回收后台进程；
- 版本化语义弹性 UI 描述：以区域/关系/旅程/要求和 dirty/planned/stable 状态记录设计意图，后端
  精确选择唯一活动方案，Agent 草稿不能绕过 stable 保护或前端边界；
- 固定版本、离线只读的 UI/UX Pro Max 核心建议器：整树 hash 与白名单执行约束第三方输入，原始输出
  和规范化建议作为绑定活动 SUID 的不可变证据；不会自动调用 Agent、修改页面或成为设计真相；
- 由版本化 task/profile/runbook 驱动的本地真实运行协调器；
- 必须由外部显式授权、且仍服从相同工作树/验证/审查门禁的真实 Codex runbook；
- append-only 运行记录链、确定性补丁重放 adapter 和 run 级监督入口；
- 审批后复核补丁/基线、创建单一提交、只做本地 fast-forward 并保留集成证据的 adapter；
- 只在原转换、内容制品和 Git 后置事实完全一致时补写缺失 decision/integration/cleanup 记录的证据约束恢复器；
- Agent 前持久化 execution intent、严格 outcome 落盘后才允许补写首记录的无重跑执行恢复边界；
- 从版本化定义经真实协调服务重建、并在 Windows/POSIX 校验的 21-run Phase 0 语料；
- SQLite/PostgreSQL 事务控制平面、迁移机制、事务 outbox 和由 `WorkflowKernel` 产生的精确转换缓冲；
- HMAC 签名的本地身份会话、RBAC、审批/执行权限证明和短期 Agent 凭据租约；
- worker 租约、心跳、取消、孤儿回收，以及 Linux bubblewrap 强隔离端口；
- 执行前 token/费用/时间/尝试预算、上下文包版本/哈希/大小门禁和运行中超限取消；
- 只读多运行 Dashboard、持久证据/事件视图、无敏感字段遥测和确定性执行器比较；
- 认证的协作式任务编排：多计划管理、多轮讨论、Agent 候选图差异与应用/拒绝、结构化
  节点和语义边 CRUD、完整度评估、历史派生、归档、追加式 revision 与用户定稿哈希；另提供
  显式 opt-in、结构化输出和原始交互制品绑定的 Codex planning adapter，以及认证、去存储 URI、
  哈希验证的有界规划证据查看器；本地 fixture 仍为默认；
- 聚焦的 TaskManager 主应用：后台统一任务摘要、revision-bound DAG/Backlog/总日志、完整 Agent
  对话、proposal 决定与任务流固化；当前 Web UI 以左缘垂直栏切换首页、Quick Agent、总控、编排和
  执行五个版面，并以较大分层字号和精简首屏摘要形成 Calm Ops；编排页继续保持左 2/3 DAG、右 1/3
  节点详情与 Agent 对话。Quick Agent 通过本机 Codex App Server 显示真实额度窗口及动态模型、
  effort、速度和能力选单，并提供与任务链隔离的只读讨论/受限仓库写入。后台以源包哈希锁定
  BraveNewWorld target，并把 finalized revision
  装配为 hash-chained、Kernel-owned 的节点运行；显式启用的 durable 本机 Codex worker 支持 API
  重启后 start-or-locate、运行级隔离 worktree 和原始证据，默认仍禁用 dispatch，且不伪造完成；
- 内容绑定的本地 dependency attachment：将 operator 提供的 Anygine Git checkout/Conan toolchain
  路径与源控 revision/SHA-256 核对，只向受治理 validation 注入显式环境，并以前后漂移证据
  支持隔离 BraveNewWorld C++ build、CTest 和 GPU smoke；
- 一个可运行的闭环演示和标准库测试；
- 项目指导、架构决策和分阶段路线图。

## 快速开始

需要 Python 3.11 或更高版本。普通使用只需一个跨平台 Python 入口；首次运行会自动检测并初始化
仓库 venv、Node/npm、lockfile UI 依赖和桌面 WebView，然后启动后台与独立 App 窗口：

```powershell
py -3 start.py
```

```bash
python3 start.py
```

关闭桌面窗口会同时停止本次入口拥有的 API/Vite 进程。只读检查使用
`python start.py --check`，无窗口服务模式使用 `python start.py --headless`；Windows 强制 WebView2，
Linux 使用 Qt 且需要 `DISPLAY` 或 `WAYLAND_DISPLAY`。入口自动签发的临时身份包含
`planner + quick-agent + reviewer + approver`，使本机执行页可显示人工 review/approval、反馈与只读
辅助入口；它仍不启用 durable task execution、validation 或 source integration。Quick Agent 和任务
绑定辅助只有用户发送消息才调用本机 Codex，源码 Reviewer/Approver 独立性仍由后端强制。

`python start.py --smoke-test` 只启动并健康检查 API/Vite 后立即清理，不创建窗口，因此 Linux 无图形
会话也可使用。固定的 loopback 健康检查不经过用户配置的 HTTP 代理。

开发、CI 或需要手工高级参数时仍可单独运行 `init.cmd`/`init.sh`。初始化不会安装系统软件或修改
持久 `PATH`；没有全局 npm 时可通过
`.\init.cmd -- npm --prefix ui-framework run dev`（POSIX 使用 `sh ./init.sh -- ...`）
运行前端。完整参数与集成协议见[跨平台开发环境初始化](docs/INITIALIZATION.md)。

环境准备后，Windows 与 POSIX 使用
完全相同的公共接口：

```text
jobslayer <子命令> [参数]
python -m jobslayer <子命令> [参数]
```

例如两端均可运行 `jobslayer check` 或 `python -m jobslayer check`。完整门禁也会离线
验证 `ui-framework` 的 TypeScript 和 production build，因此首次运行前必须完成上述初始化。
前者是
安装包生成的 console script，后者是源码/模块入口；两者进入同一个 launcher。
平台脚本只用于尚未激活环境时的 bootstrap。

POSIX（Linux/macOS/WSL）公共命令示例：

```bash
./jobslayer check
./jobslayer validate-testbed testbeds/brave-new-world.json
./jobslayer inspect-testbed testbeds/brave-new-world.json
./jobslayer validate-runbook runbooks/bnw-anygine-small-app-001-codex.json
./jobslayer validate-ui-design ui-designs/catalog.json
./jobslayer inspect-ui-design ui-designs/catalog.json --page-id task-manager
./jobslayer inspect-readiness --state-root .jobslayer --required-reviewed-tasks 20
./jobslayer build-phase0-corpus
./jobslayer inspect-readiness --state-root .jobslayer/phase0-corpus/state --required-reviewed-tasks 20
./jobslayer inspect-recovery .jobslayer/runs/RUN_ID
./jobslayer demo --journal .jobslayer/demo.jsonl
./jobslayer create-local-identity-key .jobslayer/identity/key.json
./jobslayer issue-local-identity-session \
  --key .jobslayer/identity/key.json \
  --subject-id local-reviewer --display-name "Local reviewer" \
  --role reviewer --output .jobslayer/identity/reviewer.json
./jobslayer review-decision examples/decision-card.example.json \
  --identity-session .jobslayer/identity/reviewer.json \
  --identity-key .jobslayer/identity/key.json \
  --output .jobslayer/example-decision.json
./jobslayer ui examples/decision-card.example.json \
  --identity-session .jobslayer/identity/reviewer.json \
  --identity-key .jobslayer/identity/key.json \
  --output .jobslayer/example-visual-decision.json \
  --open-browser
```

TaskManager 桌面应用的推荐入口是：

```bash
python3 start.py
```

Windows 使用 `py -3 start.py`。入口在独立原生窗口加载首页；左侧垂直栏切换五个版面，任务编排页
仍以左 2/3 任务图、右 1/3 节点详情与 Agent 对话为核心。手工身份、Codex opt-in 和高级接口说明见
[TaskManager 聚焦应用](docs/TASK_MANAGER.md)。

Windows PowerShell 使用原生 Python，不要求 WSL：

```powershell
.\jobslayer.cmd check
.\jobslayer.cmd validate-testbed testbeds/brave-new-world.json
.\jobslayer.cmd serve-dashboard `
  --state-root .jobslayer/phase0-corpus/state `
  --identity-session .jobslayer/identity/observer.json `
  --identity-key .jobslayer/identity/key.json `
  --open-browser
```

下文的 `./jobslayer` 在 Windows 上均对应 `.\jobslayer.cmd`。JobSlayer
控制平面和完整开发门禁可原生运行。BraveNewWorld profile 现在包含 contract、C++ build/CTest
和 runtime smoke；实际运行前必须用 TaskManager 部置参数绑定对应平台的 Anygine source/toolchain
和所需图形会话环境，缺失时目标失败关闭。profile 显式登记 POSIX `./bnw` 与 Windows
`.\bnw.cmd` 两套 argv；生成型 Conan toolchain 在 run 装配时按主机平台捕获哈希并持续检查漂移。

演示会依次经过：

```text
Draft -> Planned -> Implementing -> Verifying -> Reviewing
      -> MergeReview
```

它只演示到 `MergeReview` 的控制平面，不会伪造集成证据、调用真实模型、修改外部仓库或合并代码。完整本地成功路径见下方体验手册。

## 文档入口

- [项目开发指导](docs/PROJECT_GUIDE.md)
- [交互设计与前后端协作指南](docs/INTERACTION_DESIGN_GUIDE.md)
- [语义弹性 UI 描述框架](docs/SEMANTIC_UI_DESIGN.md)
- [外部 UI/UX 建议接入](docs/UI_ADVICE.md)
- [协作式任务编排](docs/TASK_ORCHESTRATION.md)
- [TaskManager 聚焦应用](docs/TASK_MANAGER.md)
- [长任务执行与恢复](docs/LONG_RUNNING_EXECUTION.md)
- [Workbench Stage 0 交互原型](ui-framework/README.md)
- [跨平台开发环境初始化](docs/INITIALIZATION.md)
- [初步实施路线图](docs/ROADMAP.md)
- [短期基础设施开发计划](docs/SHORT_TERM_INFRASTRUCTURE_PLAN.md)
- [开发决策与落实日志](docs/DEVELOPMENT_LOG.md)
- [架构决策索引](docs/adr/README.md)
- [人工监督 CLI 使用说明](docs/HUMAN_SUPERVISION.md)
- [极简可视化审查界面](docs/VISUAL_REVIEW_UI.md)
- [统一源码与正式程序入口](docs/UNIFIED_ENTRYPOINT.md)
- [Codex CLI 集成说明](docs/CODEX_INTEGRATION.md)
- [受治理执行闭环](docs/GOVERNED_EXECUTION_LOOP.md)
- [Phase 0 初步框架总说明](docs/PHASE0_FRAMEWORK.md)
- [Phase 1 首次真实 Codex 与滤波主题实施说明](docs/PHASE1_FILTER_CODEX_RUN.md)
- [最小开发闭环与体验测试手册](docs/MINIMUM_DEVELOPMENT_LOOP.md)
- [BraveNewWorld 实验项目方案](docs/testbeds/BRAVE_NEW_WORLD.md)
- [示例任务](examples/task.example.json)
- [示例决策卡](examples/decision-card.example.json)

## 当前开发焦点与实验项目

中期主线进一步收紧为 TaskManager 单产品闭环：精简图状任务 UI、将当前逐按钮推进收束为一次只运行
一个节点的可恢复串行协调器，并把 Agent、命令、验证、审查和人工门禁反馈统一投影回 DAG。工作台其余
能力保留为历史/高级入口，不进入当前退出条件。

当计划或 run 到达 proposal、固化、装配、review、checkpoint、最终门、失败或阻塞等交互点时，
TaskManager 会在任务图/详情/执行反馈中给出绑定当前 revision 的角色要求、详细步骤、待审证据、允许
决定及禁止动作。它只解释现有治理路径，不替代 RBAC、`WorkflowKernel` 或人工批准。

BraveNewWorld 已重置为基于 Anygine 公共 build-tree contract 的小 App 测试床。固定基线为
`e7bff4aceca5dee998d0db1dc1c50e4b935fabda` / `bnw-anygine-0`，已发布到远端；旧网页端机电模拟内容
不再属于当前主干目标。登记与验证入口见 [`testbeds/brave-new-world.json`](testbeds/brave-new-world.json)。

```bash
git clone https://github.com/fangzhouRWTH/BraveNewWorld.git
```

BraveNewWorld 不属于 JobSlayer 控制平面的源码；JobSlayer 只保存项目登记、任务规格、运行记录和验证
证据。Anygine 仍是独立引擎库，BraveNewWorld 只消费固定 public targets，不复制或修改引擎源码。

## 当前边界

当前具备本地认证、事务控制平面和项目 Dashboard，但仍不是远程多用户生产平台。PostgreSQL adapter 已由真实 PostgreSQL 16 合同测试覆盖；SQLite 是无需外部服务的开发后端。Linux worker 可使用 bubblewrap 的默认拒绝式网络、挂载和资源隔离；原生 Windows 没有被冒充成等价强沙箱，要求强隔离的任务会失败关闭，可由同一接口路由到 WSL/Linux worker。

尚未实现的能力包括远程对象存储、真实短期模型凭据下发 adapter、第二个真实模型执行器、自动修复编排、Dagger、Temporal、Ray、Kubernetes、push 和部署。决定应用、集成与清理都要求签名身份和各自权限；集成仍只允许在证据完全匹配时执行本地 fast-forward。完整阶段状态以[短期基础设施计划](docs/SHORT_TERM_INFRASTRUCTURE_PLAN.md)和[路线图](docs/ROADMAP.md)为准。
