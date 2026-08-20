# JobSlayer 开发决策与落实日志

## 记录规则

本文按时间顺序追加开发过程中的决策、落实步骤和验证结果，是项目工作的可读流水账；稳定且影响长期架构的决定另写 ADR，并从这里链接。

每次实质开发至少记录：

- 日期、记录编号和状态；
- 背景与当次目标；
- 做出的决定、理由和替代方案；
- 实际落实步骤及主要文件；
- 执行的验证与结果；
- 已知限制、风险和下一步。

历史条目原则上不原地改写。发现错误时追加“更正/取代”条目，并引用原编号。纯格式修正可以直接进行，但不得借机改变历史决定的含义。

---

## DEV-2026-08-06-01 — 建立 JobSlayer Phase 0 基线

- 状态：完成
- 类型：策划转化、架构与首批实现

### 背景与目标

初始仓库只有 `.git`，没有现有技术栈。输入策划要求构建面向 C++/Python、图形、仿真和训练工程的 AI 协同开发平台，并强调由确定性控制平面治理非确定性 Agent。

### 决策

1. JobSlayer 定位为工程控制平面，而不是多 Agent 自由对话产品。
2. 项目拥有任务、运行、事件、制品、验证和状态模型；外部 Agent/工作流框架通过适配器接入。
3. Phase 0 使用 Python 3.11+ 与 Pydantic 类型契约；暂不引入 PostgreSQL、Temporal、Kubernetes 等基础设施。
4. 任务完成必须由通过的验证报告和有权行为者共同决定。
5. 先用本地 JSONL 追加日志和哈希链验证状态/审计模型；生产级不可变性留待事务存储和外部审计锚点。

长期架构决定见 [ADR-0001](adr/0001-owned-control-plane.md)。

### 落实步骤

1. 建立 `README.md`、`AGENTS.md`、项目指导和 Phase 0～4 路线图。
2. 定义 `TaskSpec`、`AgentRunSpec`、`RunEvent`、`ArtifactManifest`、`VerificationReport` 和 `TransitionRecord`。
3. 实现 `WorkflowKernel` 的合法转换、行为者权限、验证通过/失败门禁。
4. 实现单进程追加式 JSONL 审计日志、序列检查和 SHA-256 哈希链。
5. 提供 `validate-task`、`verify-journal` 和无外部副作用的 `demo` CLI。
6. 添加正向闭环、非法转换、权限拒绝、验证拒绝和篡改检测测试。

### 验证

- `python -m unittest discover -s tests -v`：13 项测试通过（当时结果）。
- 示例 `TaskSpec` 校验通过。
- CLI 演示经过 6 次转换到达 `Completed`，日志完整性检查通过。
- `git diff --check` 通过。

### 限制与下一步

- 日志只在单进程内串行写入，不能抵抗有文件系统权限的完整重写或尾部删除。
- 尚无真实 Git 工作区、受限执行器或外部 Agent。
- 下一步需要外部实验项目和一任务一 worktree。

---

## DEV-2026-08-07-01 — 登记 BraveNewWorld 外部实验项目

- 状态：完成
- 类型：测试床决策、领域契约与文档

### 背景与目标

用户提供空的本地测试仓库 `/home/fangzhou/projects/JobSlayer/TestProjects/BraveNewWorld`，对应 GitHub 仓库 `fangzhouRWTH/BraveNewWorld`，希望它既能验证 JobSlayer，又有控制、机器人与信号处理教学用途。

### 决策

1. BraveNewWorld 是独立外部测试床，不复制进 JobSlayer，也不成为控制平面源码的一部分。
2. 产品采用“可视化基座＋轻量仿真＋UI 实验壳＋Demo 主题包”的逻辑分层。
3. 交互入口和 headless 验证入口必须复用同一仿真内核和轨迹契约。
4. 首批主题按信号滤波、PID、差速机器人、二连杆机械臂递进；暂缓倒立摆、SLAM 和强化学习。
5. 在 JobSlayer 具备受控工作区前，不自动修改或推送 BraveNewWorld。
6. 真正的保留验证和标准答案不提交到 Agent 可访问的公开仓库。

### 落实步骤

1. 新增机器可读登记 `testbeds/brave-new-world.json`，记录 HTTPS、SSH、默认分支和本地提示路径。
2. 新增 `TestbedSpec`、`RepositoryLocation` 和 `TestbedStatus` 提供方无关契约。
3. 新增 `jobslayer validate-testbed` CLI。
4. 编写 BraveNewWorld 双重目标、架构、主题、验证和 BNW-0～3 建设方案。
5. 建立 benchmark 目录边界说明，并更新 README 与路线图。
6. 添加登记契约和固定远程地址的自动化测试。

### 验证

- 实际本地 Git remote：`git@github.com:fangzhouRWTH/BraveNewWorld.git`。
- HTTPS clone：`https://github.com/fangzhouRWTH/BraveNewWorld.git`。
- `jobslayer validate-testbed testbeds/brave-new-world.json` 通过。
- `python -m unittest discover -s tests -v`：16 项测试通过（当时结果）。
- `git diff --check` 通过。

### 限制与下一步

- BraveNewWorld 仍为空仓库、`main` 尚无首个提交，不能作为固定基线运行任务。
- 优先继续 JobSlayer 的 workspace 能力；随后再为 BraveNewWorld 建立 BNW-0 人工基线。

---

## DEV-2026-08-07-02 — 一任务一 Git worktree

- 状态：完成（本地适配器）
- 类型：架构与实现

### 背景与目标

JobSlayer 需要在调用真实编码执行器前保证并发任务不共享可写仓库，并且所有修改都能相对固定基线检查、收集和清理。本次目标是实现本地 Git worktree 管理骨架，不接入 Agent、不访问网络、不修改 BraveNewWorld。

### 初始决策

1. 定义提供方无关的工作区规格、清单、检查和补丁契约。
2. `WorkspaceManager` 使用协议隔离实现；首个适配器使用本地 Git CLI 和参数数组，不通过 shell。
3. 每个工作区从可解析的 commit 建立独立 `jobslayer/<workspace-id>` 分支。
4. 工作区路径只能由经过约束的 `workspace_id` 在配置根目录下推导，调用者不能指定任意删除路径。
5. 默认拒绝清理脏工作区，保留分支用于后续合并提案；不提供强制删除入口。
6. 收集补丁前按 `TaskSpec.allowed_paths` 和 `forbidden_paths` 检查全部变化，禁止路径优先。

详细决定见 [ADR-0002](adr/0002-task-isolated-git-worktrees.md)。

### 落实步骤

1. 在领域层新增 `WorkspaceSpec`、`WorkspaceManifest`、`WorkspaceInspection` 和 `WorkspacePatch`；补丁契约会自行校验 SHA-256。
2. 新增提供方无关的 `WorkspaceManager` 协议，使后续执行器不依赖 Git 实现细节。
3. 在 adapter 层实现 `GitWorktreeManager`：
   - 校验源仓库根目录和独立 workspace root；
   - 将短/长 commit 解析为固定完整 commit；
   - 关闭管理命令的 Git hooks；
   - 创建 `jobslayer/<workspace-id>` 分支和 worktree；
   - 检查登记路径、分支、HEAD、工作树状态和相对基线的变化；
   - 同时收集 tracked 与 untracked 文件的 binary-capable patch；
   - 根据任务允许/禁止路径拒绝越界补丁；
   - 默认拒绝清理脏工作区，干净清理后保留分支。
4. 对 workspace ID 同时实施目录和 Git ref 约束，拒绝路径逃逸、连续点、结尾点和 `.lock`。
5. 添加临时 Git 仓库测试，不依赖网络或 BraveNewWorld，覆盖两个工作区隔离和所有关键拒绝路径。
6. 更新 README、项目指导和路线图，将本地 worktree 标记为已实现、仓库镜像标记为待实现。

### 验证

- `.venv/bin/python -m unittest discover -s tests -v`：26 项测试通过。
- Worktree 专项覆盖：固定基线、并行隔离、tracked/untracked patch、哈希、允许/禁止路径、未知 commit、非法 ID、脏清理拒绝、干净清理和分支保留。
- `.venv/bin/python -m compileall -q src tests` 通过。
- `.venv/bin/jobslayer validate-testbed testbeds/brave-new-world.json` 通过。
- `git diff --check` 通过。
- BraveNewWorld 工作树保持未修改。

### 限制与下一步

- 当前管理器面向本地已有的非 bare 仓库；尚未实现远程 clone/fetch、只读镜像或凭据策略。
- Worktree 不是进程、网络或资源沙箱；不能直接承载不可信 Agent 命令。
- 跨进程分配同一 workspace ID 尚无事务锁；任务分支也尚无自动保留/归档策略。
- 补丁当前在内存中收集，尚未通过 `ArtifactManifest` 注册到制品存储。
- 下一步实现受限本地命令运行器，并让它只接受经过登记的 `WorkspaceManifest`。

---

## DEV-2026-08-07-03 — 受控执行与 CLI 人工监督

- 状态：完成（Phase 0 本地能力）
- 类型：架构与实现

### 背景与目标

在任务级 worktree 已完成后，继续建立确定性验证命令的受控执行能力。用户同时提出人工侧验证和监督需求；本轮需要提供最小但真实可用的交互入口，又不能过早建设依赖数据库和事件服务的完整 Web 前端。

### 初始决策

1. 同时推进执行与监督，因为没有人工门禁的执行器不构成受治理闭环。
2. 先提供结构化 CLI 决策卡；Web UI 等持久状态、制品和事件流稳定后再复用同一契约。
3. 人工工具只产生带卡片哈希的 `HumanDecision`，由控制器验证并通过 `WorkflowKernel` 应用，不能直接修改状态。
4. 本地命令 runner 只接受可信 `CommandPolicy` 中的精确/前缀规则，不执行 shell 字符串，不继承宿主敏感环境。
5. 本地 runner 明确不是强沙箱；本轮不声称已经实现网络、CPU、内存或系统调用隔离。

详细决定见 [ADR-0003](adr/0003-governed-local-command-runner.md) 和 [ADR-0004](adr/0004-cli-first-human-supervision.md)。

### 落实步骤

1. 新增 `CommandRule`、`CommandPolicy`、`CommandRequest`、`CommandResult` 和 `CommandStatus` 契约。
2. 新增提供方无关 `CommandRunner` 协议和 `GovernedLocalCommandRunner`：
   - 通过最长匹配的可信 argv 前缀选择规则；
   - 不使用 shell，拒绝未批准的额外参数和超额 timeout；
   - 验证请求与 `WorkspaceManifest` 的 workspace/task 对应关系；
   - 拒绝不存在、越界或通过 symlink 逃逸的 cwd；
   - 使用最小环境、临时 HOME/TMPDIR，不继承任意宿主变量；
   - 超时或父进程遗留子进程时终止整个进程组；
   - 并行排空 stdout/stderr，限制保留大小，同时统计完整字节数和 SHA-256；
   - 把通过、失败和超时统一为结构化结果。
3. 新增 `DecisionCard`、`DecisionOption`、`EvidenceSummary` 和 `HumanDecision` 契约，强制唯一推荐/默认项。
4. 实现稳定卡片哈希、终端渲染和人工决定生成逻辑。
5. 新增 `jobslayer review-decision`：支持交互选择/理由、显式取消、非交互 adapter 参数和不覆盖写入。
6. 新增示例决策卡和人工监督使用说明，明确决定记录尚未应用到工作流。
7. 更新 README、项目指导、ADR 索引与路线图状态。

### 验证

- `.venv/bin/python -m unittest discover -s tests -v`：41 项测试通过。
- Runner 专项覆盖：精确政策、额外参数拒绝、timeout 上限、进程组终止、遗留子进程清理、输出截断/全量哈希、环境秘密隔离、失败退出码和 cwd symlink 逃逸。
- Supervision 专项覆盖：卡片展示、推荐项约束、卡片哈希绑定、未知选项拒绝、交互决定写入和防覆盖。
- `.venv/bin/jobslayer review-decision examples/decision-card.example.json ...` 人工工具烟雾验证通过。
- `.venv/bin/python -m compileall -q src tests`、配置校验和 `git diff --check` 通过。

### 限制与下一步

- 本地 runner 不提供内核级网络、CPU、内存、磁盘、设备或系统调用隔离，只能运行受信验证命令。
- 当前 API 是同步的；尚无跨进程取消句柄、并发配额或资源调度。
- CLI 的 `actor_id` 是身份声明而非认证；决策 JSON 也尚未签名或注册为制品。
- `HumanDecision` 尚未由控制器核验和映射到 `WorkflowKernel`，因此不会推进任务。
- 下一步定义 `AgentExecutor` 及标准事件/取消语义，并实现决定应用服务；随后才能有边界地接入 Codex CLI。

---

## DEV-2026-08-07-04 — 决定应用与 Codex CLI adapter

- 状态：完成（adapter 合规测试，真实模型运行待门禁）
- 类型：工作流、执行器与官方接口核对

### 背景与目标

上一轮已经能产生人工决定和运行受信验证命令，但决定尚不能安全推进状态，编码 Agent 也没有统一生命周期。本轮目标是闭合决定应用规则，定义 `AgentExecutor`，并根据当前 Codex 非交互接口实现第一个 adapter。

### 决策

1. 人工决定必须同时通过卡片哈希、任务/选项/证据绑定、有效授权窗口、决策类型权限和当前状态检查。
2. `MergeReview` 允许授权人工退回 `Repairing`；`PlanReview` 允许退回 `Planned`。离开两个审批状态都要求 human/policy。
3. `AgentExecutor` 采用非阻塞 start、增量事件轮询、显式取消和终态 collect；框架对象不得进入领域层。
4. Codex 使用官方 `codex exec --json` 事件面，保留 raw JSONL/stderr，再映射到项目事件。
5. 权限映射只允许 `read-only`/`workspace-write`，拒绝 `danger-full-access`；模型、权限、schema 都从可信命名映射选择。
6. adapter 不继承 ambient OpenAI/Codex API key；真实调用推迟到固定测试基线、预算批准和凭据隔离完成后。

详细决定见 [ADR-0005](adr/0005-codex-cli-executor-adapter.md)。

### 官方接口核对

1. 按 `openai-docs` 技能获取并阅读当前 Codex Manual 的 Non-interactive mode。
2. 本机核对 `codex-cli 0.142.4` 与 `codex exec --help`。
3. 确认 JSONL 事件、stdin prompt、`--ephemeral`、`--ignore-user-config`、`--ignore-rules`、sandbox 和 output schema 约定。
4. 未调用真实模型、未产生模型费用、未修改 BraveNewWorld。

### 落实步骤

1. 新增 `ApprovalAuthority` 和 `DecisionApplicationService`，将合法人工决定应用到 `WorkflowKernel`。
2. 修改审批状态转换和授权规则，并添加允许/拒绝成对测试。
3. 新增 `AgentInvocation`、run handle/status/result、cancellation result 和 `AgentExecutor` 协议。
4. 实现线程安全 `RunEventBuffer`：自动序列、内容哈希、增量读取、终态封口和完整性校验。
5. 实现 `CodexCliExecutor`：
   - 从 stdin 发送 prompt；
   - 后台消费 stdout JSONL 和 stderr；
   - 归一化 thread/turn/item/error 事件；
   - 保存并哈希原始日志；
   - 支持 timeout、进程组取消、终态收集；
   - 非 JSON 协议输出强制判失败；
   - ambient API key 不进入子进程。
6. 使用可执行假 CLI 覆盖完整、失败、取消和事件轮询，不发起外部请求。
7. 新增 Codex 集成说明并更新项目指导、人工监督说明、ADR 索引和路线图。

### 验证

- `.venv/bin/python -m unittest discover -s tests -v`：57 项测试通过。
- 决定应用专项：授权批准、退回修复、卡片篡改拒绝、授权过期拒绝和验证报告门禁。
- 工作流规则专项：human 可从 `MergeReview` 退回修复，agent 被拒绝。
- 事件专项：序列/哈希、增量读取、终态后写入拒绝和篡改检测。
- Codex adapter 专项：JSONL 映射、raw log 哈希、增量事件、取消、非法 JSON、危险权限拒绝、未知 profile 拒绝和 API key 环境隔离。
- `.venv/bin/python -m compileall -q src tests` 与 `git diff --check` 通过。

### 限制与下一步

- `RunEventBuffer` 和运行表仍在内存，控制器进程重启后不能恢复。
- raw logs 尚未注册为 `ArtifactManifest`，没有保留/访问控制策略。
- 本地已保存 Codex 登录仍依赖 HOME/CODEX_HOME；生产 credential provider 未实现。
- 尚未使用真实 Codex 运行，因此未来 CLI 版本/事件差异需要受控烟雾测试确认。
- 下一步实现应用控制器，把 `TaskSpec -> Workspace -> AgentRun -> Patch -> Verification -> Decision` 串成单一命令；在此之前先为 BraveNewWorld 建立人工 BNW-0 固定基线。

---

## DEV-2026-08-07-05 — 证据制品、验证引擎与应用控制器闭环

- 状态：完成（假 Agent 端到端；真实模型/测试床待门禁）
- 类型：应用编排、验证、制品与人工监督

### 背景与目标

前四轮已分别实现工作流、工作区、受控命令、人工决定和 Codex adapter，但仍需调用者手工拼接。此轮目标是让 JobSlayer 自己决定每一步何时发生、需要哪些证据以及失败落入哪个状态，同时继续避免真实模型费用、远端写入和对 BraveNewWorld 的提前修改。

### 决策

1. 用 `TaskExecutionController` 编排一次实现尝试，所有状态变化仍只通过 `WorkflowKernel.transition`。
2. Phase 0 只支持从 `Draft` 开始且 `max_attempts=1`；不静默实现未定义的自动重试。
3. 进入实现必须携带绑定 task、human/policy 行为者、风险上限和有效窗口的 `TaskExecutionAuthorization`。
4. 引入提供方无关 `ArtifactRegistry`；本地 adapter 使用 SHA-256 内容寻址对象和独立持久清单，不覆盖对象，读取时复核 URI、大小和哈希。
5. `ValidationProfile` 在建模时确认每项检查命中可信命令政策并服从 timeout；验证失败、超时和命令拒绝都必须生成证据。
6. 验证通过只进入 `Reviewing`；审查接受并引用本次验证报告后才生成合并决策卡。控制器停止于 `MergeReview`，不自动提交、推送、合并、部署或完成任务。
7. Agent 失败、日志哈希不一致、空补丁和路径政策拒绝均进入 `Failed` 并保留失败制品；必需验证失败进入 `Repairing`。

详细决定见 [ADR-0006](adr/0006-evidence-backed-application-controller.md)。

### 落实步骤

1. 新增 `ValidationCheckSpec`、`ValidationProfile`、执行授权、审查报告、执行结果、审查处置和合并审查包契约。
2. 将命令与工作区 adapter 的基础异常提升到提供方无关协议层，允许控制器统一处理而不依赖具体 adapter 类。
3. 实现 `LocalArtifactRegistry`：
   - 对象按内容 SHA-256 分目录保存；
   - 相同内容去重但每次登记保留独立 artifact ID；
   - 清单独立持久化并可按 ID 加载；
   - 读取时验证 URI、内容大小与 SHA-256；
   - 文件以只读权限落盘，adapter 不提供覆盖入口。
4. 实现 `VerificationEngine`，运行可信 profile 并为每项检查登记完整 `CommandResult` 或结构化拒绝证据；报告绑定 workspace 基线和 patch 哈希。
5. 实现 `TaskExecutionController.execute_implementation`，串联授权、worktree、Agent 生命周期、raw log 登记、补丁路径政策、验证和状态路由。
6. 实现 `prepare_merge_review`，将审查要求修改路由到 `Repairing`，或将接受报告、补丁及验证证据组合成等待人工决定的卡片。
7. 用同步假 Agent、临时 Git 仓库和真实本地 Python 验证命令建立端到端测试；再使用现有 `DecisionApplicationService` 验证最终 `Completed` 只能来自授权人工决定。
8. 新增受治理闭环说明，更新 README、项目指导、人工监督说明、ADR 索引和路线图。

### 验证

- `.venv/bin/python -m unittest discover -s tests -v`：69 项测试通过。
- 新增专项覆盖：内容去重/清单加载/篡改发现、验证通过/命令拒绝证据、验证配置越界命令/超时拒绝。
- 应用闭环覆盖：假 Agent 成功至授权人工完成、验证失败进入修复、Agent 失败进入失败、审查退回修复、超风险授权在任何状态变化前拒绝。
- `.venv/bin/python -m compileall -q src tests` 与 `.venv/bin/pip check` 通过。
- `.venv/bin/jobslayer validate-testbed testbeds/brave-new-world.json` 通过；登记的 HTTPS 地址为 `https://github.com/fangzhouRWTH/BraveNewWorld.git`。
- `git diff --check` 和新增/现有文本尾随空白扫描通过。
- BraveNewWorld 本地仓库保持空工作树、`main` 分支且尚无 `HEAD`；实际 `origin` 为等价 SSH 地址 `git@github.com:fangzhouRWTH/BraveNewWorld.git`。
- 未调用真实 Codex、未产生模型费用、未推送或合并任何仓库。

### 限制与下一步

- 控制器和执行器运行表仍是单进程内存状态，进程重启不能恢复中途任务。
- 本地制品库不是对抗主机管理员的不可变存储，尚无事务索引、RBAC、保留策略或垃圾回收。
- 当前控制器没有任务 JSON/CLI 装配入口、自动修复重试和真实身份 adapter。
- 本地命令 runner 与 Codex workspace-write 都不构成生产级外层沙箱。
- BraveNewWorld 仍未修改；下一步应由人工建立 BNW-0 固定基线，再固化 headless 验证 profile 和无网络外层隔离，最后运行一个低风险真实 Codex 任务。

---

## DEV-2026-08-07-06 — Loopback 极简可视化人工审查

- 状态：完成（本地单决策界面）
- 类型：可视化交互、人工监督与安全边界

### 背景与目标

用户要求及时接入可视化交互，同时保持极简、只反映真实功能并便于模块化调试。现有 CLI 能生成决定，但证据、状态和选项增多后不利于人工快速核对；另一方面，Phase 0 尚无认证身份、数据库和远程访问控制，不能把本地能力包装成虚假的完整 Web 平台。

### 决策

1. 实现 loopback 单卡片审查页，不建设多项目仪表盘或聊天界面。
2. 不增加 Web/前端框架依赖；使用 Python 标准库 HTTP 和原生 HTML/CSS/JavaScript。
3. 页面只展示严格校验后的 `DecisionCard`、可选哈希链审计状态、证据、选项、后果、制品 ID 和能力边界。
4. 页面只生成 create-only `HumanDecision`；不应用决定、不合并 Git、不部署，并明确显示身份未认证。
5. CLI 与 Web 共用提供方无关 `DecisionStore` 和本地 0600、排他创建 adapter，已存在决定时只读且拒绝覆盖。
6. 提供 journal 时，`PlanReview`/`MergeReview` 卡片必须与当前任务状态匹配；过期卡片在 session 和 HTTP 两层拒绝记录。
7. 服务只允许 loopback；POST 要求随机会话令牌、自定义 header、严格字段和请求大小上限，静态资源不访问外网。
8. 页面语义结构、样式、交互脚本、session、HTTP adapter 和存储 adapter 分离。

详细决定见 [ADR-0007](adr/0007-loopback-visual-review-surface.md)。

### 落实步骤

1. 新增 `DecisionStore` 协议和 `LocalDecisionStore`，让 CLI/Web 复用同一不可覆盖决定记录路径。
2. 新增 `ReviewSession`，集中处理卡片哈希、行为者、证据、已有决定和审计状态绑定。
3. 新增 `ReviewHttpServer`：
   - `GET /api/session` 返回真实卡片、状态和能力；
   - `POST /api/decisions` 记录决定并返回 `recorded_not_applied`；
   - 拒绝非 loopback、错误 token、额外 JSON 字段、重复提交和状态过期卡片；
   - 设置 no-store、CSP、nosniff、DENY frame 等响应头。
4. 新增独立 HTML/CSS/JS：响应式主/侧栏、证据列表、状态时间线、选项后果、理由输入和能力边界；动态数据仅用 `textContent` 渲染。
5. 新增 `jobslayer serve-review`，支持 card、actor、create-only 输出、可选 journal、port 和主动打开浏览器。
6. 将静态资源纳入 setuptools package data。
7. 新增 UI 使用/调试文档并更新 README、人工监督说明、项目指导、路线图和 ADR 索引。

### 验证

- `.venv/bin/python -m unittest discover -s tests -v`：74 项测试通过。
- UI/API 专项覆盖：真实能力与审计状态、纯本地静态资产与 CSP、会话 token、决定记录但不应用、重复记录拒绝、陈旧卡片拒绝和非 loopback 绑定拒绝。
- 原 CLI 决定写入和防覆盖测试继续通过，证明两种入口共用存储后没有回归。
- 手工启动 `serve-review --port 0` 后，通过实际 loopback HTTP 读取 `/api/session`，确认返回卡片、未认证身份、未知状态和不可用能力；随后正常停止服务，未产生决定文件。
- 使用本机 headless Chrome 在 1440×1200 视口完成页面烟雾与视觉检查，确认信息层级、双栏布局、证据哈希、能力边界和选项后果均实际渲染；测试截图仅保存在 `/tmp`，未加入仓库。
- 未修改 BraveNewWorld，未运行真实 Agent，未推送、合并或部署。

### 限制与下一步

- 当前页面一次只审查一张卡，不提供任务列表、搜索、实时运行控制台、diff 内容或制品下载。
- loopback token 只缓解跨站/误提交，不是身份认证；服务禁止远程绑定。
- journal 每次读取本地 JSONL；没有断线事件流或跨进程状态恢复。
- 决定记录仍需带真实授权调用 `DecisionApplicationService` 才能改变工作流。
- 下一步应先把 BraveNewWorld BNW-0 的真实验证和首个决策卡接入该界面，再用实际使用反馈决定 diff/曲线/仿真证据查看器的最小范围。

---

## DEV-2026-08-07-07 — 固化统一源码、UI、验证与正式程序入口

- 状态：完成（统一 launcher 与开发门禁）
- 类型：开发体验、程序入口与验证治理

### 背景与目标

用户要求将 JobSlayer 交互界面主入口固化成统一脚本，并规定后续开发验证和正式程序都经过该基础入口。此前仓库同时使用 `.venv/bin/jobslayer`、`python -m jobslayer`、直接 unittest 命令和 `serve-review`，存在解释器选择、入口帮助和完成标准逐渐分叉的风险。

### 决策

1. 仓库根目录 `./jobslayer` 成为源码 checkout 唯一推荐入口。
2. 根脚本只选择 Python、bootstrap `src` 并调用公共 launcher，不持有业务逻辑。
3. `./jobslayer`、`python -m jobslayer` 和安装后的 console script 全部进入 `jobslayer.launcher:main`。
4. `ui` 固化为 `serve-review` 的稳定短别名；界面权限和真实能力边界保持不变。
5. `check` 固化为报告开发完成前的唯一验证入口，顺序执行完整 unittest、compileall、pip check、测试床登记和 `git diff --check`。
6. `check` 执行全部步骤后统一返回结果；任一失败使最终退出码非零，不能用部分成功掩盖。
7. 根脚本按 `JOBSLAYER_PYTHON`、仓库 `.venv/bin/python`、当前 Python 的顺序选择解释器；无效显式路径直接失败。
8. `AGENTS.md` 和活跃使用文档统一改用 `./jobslayer`；历史开发日志中的当时命令不重写。

详细决定见 [ADR-0008](adr/0008-unified-source-and-installed-entrypoint.md)。

### 落实步骤与文件

1. 新增根可执行文件 `jobslayer`，并设置 executable bit；修复了比较解释器时不能解析 venv symlink 的细节，否则系统 Python 和 venv 会被误判为同一入口。
2. 新增 `src/jobslayer/launcher.py`，并让 `src/jobslayer/__main__.py` 与 `pyproject.toml` console script 指向它。
3. 在 `src/jobslayer/cli.py` 新增 `ui` alias 与 `check --root`。
4. 新增 `src/jobslayer/development/checks.py`，以结构化步骤和参数数组运行完整验证，不使用 shell 字符串。
5. 新增 `tests/test_development_checks.py`，覆盖 checkout 识别、错误目录拒绝、五步顺序、不中断汇总和失败报告。
6. 新增 `tests/test_unified_entrypoint.py`，覆盖 executable bit、根脚本/模块 UI 帮助一致及安装入口配置。
7. 新增 [统一入口说明](UNIFIED_ENTRYPOINT.md)，更新 README、项目指导、可视化/人工监督说明、路线图、ADR 索引和 `AGENTS.md`。
8. 首次使用 `pip install --no-build-isolation -e .` 时发现当前 venv 未安装 setuptools；标准 `pip install -e .` 使用 PEP 517 隔离构建成功。随后比较根脚本、模块入口和重新生成的 `.venv/bin/jobslayer` 帮助输出，结果完全一致。

### 验证

- 入口/验证编排专项：7 项测试通过，包括无效显式 Python 必须失败且不能静默回退。
- `./jobslayer --help`、`./jobslayer ui --help`、`./jobslayer check --help` 和 `python -m jobslayer --help` 均正常。
- 源码入口、模块入口和重装后的 console script 帮助输出逐字节一致。
- 最终只通过统一入口运行 `./jobslayer check`：5/5 步通过，其中完整 unittest 为 81 项测试通过；compileall、pip check、BraveNewWorld 登记和 `git diff --check` 均通过。

### 限制与下一步

- 根脚本面向当前 POSIX checkout；Windows 使用 `python -m jobslayer` 或安装后 console script，但仍进入相同 launcher。
- `check` 是源码仓库开发命令，正式部署环境不需要携带 tests、Git 或测试床文件。
- `git diff --check` 只覆盖 Git 已识别的 diff；新增文件的文本卫生仍由测试、评审和额外扫描共同保障。
- 下一步开发一律从 `./jobslayer check` 完成门禁；正式功能和后续 BraveNewWorld 任务入口也继续注册到同一 CLI，不再新建平行脚本。

---

## DEV-2026-08-07-08 — BNW-0 真实产品基线与只读测试床检查

- 状态：完成（本地固定，远端未发布）
- 类型：外部测试床、示教 UI、确定性仿真与基线治理

### 背景与决策

用户要求继续开发、及时接入极简真实可视化，并完整记录每次决策和落实。BraveNewWorld 此前为无 HEAD 的空仓库，因此先用人工辅助方式建立不计入 Agent 成绩的 BNW-0，再让 JobSlayer 登记和核对事实。技术选择、数值边界与首轮失败修正记录在 BraveNewWorld 自身 `docs/DEVELOPMENT_LOG.md` 和 ADR-0001；JobSlayer 侧的持久治理决定见 [ADR-0009](adr/0009-fixed-local-testbed-baseline.md)。

### BraveNewWorld 落实

1. 建立纯标准库 Python 3.11+ 包、根入口 `./bnw` 和零运行时依赖的构建配置。
2. 建立严格 `SimulationRequest`、版本化 `Scenario`、`SimulationTrace` 和 `DemoManifest`；所有请求拒绝未知字段、非有限值、非整步时长和超过 5000 步的轨迹。
3. 实现 `τ dy/dt + y = K u` 的零阶保持精确离散内核，输出 schema、引擎/运行时版本、数值指标和规范 JSON SHA-256。
4. 建立 `simulate`、`run-scenario` 无头入口，以及 loopback-only HTTP API。
5. 建立独立 HTML/CSS/JavaScript 极简 UI；参数、曲线、指标和 JSON 导出全部来自同一 Python 内核，没有伪造暂停、单步或动画能力。
6. 首轮检查发现 unittest 子进程未继承 `src/` 搜索路径；修正为只向检查子进程传播显式 `PYTHONPATH`。暂存检查随后发现新文件尾部空白，清理并把 cached diff 检查加入统一入口。
7. 最终 `./bnw check` 为 4/4，16 项测试全部通过；headless Chrome 以 1440×1000 视口验证默认 101 点真实曲线、指标和轨迹哈希。
8. 创建本地根提交 `fb43878c9f0164deef272e55969c0fc134a6d6a3` 和附注标签 `bnw-0`；工作树干净，没有 push。

### JobSlayer 落实

1. 在 `TestbedSpec` 增加可选 `TestbedBaseline`，登记固定 commit、tag、明确发布状态和结构化验证命令。
2. 新增提供方无关 `TestbedInspector` 协议、只读 `LocalGitTestbedInspector` 和结构化 `TestbedInspection`。
3. 新增统一命令 `./jobslayer inspect-testbed`，只核对工作树、HEAD、解引用标签与 origin；不测试、不 fetch、不提交、不推送、不改变工作流。
4. 更新 `testbeds/brave-new-world.json`：BNW-0 commit/tag、`./bnw check` 和 `published: false`。
5. 新增接受干净登记基线以及拒绝脏 checkout、无基线和危险 tag 的测试；更新项目指导、路线图、统一入口和测试床文档。

### 验证、限制与下一步

- 专项测试：13 项模型、登记和本地 Git inspection 测试通过。
- 实际 `./jobslayer inspect-testbed testbeds/brave-new-world.json` 返回 `valid_local_baseline: true`：HEAD/tag/origin 均匹配，工作树干净，`baseline_published: false`。
- 完整 `./jobslayer check` 为 5/5：86 项 unittest、compileall、依赖一致性、BraveNewWorld 登记和 Git diff 检查全部通过。
- 本次没有运行真实 Agent、没有模型费用，没有推送、合并或部署。
- 下一步把 BNW 的 `./bnw check` 映射为 JobSlayer 受治理 validation profile 和任务 JSON，补齐外层无网络隔离，再选择一个低风险 BNW-1 任务形成首个真实执行闭环。

---

## DEV-2026-08-07-09 — Phase 0 初步框架装配与首个真实沙盒运行

- 状态：完成（真实本地运行到 MergeReview；人类决定未记录）
- 类型：运行装配、持久证据、测试床验证、监督 UI

### 目标与完成标准

用户授权持续运行直到初步框架完整，并在最后形成集中说明。此轮把“完整”限定为：版本化输入能够通过统一入口驱动真实外部仓库 worktree，执行器、补丁路径、验证、制品、实现审查和人类决定界面全部接通；进程结束后能够从本地记录恢复和检查。真实模型费用、未授权人类决定、Git 合并和远端发布不属于自动完成范围。

### 决策

1. 用独立 task、validation profile、runbook 和 SHA-256 固定执行输入组成可审计运行，不在 CLI 参数中临时拼接关键政策。
2. 新增 `scripted_patch` adapter，只重放固定补丁来验收框架，不把它描述成模型或计入 Agent 能力。
3. 只有 low-risk、零模型费用的 scripted run 可由 `phase0-local-scripted-policy-v1` 自动签发十五分钟执行授权；runbook loader 同时绑定 testbed/repository/base/profile/task/executor。
4. 运行阶段用另一条 append-only hash chain 保存可恢复快照，但状态仍只取自 `WorkflowKernel` 审计日志。
5. 实现审查者必须显式声明 `agent` 或 `human`；merge 决定仍只由 `HumanDecision` 表达。
6. `run-ui` 只接线真实 run card/journal；`apply-run-decision` 必须收到外部有效 `ApprovalAuthority`，且不负责 Git merge。

详细架构决定见 [ADR-0010](adr/0010-source-controlled-local-run-assembly.md)，集中操作说明见 [PHASE0_FRAMEWORK.md](PHASE0_FRAMEWORK.md)。

### 落实步骤

1. 新增 `LocalTaskRunbook`、`ScriptedPatchConfig` 和 `LocalRunbookLoader`，拒绝 checkout 外路径、哈希漂移和 task/testbed/profile/invocation 错绑。
2. 新增 `ScriptedPatchExecutor`，使用参数数组运行 `git apply --check`/`git apply`，保留 raw JSONL、stderr、事件和内容哈希；错误补丁形成失败结果而不是伪造完成。
3. 新增 `LocalRunLedger`，对 execution、implementation review 和 decision application 阶段做序列、payload hash、previous hash 和 record hash 校验。
4. 新增 `LocalRunCoordinator`，装配测试床 inspection、worktree、executor、制品库、runner、validation engine、controller、审查和决定应用服务。
5. 新增统一命令：`validate-runbook`、`run-task`、`inspect-run`、`review-run`、`run-ui` 和 `apply-run-decision`。
6. `apply-run-decision` 把决定和权限文件登记为不可变制品；跨制品/日志事务仍是 Phase 0 已知限制。
7. 将 runbook 绑定检查加入 `./jobslayer check`，开发完整门禁由五步扩展为六步。
8. 新增 BraveNewWorld `brave-new-world-v1` profile、慢响应任务、runbook 和固定 replay diff。
9. 新增 runbook、scripted executor、run ledger、local coordinator、决定应用和统一入口测试。

### 首个真实沙盒运行

1. `./jobslayer validate-runbook runbooks/bnw-scenario-slow-001.json` 通过，固定输入 diff SHA-256 为 `1aaa600263532446a2890b26a2fc4183b75a817ec6f165420a7c82966ec67068`。
2. `./jobslayer run-task ...` 在 BNW-0 创建 `jobslayer/bnw-scenario-slow-001-ws-01` worktree，只产生 `scenarios/first-order-slow.json`。
3. 新场景无头命令和完整 `./bnw check` 都通过；收集后的规范 workspace patch SHA-256 为 `1eb64b0ad19cd676cfc4447222b95023461302027d158a03003edb74c9854e4d`。
4. `inspect-run` 确认 execution record chain、workflow audit chain 和全部 execution artifacts 有效，状态为 `Reviewing`。
5. 首次调用 `review-run` 暴露 CLI 装配缺陷：`actor_type` 参数被误加到旧 `review-decision` 分支，导致 `NameError`；在任何状态变化前失败。修正函数签名/分发并运行旧监督测试后继续。
6. 以 `agent / codex-framework-reviewer` 身份核对唯一 changed path、场景 JSON、161 点真实轨迹和验证证据，然后接受实现审查；任务进入 `MergeReview` 并生成真实 decision card。
7. `run-ui` 在 1440×1200 headless Chrome 中完成视觉检查，真实显示 low risk、五次状态转换、三项证据、未认证身份和不可应用/合并/部署边界；截图只在 `/tmp`。
8. 没有提交 UI 决定，因此 run 中没有 `decision.json`；没有应用完成状态、Git merge、Brave main 修改、commit、push 或部署。

### 验证、限制与下一步

- 新增专项覆盖：固定 patch 成功/失败/错 executor，runbook 哈希和基线漂移，run ledger 追加/篡改/阶段顺序，本地 run 的执行、错误 actor 拒绝、审查、真实卡片、外部 authority 应用及 run 不可覆盖。
- 完整 `./jobslayer check` 为 6/6：97 项 unittest、compileall、依赖一致性、测试床登记、runbook 全引用绑定和 Git diff 检查全部通过。
- BraveNewWorld `./bnw check` 为 4/4，16 项测试通过；最终 `inspect-testbed` 与 `inspect-run` 分别确认 BNW 主 checkout 干净且固定、真实 run 双链与制品完整。
- 当前 trusted runner 仍没有网络、系统调用、CPU 和内存强隔离；scripted adapter 自身只执行 Git patch，但 BNW 验证命令仍运行在主机上。
- run ledger、workflow journal 和 artifact registry 分别防篡改，但不是一个跨文件事务；生产恢复需要事务数据库。
- 下一步进入具体讨论：BNW-1 主题、BNW-0 发布、真实 Codex 外层沙箱、真实身份/authority provider 和 run UI 最小扩展。

---

## DEV-2026-08-07-10 — 外部显式授权的真实 Codex 与 BNW 滤波闭环

- 状态：完成（真实 Codex 候选到达 MergeReview；等待人类决定）
- 类型：执行器接线、信号处理示教、真实 UI、验证与人工监督

### 目标与范围

用户批准执行下一阶段初步方案。本轮选择最小纵向切片：JobSlayer 仍是开发主题，BraveNewWorld 提供一个完整、可见、可量化的信号滤波任务。完成标准是源控任务能够由真实 Codex 在独立 worktree 实现，经确定性验证、独立实现审查和真实监督 UI 到达 `MergeReview`；自动决定、Git 合并、推送和部署不在授权范围。

### 治理决策

1. 把 `LocalTaskRunbook.executor` 扩展为 discriminator 联合契约，支持 `scripted_patch` 与 `codex_cli`，但不让 provider 对象进入领域层。
2. 真实 Codex 当前只允许 low-risk、`workspace_write`、默认模型 profile、无输出 schema 和 `max_attempts=1`；声明多次尝试会在 runbook 加载时拒绝。
3. 源控 runbook 不能自我授权。`run-task` 对 `codex_cli` 强制要求外部 `--authorized-by`，缺失时在创建 run/worktree 前拒绝；记录为 human 执行授权，但明确它仍是未认证身份声明。
4. Codex binary 和本机登录由运行端控制，不放入 runbook。确定性 replay 继续使用 policy 授权，且拒绝附带 human `authorized_by`，避免混淆权限来源。
5. 真实模型仍经过原有 controller、worktree、路径政策、raw log 制品、验证引擎、运行记录链、工作流审计链、实现审查和决定卡；不创建“模型自报完成”分支。
6. 当前只记录事后 token usage，不宣称执行前成本强制；继续如实暴露 `network_isolation=false`、`resource_isolation=false`。

详细决定见 [ADR-0011](adr/0011-explicitly-authorized-codex-runbooks.md)，集中实施说明见 [PHASE1_FILTER_CODEX_RUN.md](PHASE1_FILTER_CODEX_RUN.md)。

### JobSlayer 落实

1. 新增 `CodexCliConfig` 和 executor 联合验证；Codex runbook 不读取 replay patch，prepared run 的 patch bytes 因而可为空。
2. `LocalRunCoordinator.execute` 新增外部授权参数、Codex binary 预检、按 adapter 构造执行器和与 timeout 相容的单次授权窗口。
3. CLI 的 `run-task` 新增 `--authorized-by`，`validate-runbook` 对无 patch 的真实 executor 明确输出 `patch_sha256: null`。
4. 新增正反测试：绑定 Codex runbook、拒绝未实现的 retry、缺少人类授权时不创建状态目录、使用假 CLI 经过真实 adapter 生命周期并记录 human authorization 与 token usage。
5. 新增 `tasks/bnw-filter-demo-001.json`、`validation-profiles/brave-new-world-filter-v1.json` 和 `runbooks/bnw-filter-demo-001-codex.json`；完整开发门禁增加第二项 runbook 绑定检查，由六步扩展为七步。

定向测试首次误用环境中不存在的裸 `python`，命令以 127 在任何代码执行前失败；改用仓库 `.venv/bin/python` 后全部通过。这一环境差异没有被掩盖或当成产品缺陷。

### 真实 Codex 运行

1. 本机 `codex-cli 0.142.4` 登录状态有效；运行前确认目标 run/workspace 均不存在。
2. 通过用户本轮请求派生的外部声明 `fangzhou-user-request-2026-08-07` 启动 `bnw-filter-demo-001-run-01`，固定基线仍是 `fb43878c9f0164deef272e55969c0fc134a6d6a3 / bnw-0`。
3. Codex 在 `jobslayer/bnw-filter-demo-001-ws-01` 修改 19 个白名单文件：严格 demo 契约、共享 dispatcher、自实现固定 seed LCG、指数低通内核、场景、CLI/API、两个主题的 Canvas UI、测试、README、架构、开发日志和 BNW ADR-0002。
4. 模型沙箱中的首轮 `./bnw check` 为 3/4：新 PRNG 的固定期望序列写错，同时 Codex `workspace-write` 环境禁止创建 loopback socket。模型修正真实序列，把自动 HTTP 测试改为内存驱动同一 handler，并再次完成检查。
5. 场景初始参数产生负 RMS 改善。模型没有把“曲线更平滑”当成功，而是用同一内核扫描候选参数，选择 `noise_amplitude=0.7`、`cutoff_hz=3 Hz`，默认场景改善为 `42.8864%`。
6. 模型还发现旧一阶响应序列化携带新 demo 默认字段，随后按 `demo_id` 收紧输出，保留 BNW-0 外部形状。
7. 终态 workspace patch SHA-256 为 `ec3a3373e8ada492a51f9eaa671f13ce5a2cd8c286d4f97a27da4774a69bd41c`；raw Codex JSONL SHA-256 为 `fd93825c2a214dfe982382ae9b67304a402166bf0dcef018206c0cc046810dbf`。
8. usage 证据为 input `1,184,089`、cached input `1,106,432`、output `21,831`、reasoning output `2,510` tokens。高使用量被记录为后续执行前预算门禁的直接需求，不据此推断费用。

### 独立验证与视觉检查

1. JobSlayer 规定的 `noisy-low-pass` 场景和完整 `./bnw check` 均通过；后者为 4/4、28 项 unittest。
2. 默认场景独立解析为 301 点，轨迹哈希 `52517134680e55ae2b21abddcb323146aa866fe2732a88cc1276bbf476dd7613`，RMS 从 `0.4016438040` 降至 `0.2293931742`。
3. 外层首次 `curl 127.0.0.1` 被当前代理环境转发并返回 502；改用明确 `--noproxy '*'` 后，真实 `/api/demos` 和 `/api/simulate` GET/POST 均通过。这是本地代理选择问题，不是 BNW HTTP 错误。
4. 使用 headless Chrome 和 DevTools 协议真实切换到 `noisy-low-pass`：滤波字段可见、阶跃字段隐藏，状态显示 301 个采样点，三条曲线和六项指标均与 API 一致；截图只保存在 `/tmp`。
5. 独立 Agent 技术审查接受实现，同时把“套件内 HTTP 为内存 handler，外层已补真实 socket/browser 烟雾”和“无外层网络/资源隔离”保留为 findings。
6. run 进入 `MergeReview`，记录数 2、状态转换 5；record chain、workflow audit chain 和制品完整性均有效。
7. `run-ui` 在 1440×1200 视口显示 low risk、真实任务、声明式 human 执行授权、19 文件补丁、验证/补丁/审查证据，以及只能记录决定、不能应用/合并/部署的能力边界。

### 当前状态与限制

- BraveNewWorld 主 checkout 仍干净地停在 `bnw-0`；候选 worktree 必须保持脏状态供人工审查。
- 真实 run 没有 `decision.json`，没有应用 `Completed`，没有提交、Git merge、push 或部署。
- 真实模型运行已被证明，但执行前 token/cost 预算、认证身份、短期凭据、外层 OCI/VM 隔离和有界自动修复仍未实现。
- JobSlayer 本轮源码改动保持未提交，等待总体讨论后确定提交边界。
- 最终 `./jobslayer check` 为 7/7，101 项 unittest 全部通过；两套源控 runbook 绑定、编译、依赖、测试床和 diff 门禁均通过。

---

## DEV-2026-08-07-11 — 证据门禁的本地最小开发闭环

- 状态：完成（临时仓库全路径通过；真实 BNW 候选等待人工体验）
- 类型：工作流语义、源码集成、恢复、统一 CLI、体验手册

### 背景与完成口径

用户要求继续执行，直到能够支持完成最小开发闭环的全量基础操作，再进入一段体验测试。审计发现原实现把 merge review 的 `approve` 直接转换为 `Completed`，但没有 commit 或 merge：状态真相会早于 Git 真相。本轮把完成口径限定为本地单任务成功路径——任务绑定、隔离执行、验证、实现审查、人工决定、权限应用、受控本地集成、完成和 worktree 清理；自动 repair、push/PR、部署和生产隔离不是本轮完成条件。

### 治理决策

1. 新增 `Integrating`，把批准和完成拆开：`MergeReview → Integrating` 仍要求 human/policy 与通过的验证；`Integrating → Completed` 还必须提供同一 patch SHA-256 的 `SourceIntegrationResult`。
2. `apply-run-decision` 只应用权限和决定，不改变 Git；`integrate-run` 必须由操作员另行显式调用；`cleanup-run` 再单独收尾。UI 仍只能 create decision。
3. 只支持本地、干净目标 checkout 上的单提交 fast-forward。目标漂移不自动 rebase/merge，工作区漂移不重新解释为已审核补丁。
4. 恢复校验不能只信 commit message 或 changed paths。adapter 用临时 Git index 把审核 patch 应用到固定 base，并要求提交 tree 完全一致。
5. 集成禁用目标仓库 hooks，不 fetch、push、rebase、force update、创建 merge commit 或部署；清理只移除干净 worktree并保留任务分支。
6. 旧 run、卡片、日志和制品保持不可变。真实 BNW 候选不由本轮代替用户做决定或集成。

详细决定见 [ADR-0012](adr/0012-evidence-gated-local-fast-forward-integration.md)，体验步骤见 [最小开发闭环与体验测试手册](MINIMUM_DEVELOPMENT_LOOP.md)。

### 落实步骤

1. 领域层新增 `TaskState.INTEGRATING` 和提供方无关 `SourceIntegrationResult`；kernel 完成门禁同时核验授权 actor、通过报告、task 绑定、patch SHA 和 integration evidence。
2. `DecisionApplicationService` 的 merge `approve` 改为进入 `Integrating`；新决策卡明确说明只有本地 Git 快进成功后才完成。
3. 新增 `SourceIntegrator` 协议和 `LocalGitIntegrator` adapter；复核 task/workspace/patch 绑定、目标分支、干净状态、base、完整 patch、预期 tree、单提交 parent 和 commit trailer。
4. run ledger 增加 `source_integration` 与 `workspace_cleanup` 两个哈希链阶段；集成结果注册为内容寻址制品，完成转换继续写入 workflow 审计链。
5. `LocalRunCoordinator` 增加 integrate/cleanup 恢复操作；`inspect-run` 增加 `integration`、workspace `present` 和真实的 `source_integration`/`workspace_cleanup` capabilities。
6. 统一入口新增 `integrate-run`、`cleanup-run`；原 `demo` 停在 `MergeReview`，不再用不存在的外部 Git 事实伪造 `Completed`。
7. 极简 UI 对所有 merge review 固定展示当前运行时边界：批准后只进入 `Integrating`，另行显式集成后才可能完成。这样不改写旧卡片制品，也不会让历史措辞掩盖新语义。
8. 更新 README、项目指导、执行闭环、人工监督、统一入口、路线图、Phase 0/1 说明、BraveNewWorld 说明和 ADR 索引。

### 验证与真实状态

- 工作流正反测试覆盖：人工批准允许进入 `Integrating`；缺少 integration result 或 agent 尝试完成均被拒绝且状态不变。
- 本地 Git adapter 覆盖 tracked + 新文件、workspace 漂移、target 漂移、相同路径/提交说明但不同 tree 的替换、成功后的幂等恢复。
- 临时仓库端到端覆盖 execution → review → decision → `Integrating` → commit/fast-forward → `Completed` → cleanup；最终目标内容、commit、集成制品、双哈希链和保留分支一致。
- 最终只通过统一入口运行 `./jobslayer check`：7/7 全部通过，其中完整 unittest 为 107 项；compileall、依赖一致性、测试床登记、两套 runbook 绑定和 Git diff 门禁均通过。
- 重新运行真实 `inspect-run`：`bnw-filter-demo-001-run-01` 仍为 `MergeReview`，2 条 run records、5 次转换、双链和全部制品有效，没有 `decision.json`。
- 重新运行 `inspect-testbed`：BraveNewWorld 主 checkout 仍干净，HEAD/tag 都是 `fb43878c9f0164deef272e55969c0fc134a6d6a3 / bnw-0`；origin 为 `git@github.com:fangzhouRWTH/BraveNewWorld.git`，登记的公共地址保持 `https://github.com/fangzhouRWTH/BraveNewWorld.git`。
- 用真实滤波 run 在 1440×1400 headless Chrome 中复核极简 UI：旧卡片原文保持不变，新“批准后的真实边界”清楚显示 `Integrating`、显式 `integrate-run`、本地 fast-forward 和不 push/deploy；本次只读检查没有提交决定。
- 本轮没有对真实 BNW 创建 commit、合并、清理、push 或部署，也没有代替人类生成决定或 authority。

### 仍然明确未完成

- `Repairing` 已有确定性落点和证据，但尚不能由 coordinator 自动发起下一轮有界修复；体验期先验证成功路径和人工拒绝边界。
- 本地 actor/authority 尚未接入认证身份；生产环境不能依赖声明式字符串。
- 跨 workflow journal、run ledger、artifact registry 和 Git 的完整事务仍未实现；当前只对主要中断点提供可核验恢复。
- 无外层网络/系统调用/CPU/内存强隔离，无执行前 token/cost 强制，无远端 push/PR 与部署能力。

---

## DEV-2026-08-10-01 — 原生 Windows 与 POSIX 跨平台开发基线

- 状态：完成（JobSlayer 控制平面与完整开发门禁已在原生 Windows 验证）
- 类型：开发入口、进程监督、证据字节、Git 集成、制品路径、测试与文档

### 背景与完成口径

用户询问项目是否必须在 WSL 中运行，并要求调整为跨平台兼容。只读审计发现
领域契约、工作流 kernel 和应用控制器没有平台绑定，实际阻点集中在 Unix
shebang、`.venv/bin/python`、`os.killpg`、文本模式 patch stdin、`/dev/null`、
Windows 文件 URI/只读属性和依赖可执行 shebang 的测试夹具。

本轮完成口径是：不改变领域状态、权限或完成门禁，不增加运行时依赖，使
JobSlayer 源码入口、完整 `check`、临时 Git 闭环、本地 runner 和 Codex adapter
生命周期可在原生 Windows 运行，同时保留 POSIX 行为和证据哈希。外部
BraveNewWorld 自身的 `./bnw` 命令不被 JobSlayer 隐式改写。

详细决策见 [ADR-0013](adr/0013-cross-platform-local-control-plane.md)。

### 落实步骤

1. 新增 `jobslayer.cmd`，正确发现 Windows `.venv`/`py`/`python`、尊重
   `JOBSLAYER_PYTHON` 并透传失败退出码；POSIX `jobslayer` 同时识别 Windows
   和 POSIX venv（只选择当前宿主对应的布局），`.gitattributes` 固定 launcher 与源码行尾并保护 `.diff`
   原始字节。
2. 新增 `jobslayer.execution.processes`，集中选择 POSIX session/process group
   或 Windows process group、`CTRL_BREAK`、`taskkill /T /F`；本地 command
   runner 与 Codex adapter 不再直接调用 POSIX-only API。
3. Codex executable 扩展为受控非空 argv 前缀，测试可用
   `sys.executable fake_codex.py`，默认正式命令仍是 `codex`；Windows 所需的
   非敏感系统环境键加入显式允许集。
4. Git integration 用二进制 stdin 重放审核 patch，避免 Windows 文本层注入
   CRLF；所有 Git adapter 用 `os.devnull` 禁用 hooks。固定 replay diff 恢复为
   SHA-256 `1aaa600263532446a2890b26a2fc4183b75a817ec6f165420a7c82966ec67068`。
5. 本地制品 URI 用平台标准转换；POSIX 保留 `0400` 加固，Windows 不再把
   硬链接共享的只读属性用于临时文件，内容地址和读取时哈希仍是完整性真相。
6. 测试夹具不再直接执行 shebang 文件，原始 stdout/hash 断言使用宿主换行；
   Windows 无符号链接权限时只跳过该环境能力测试，其余安全拒绝路径不变。
7. 更新仓库指令、README、统一入口、最小闭环、Codex、项目指南、路线图、
   ADR 索引和本开发日志，明确控制平面跨平台与外部测试床命令平台性的边界。

### Changed files

- `.gitattributes`
- `AGENTS.md`
- `README.md`
- `docs/CODEX_INTEGRATION.md`
- `docs/DEVELOPMENT_LOG.md`
- `docs/MINIMUM_DEVELOPMENT_LOOP.md`
- `docs/PROJECT_GUIDE.md`
- `docs/ROADMAP.md`
- `docs/UNIFIED_ENTRYPOINT.md`
- `docs/adr/0013-cross-platform-local-control-plane.md`
- `docs/adr/README.md`
- `fixtures/patches/bnw-scenario-slow-001.diff`
- `jobslayer`
- `jobslayer.cmd`
- `src/jobslayer/adapters/codex_cli.py`
- `src/jobslayer/adapters/git_workspace.py`
- `src/jobslayer/adapters/local_artifacts.py`
- `src/jobslayer/adapters/local_command.py`
- `src/jobslayer/adapters/local_git_integration.py`
- `src/jobslayer/adapters/scripted_patch.py`
- `src/jobslayer/application/local_run.py`
- `src/jobslayer/execution/processes.py`
- `tests/test_artifacts.py`
- `tests/test_codex_cli.py`
- `tests/test_local_command.py`
- `tests/test_local_run.py`
- `tests/test_processes.py`
- `tests/test_unified_entrypoint.py`

`JobSlayer.code-workspace` 是本轮开始前已存在的用户未跟踪文件，未读取或修改，
不属于上述变更。

### 验证命令与结果

1. `winget install --id Python.Python.3.12 --exact --scope user --silent
   --accept-source-agreements --accept-package-agreements --disable-interactivity`
   成功安装用户级 Python `3.12.10`。
2. 原生 Python 执行 `python -m venv --clear .venv`，随后
   `.venv\Scripts\python.exe -m pip install -e .` 成功；安装
   `jobslayer 0.1.0`、`pydantic 2.13.4` 及其依赖。
3. 首次 `.\jobslayer.cmd check` 为 `5/7`，111 项测试出现 6 failures、14 errors、
   1 skip；失败证据对应 Windows 只读硬链接、CRLF patch stdin、原生换行和
   `.cmd` 退出码，未被当作通过。
4. 定向复测命令
   `.venv\Scripts\python.exe -m unittest tests.test_artifacts
   tests.test_local_command tests.test_local_git_integration tests.test_local_run
   tests.test_verification tests.test_unified_entrypoint -v` 在最后一个文件 URI
   夹具修正前为 30 项中 1 error、1 skip；该错误随后修复。
5. 修复后通过统一 Windows 入口运行 `.\jobslayer.cmd check`：`7/7` 全部通过；
   完整 unittest 为 111 项 `OK`，1 项因 Windows 当前未授予创建目录符号链接
   权限而 skip；compileall、pip check、测试床、两套 runbook 绑定和
   `git diff --check` 均通过。

### 限制与下一步

- 原生 Windows 的进程树终止是本地监督，不是对恶意代码的安全沙箱；网络、
  CPU、内存与系统调用强隔离仍未实现。
- Windows `chmod` 不承担制品不可变保证；拥有文件系统写权限的主体仍能改写
  本地存储，读取时哈希会检测内容变化。
- BraveNewWorld profile 仍登记 `./bnw`。要让真实 BNW runbook 原生 Windows
  执行，下一步应先在测试床提供等价 Windows 入口，再设计显式版本化的平台
  命令选择；不得在 adapter 内静默替换验证规则。
- 建议下一步在 Windows Developer Mode/具备 symlink 权限的 CI 与一个 POSIX
  CI job 中各运行统一 `check`，把当前本地双路径验证固化为持续集成矩阵。

---

## DEV-2026-08-10-02 — Windows/Linux 统一公共接口

- 状态：完成
- 类型：公共 CLI、进程生命周期协议、adapter 注入、测试与架构文档

### 背景与完成口径

用户进一步要求为 Windows 和 Linux 设定通用接口。审计确认 packaging 已生成
跨平台 `jobslayer` console script，`python -m jobslayer` 也已共享同一 launcher；
但文档仍突出平台 bootstrap，进程监督则只是集中函数，adapter 不能通过明确
协议注入其他实现。

本轮完成口径是：对外明确同一 CLI grammar，对内形成提供方无关、可注入、
可测试的进程生命周期协议，同时不把平台字段加入领域模型，不改变工作流、
验证、权限或完成语义。详细决定见
[ADR-0014](adr/0014-unified-cli-and-process-supervisor-interface.md)。

### 落实步骤

1. 把安装后的 `jobslayer <command>` 和源码环境的
   `python -m jobslayer <command>` 定为 Windows/POSIX 公共接口；两个根脚本
   明确降为未激活环境的 bootstrap。
2. `jobslayer.execution` 公开 runtime-checkable `ProcessSupervisor` Protocol、
   `PosixProcessSupervisor`、`WindowsProcessSupervisor` 与
   `native_process_supervisor()`。
3. 原模块级 launch/terminate 函数保留为兼容 facade；native supervisor 继续
   执行 ADR-0013 已验证的 POSIX/Windows 行为。
4. `GovernedLocalCommandRunner` 和 `CodexCliExecutor` 新增可选
   `process_supervisor` 构造参数，默认 native 实现；启动和终止均只经过接口。
5. recording supervisor 测试确认两个 adapter 调用统一 launch/terminate
   契约；当前宿主实现另行通过 runtime Protocol 检查和真实睡眠进程终止测试。

### Changed files

- `README.md`
- `docs/CODEX_INTEGRATION.md`
- `docs/DEVELOPMENT_LOG.md`
- `docs/PROJECT_GUIDE.md`
- `docs/UNIFIED_ENTRYPOINT.md`
- `docs/adr/0014-unified-cli-and-process-supervisor-interface.md`
- `docs/adr/README.md`
- `src/jobslayer/adapters/codex_cli.py`
- `src/jobslayer/adapters/local_command.py`
- `src/jobslayer/execution/__init__.py`
- `src/jobslayer/execution/processes.py`
- `tests/test_codex_cli.py`
- `tests/test_local_command.py`
- `tests/test_processes.py`

### 验证命令、结果与限制

- 定向运行
  `.venv\Scripts\python.exe -m unittest tests.test_processes
  tests.test_local_command tests.test_codex_cli -v`：19 项全部通过，1 项因当前
  Windows 未授予目录 symlink 权限而 skip。
- 通过统一 Windows bootstrap 运行 `.\jobslayer.cmd check`：`7/7` 全部通过；
  完整 unittest 为 112 项 `OK`，1 项因当前 Windows 未授予目录 symlink 权限
  而 skip；compileall、pip check、测试床、两套 runbook 绑定和
  `git diff --check` 均通过。
- 通用接口统一的是 JobSlayer CLI 与已授权进程树生命周期，不负责替换外部
  测试床命令。BraveNewWorld 仍需自身提供 `bnw` 跨平台 console script，之后
  才能把 profile 中的 `./bnw` 升级为两端相同的 `bnw`。
- 下一步建议在 BraveNewWorld 仓库提供 packaging console script `bnw`，并在
  Windows/Linux CI 各验证 `bnw check`，再通过新 ADR 更新固定基线和 profile。

---

## DEV-2026-08-11-01 — 短期基础设施计划与 Phase 0 证据门禁

- 状态：完成（ST-00 门禁实现完成；真实语料积累和人工复盘继续执行）
- 类型：基础设施规划、阶段门禁、只读运行语料检查、CLI 与测试

### 背景与决策

用户要求先把高优先级基础设施写入短期开发计划，再逐项执行。Phase 1 依赖
Phase 0 稳定闭环和至少 20 个内部样例复盘，但此前没有机器可执行的语料门禁；
直接引入 PostgreSQL 或分布式依赖会违反路线图退出条件。

本轮先新增 `docs/SHORT_TERM_INFRASTRUCTURE_PLAN.md`，按 ST-00 至 ST-07 排列
证据语料、恢复、事务存储、身份、沙箱、预算/上下文、管理视图和双执行器评测。
首个实现切片依据 ADR-0015 提供只读 `inspect-readiness`，复用现有 run
inspection，不改变任务状态，也不替代人工阶段确认。

### 当前落实

1. 新增提供方无关 `RunInspector` 和 `Phase0ReadinessEvaluator`。
2. 自动门禁检查 run 双哈希链、制品、不同 task 的审查数量、决定应用后的完成路径和负路径。
3. 新增 `jobslayer inspect-readiness` 公共命令；证据不足返回非零结构化报告。
4. 新增接受完整语料、拒绝空语料、拒绝损坏 run 和拒绝非法阈值的确定性测试。
5. 更新 README、路线图、统一入口、ADR 索引和短期计划链接。

### 验证、限制与下一步

- 本切片变更文件：`README.md`、`docs/DEVELOPMENT_LOG.md`、
  `docs/ROADMAP.md`、`docs/SHORT_TERM_INFRASTRUCTURE_PLAN.md`、
  `docs/UNIFIED_ENTRYPOINT.md`、`docs/adr/README.md`、
  `docs/adr/0015-evidence-backed-phase0-readiness-gate.md`、
  `src/jobslayer/application/__init__.py`、
  `src/jobslayer/application/readiness.py`、`src/jobslayer/cli.py`、
  `tests/test_readiness.py`、`tests/test_unified_entrypoint.py`。
- 定向命令 `.venv\Scripts\python.exe -m unittest tests.test_readiness
  tests.test_unified_entrypoint -v`：11 项全部通过。正反路径包括完整的 20 个
  不同 task 语料、空语料、一个损坏 run、重复执行同一 task 试图满足计数、
  非法阈值和公共 CLI 非零退出。
- 当前 checkout 实测 `.\jobslayer.cmd inspect-readiness --state-root
  .jobslayer --required-reviewed-tasks 20`：按设计退出 1；发现 0 个 run，报告
  `automated_gate_passes: false`、`manual_confirmation_required: true`，并明确列出
  0/20 不同 task、缺少决定应用后的完成路径和缺少负路径证据。
- 最终通过统一 Windows 入口运行 `.\jobslayer.cmd check`：7/7 全部通过；
  完整 unittest 为 118 项 `OK`，1 项因当前 Windows 未授予目录 symlink 权限
  而 skip；compileall、pip check、测试床、两套 runbook 绑定和
  `git diff --check` 均通过。
- readiness 只量化已有本地证据，不能修复跨文件提交窗口，也不能认证人工身份。
- 自动门禁按不同 `task_id` 计数，重复运行同一 task 不能满足 20 个样例要求；
  但真实人工计划复盘仍由人工确认，不由该计数器代替。
- 下一切片 ST-01 将定义持久化/恢复端口、幂等键和故障注入，不直接引入数据库。

---

## DEV-2026-08-11-02 — ST-01 证据约束的本地恢复首切片

- 状态：完成（首个恢复窗口已闭合；ST-01 其余提交窗口继续执行）
- 类型：恢复协议、派生投影、故障注入、CLI 与架构文档

### 背景与决策

ST-00 门禁实现完成后，继续执行 ST-01。审计确认 accepted implementation
review 的落盘顺序是：注册 review/card 制品并经 kernel 进入 `MergeReview`、
追加 run ledger、最后 create-only 写出 `decision-card.json`。最后一步前崩溃时
权威证据完整但 UI 投影缺失；重跑 review 会重复业务动作，手工复制又缺少绑定
验证。

依据 ADR-0016，本轮定义 provider-neutral recovery contract 和本地 adapter。
自动恢复只重建 ledger 中严格 `DecisionCard` 可证明的缺失投影；篡改文件、
journal/ledger 不一致或无权威执行记录全部停止并升级，不改变工作流状态。

### 当前落实

1. 新增 `RecoveryAssessment`、`RecoveryStatus`、`RunRecoveryManager` 和
   `LocalRunRecoveryManager`。
2. `inspect-recovery` 提供只读分类；`recover-run` 执行唯一受支持的 create-only
   决策卡恢复，健康 run 重复调用保持幂等。
3. 恢复前复用 `LocalRunCoordinator.inspect` 验证双链、制品和状态，不建立第二
   套工作流真相。
4. 已存在但无效、不匹配或为 symlink 的卡片不覆盖；journal/ledger 提交缺口
   分类为 `manual_intervention`。
5. 投影写入循环处理 partial write 并 `fsync`；注入写入故障后删除本次创建的
   不完整文件，恢复条件仍然成立。

### 验证、限制与下一步

- 本切片变更文件：`README.md`、`docs/DEVELOPMENT_LOG.md`、
  `docs/MINIMUM_DEVELOPMENT_LOOP.md`、`docs/PROJECT_GUIDE.md`、
  `docs/ROADMAP.md`、`docs/SHORT_TERM_INFRASTRUCTURE_PLAN.md`、
  `docs/UNIFIED_ENTRYPOINT.md`、`docs/adr/README.md`、
  `docs/adr/0016-evidence-bounded-local-run-recovery.md`、
  `src/jobslayer/adapters/local_recovery.py`、`src/jobslayer/cli.py`、
  `src/jobslayer/recovery/__init__.py`、`tests/test_local_run.py`、
  `tests/test_unified_entrypoint.py`。
- 定向命令 `.venv\Scripts\python.exe -m unittest tests.test_local_run
  tests.test_readiness tests.test_unified_entrypoint -v`：20 项全部通过。
  恢复路径覆盖健康 run、缺失投影、重复恢复；拒绝路径覆盖被篡改投影和
  journal/ledger 提交缺口；故障注入覆盖 partial write 后清理本次不完整文件。
- `.\jobslayer.cmd inspect-recovery --help` 与
  `.\jobslayer.cmd recover-run --help` 均通过统一 Windows 入口返回 0，命令已进入
  公共 parser；`git diff --check` 通过。
- 通过统一 Windows 入口运行 `.\jobslayer.cmd check`：7/7 全部通过；完整
  unittest 为 123 项 `OK`，1 项因当前 Windows 未授予目录 symlink 权限而
  skip；compileall、pip check、测试床、两套 runbook 绑定和 Git diff 门禁
  均通过。
- 当前只闭合 review ledger 已提交、decision-card 投影未创建这一窗口；其他阶段
  仍只会保守升级人工处理。
- 下一切片需要为 execution、decision application、source integration 和
  cleanup 定义幂等键和可恢复提交事实，并增加真实子进程 crash harness。

---

## DEV-2026-08-12-01 — ST-01 源码集成记录的只读证明恢复

- 状态：完成（第二个恢复窗口已闭合；ST-01 继续执行）
- 类型：Git 事实证明、Completed 重试语义、集成记录恢复、故障回放与 ADR

### 背景与决策

继续审计 `integrate-run` 的提交顺序后确认，进程可在本地目标分支已经
fast-forward、集成结果制品已经注册、kernel 已追加 `Completed`，但
`source_integration` run record 尚未追加时退出。此前恢复器会把该状态统一升级
为人工处理；直接重跑协调器虽然 Git adapter 通常幂等，却会创建新的集成制品
manifest，并可能使补写记录引用的 artifact id 与原完成转换不一致。

依据新增 ADR-0017，本轮把首次集成与完成后恢复分开：首次路径仍由受控 adapter
创建提交和 fast-forward；`Completed` 路径只能读取原转换引用的集成制品，并用
Git commit/message/tree/paths/base/HEAD 的只读证明验证该结果。只有前三条 ledger、
journal、全部原制品、批准人和五项完成证据引用均一致时，恢复器才允许追加缺失
记录。它不调用 `WorkflowKernel.transition`，不重新执行 Agent，也不重复 Git
集成。

### 当前落实

1. `LocalGitIntegrator.verify_existing_integration` 只读验证目标分支和 source
   worktree 均干净、source 是固定 base 之上的唯一批准提交、提交消息/路径/tree
   与 reviewed patch 相同、目标 HEAD 精确等于 source commit，并复核持久化
   `SourceIntegrationResult` 的稳定字段。
2. `LocalRunCoordinator.integrate` 在状态已经为 `Completed` 时，不再调用 mutating
   `integrate` 或注册替代制品；它读取完成转换引用的唯一原始集成制品，验证
   decision、authority、verification、artifact 和 integration id 后，仅恢复缺失
   run record。
3. `LocalRunRecoveryManager` 新增 `resume_source_integration_record` assessment/action。
   它先验证 run ledger 与 journal 链、决定转换、所有历史制品、完成转换绑定和
   当前 Git 事实，再允许调用上述 completed resume 路径。
4. 目标分支在崩溃后继续前进、集成 artifact 绑定改变、journal/ledger 输入无法
   重构时均返回 `invalid_evidence`，不会补账或改变 Git。
5. 文件级崩溃回放先完整执行集成，再只保留前三条 run record。正向测试通过 mock
   明确禁止恢复调用 mutating Git 方法，同时断言目标/worktree HEAD 和 workflow
   journal 不变、第四条记录只追加一次、重复恢复保持一致。

### 验证、限制与下一步

- 本切片变更文件：`README.md`、`docs/DEVELOPMENT_LOG.md`、
  `docs/MINIMUM_DEVELOPMENT_LOOP.md`、`docs/PROJECT_GUIDE.md`、
  `docs/ROADMAP.md`、`docs/SHORT_TERM_INFRASTRUCTURE_PLAN.md`、
  `docs/UNIFIED_ENTRYPOINT.md`、`docs/adr/README.md`、
  `docs/adr/0017-read-only-git-attestation-before-integration-record-recovery.md`、
  `src/jobslayer/adapters/local_git_integration.py`、
  `src/jobslayer/adapters/local_recovery.py`、
  `src/jobslayer/application/local_run.py`、`tests/test_local_run.py`。
- 定向命令 `.venv\Scripts\python.exe -m unittest tests.test_local_run`：12 项全部
  通过，用时 33.983 秒。新增正向覆盖缺失集成记录恢复及重复幂等；拒绝覆盖目标
  HEAD 漂移和原集成 artifact 绑定改变。
- 通过统一 Windows 入口运行 `\.jobslayer.cmd check` 的实际命令为
  `.\jobslayer.cmd check`：7/7 全部通过；完整 unittest 为 126 项 `OK`，1 项因
  当前 Windows 未授予目录 symlink 权限而 skip；compileall、pip check、
  BraveNewWorld 测试床、scripted/Codex 两套 runbook 绑定与 `git diff --check`
  均通过。完整门禁用时 74.1 秒，测试用时 71.103 秒。
- 本轮仍是文件级崩溃窗口回放，没有在独立进程的精确指令边界执行强制退出；run
  ledger 自身若发生 partial append 仍可能需要人工处理。
- execution、decision application 和 cleanup 提交窗口尚未闭合；目标分支在完成
  后继续前进也会保守拒绝自动恢复，不推断“提交仍在历史中”即可接受。
- 下一步优先建立进程级 crash harness 和 run ledger 写入提交帧/partial append
  语义，再用同一证据矩阵处理 decision application 与 cleanup；ST-01 满足退出
  条件前不引入 PostgreSQL。
- 更正：本条验证记录中统一 Windows 入口的精确命令是
  `.\jobslayer.cmd check`；前文第一个反引号内多写了一个点，验证结果不受影响。

---

## DEV-2026-08-12-02 — ST-01 源码集成边界的真实进程崩溃验证

- 状态：完成（source integration crash harness 已接通；其他阶段矩阵待扩展）
- 类型：子进程故障注入、跨重启恢复验证、测试基础设施

### 背景与落实

DEV-2026-08-12-01 用删除第四条 ledger 记录回放了目标崩溃状态，但该方法没有证明
真实进程退出时 Git、artifact registry 和 workflow journal 已经按预期持久化。为
补足这一层，本轮在测试子进程中临时替换 `LocalRunLedger.append`，让正常
`LocalRunCoordinator.integrate` 完成 Git fast-forward、集成制品注册和
`Completed` journal fsync 后，在调用 source-integration append 的精确边界通过
`os._exit(86)` 强制终止进程。故障钩子仅存在于测试脚本字符串，没有进入产品 API
或生产代码。

父进程随后重新构造协调器/恢复器，并验证真实落盘状态为三条 run record、
`Completed` 最终转换和已前进的目标 HEAD。恢复时继续 mock 禁止 mutating
`LocalGitIntegrator.integrate`，证明新进程只通过原始制品和只读 Git attestation
追加第四条记录，目标 HEAD 保持不变。

### 验证、限制与下一步

- 本切片新增变更位于 `tests/test_local_run.py`，并同步更新
  `docs/DEVELOPMENT_LOG.md`、`docs/ROADMAP.md` 和
  `docs/SHORT_TERM_INFRASTRUCTURE_PLAN.md`。
- 定向命令 `.venv\Scripts\python.exe -m unittest
  tests.test_local_run.LocalRunCoordinatorTests.test_subprocess_crash_after_completed_recovers_without_reintegration
  -v`：1 项通过，用时 5.223 秒；子进程退出码严格为 86。
- 通过统一 Windows 入口运行 `.\jobslayer.cmd check`：7/7 全部通过；完整
  unittest 为 127 项 `OK`，1 项因当前 Windows 未授予目录 symlink 权限而 skip；
  compileall、pip check、BraveNewWorld 测试床、scripted/Codex 两套 runbook 绑定
  与 `git diff --check` 均通过。完整门禁用时 79.8 秒，测试用时 77.488 秒。
- 该 harness 目前只覆盖“完成转换已提交、ledger append 尚未调用”，尚未在
  `os.write` 中途退出，也未覆盖 execution、decision application 和 cleanup。
- 下一步应先定义 LocalRunLedger 的 durable append/partial-tail 语义和对应崩溃
  注入点；否则扩大业务阶段恢复仍会共享一个未明确定义的底层写入风险。

---

## DEV-2026-08-12-03 — ST-01 workflow/run 追加链的原子 generation 发布

- 状态：完成（partial JSON 风险已从本地双链提交原语移除）
- 类型：持久化原语、崩溃一致性、跨平台文件发布与 ADR

### 背景与决策

源码集成业务边界恢复接通后，继续下钻发现 `records.jsonl` 和更关键的
`workflow.jsonl` 都直接使用一次 `O_APPEND/os.write` 写最终文件。任意长度 write
不具备跨崩溃完整性保证；若进程退出时只写入半条 JSON，恢复器无法证明尾部是未
提交临时内容，自动 truncate 又会破坏 append-only 证据边界。

依据 ADR-0018，本轮保留全部 JSONL schema、sequence 和 hash-chain 规则，但把
物理提交改为前缀保持的 atomic generation publication：验证旧链后，在目标同
目录临时文件循环写入完整旧字节前缀和一条新记录，fsync 文件，再 `os.replace`
发布；POSIX 额外 fsync 父目录。逻辑证据只能追加，物理路径在 replace 前后只会
指向完整旧 generation 或完整新 generation。

### 当前落实

1. `LocalRunLedger.append` 和 `JsonlAuditJournal.append_transition` 均拒绝从无效旧
   链生成新版本，使用完整写入循环、文件 fsync、同目录 replace 和正常异常路径
   临时文件清理。
2. Windows 使用原生 `os.replace`；POSIX 在 replace 后打开父目录并 fsync。没有
   添加第三方依赖，也没有改变公共领域模型或工作流状态规则。
3. 两条链都增加 partial-generation-write 故障注入：临时文件写入一半后抛错，
   最终权威路径字节保持完全不变，旧链继续可读，正常异常路径无残留临时文件。
4. 两条链都增加真实子进程 crash harness：replace 前通过 `os._exit` 退出只看到
   旧链；replace 执行后立即退出只看到完整新链，sequence、previous hash 与 record
   hash 均有效。
5. 强制退出在 replace 前可能留下隐藏临时文件；reader 明确只读取权威路径，因此
   未提交临时文件不参与工程真相。

### 验证、限制与下一步

- 本切片变更文件：`README.md`、`docs/DEVELOPMENT_LOG.md`、
  `docs/PROJECT_GUIDE.md`、`docs/ROADMAP.md`、
  `docs/SHORT_TERM_INFRASTRUCTURE_PLAN.md`、`docs/adr/README.md`、
  `docs/adr/0018-prefix-preserving-atomic-jsonl-publication.md`、
  `src/jobslayer/application/run_records.py`、
  `src/jobslayer/workflow/journal.py`、`tests/test_run_records.py`、
  `tests/test_workflow.py`。
- 定向命令 `.venv\Scripts\python.exe -m unittest tests.test_workflow
  tests.test_run_records -v`：17 项全部通过，用时 1.236 秒；覆盖两条链的允许写入、
  篡改拒绝、partial write、replace 前退出和 replace 后退出。
- 通过统一 Windows 入口运行 `.\jobslayer.cmd check`：7/7 全部通过；完整
  unittest 为 131 项 `OK`，1 项因当前 Windows 未授予目录 symlink 权限而 skip；
  compileall、pip check、BraveNewWorld 测试床、scripted/Codex 两套 runbook 绑定
  与 `git diff --check` 均通过。完整门禁用时 63.1 秒，测试用时 61.316 秒。
- 当前仍是单控制器语义：thread lock 不能替代多进程 CAS/租约/事务隔离；全文件
  generation 发布成本随链长度增长。ST-02 仍需事务存储 adapter，不能把本实现
  宣称为生产级并发账本。
- 下一步按 ST-01 顺序闭合 cleanup（外部 worktree 删除、ledger 未追加）与
  decision application（转换已追加、ledger 未追加）业务窗口；execution 首记录
  缺失需要先补充可重构的 execution intent/outcome 持久边界。

---

## DEV-2026-08-12-04 — ST-01 决定应用与 cleanup 的无重放恢复

- 状态：完成（decision application 与 cleanup 提交窗口已闭合）
- 类型：授权证据绑定、workspace removal contract、进程崩溃恢复与 ADR

### 背景与决策

decision application 会先注册决定/authority 制品并经 kernel 转换，再追加第三条
run record；cleanup 会先移除 Git worktree，再追加第五条记录。两者在最后一步前
崩溃时，重复调用原命令分别会尝试第二次状态转换，或把“路径不存在”误当作完整
清理证明。

依据 ADR-0019，本轮将恢复建立在原副作用证据上：决定转换必须引用带 task/run
binding 的原 decision/authority artifact，并由 provider-neutral validator 只读
验证已应用转换；workspace manager 提供结构化 removal inspection，证明路径和 Git
注册均消失、source branch 仍精确指向 integrated commit。恢复只追加缺失 run
record，不重放 kernel transition 或 worktree remove。

### 当前落实

1. `DecisionApplicationService.apply` 接受 application 层追加的 evidence ids；本地
   协调器把 decision/authority artifact ids 写入同一次决定转换。
2. 新增 `validate_applied_transition`，重新校验 card hash、decision evidence、
   authority actor/kind/转换时有效窗口、option-state 映射、actor/from/to 与制品引用，
   但不调用 kernel transition。
3. 恢复器新增 `resume_decision_application_record`：从 transition 引用的唯一两个
   制品恢复严格模型，验证全部历史制品及 producer/run/task binding 后补写原记录。
4. 新增 provider-neutral `WorkspaceRemovalInspection` 和 `WorkspaceManager.inspect_removal`；
   Git adapter 验证 path absence、worktree registration absence、source branch 和
   expected commit。
5. cleanup 正常路径在 remove 后必须通过 `safely_removed`，新记录持久化结构化
   removal evidence；reader 兼容此前没有该字段的 Phase 0 旧记录。
6. 恢复器新增 `resume_workspace_cleanup_record`。真实子进程在 remove 完成、ledger
   append 前退出后，新进程验证保留分支再补写记录；测试明确禁止再次调用 remove。

### 验证、限制与下一步

- 本切片变更文件：`README.md`、`docs/DEVELOPMENT_LOG.md`、
  `docs/MINIMUM_DEVELOPMENT_LOOP.md`、`docs/PROJECT_GUIDE.md`、
  `docs/ROADMAP.md`、`docs/SHORT_TERM_INFRASTRUCTURE_PLAN.md`、
  `docs/UNIFIED_ENTRYPOINT.md`、`docs/adr/README.md`、
  `docs/adr/0019-replay-free-decision-and-cleanup-record-recovery.md`、
  `src/jobslayer/adapters/git_workspace.py`、
  `src/jobslayer/adapters/local_recovery.py`、
  `src/jobslayer/application/local_run.py`、`src/jobslayer/domain/models.py`、
  `src/jobslayer/supervision/application.py`、
  `src/jobslayer/workspace/manager.py`、`tests/test_decision_application.py`、
  `tests/test_git_workspace.py`、`tests/test_local_run.py`。
- 定向命令 `.venv\Scripts\python.exe -m unittest tests.test_git_workspace
  tests.test_local_run -v`：26 项全部通过，用时 43.430 秒；随后
  `.venv\Scripts\python.exe -m unittest tests.test_decision_application
  tests.test_local_run -v`：24 项全部通过，用时 45.575 秒。
- workflow 规则相关正反测试已成对增加：合法已应用转换携带并验证两个 artifact
  evidence；缺失必需 artifact evidence 的转换被只读 validator 拒绝且不改变状态。
- 恢复正向覆盖 decision/cleanup 真实子进程退出及重复幂等；拒绝覆盖 decision
  artifact producer 篡改和保留 source branch commit 漂移。
- 通过统一 Windows 入口运行 `.\jobslayer.cmd check`：7/7 全部通过；完整
  unittest 为 138 项 `OK`，1 项因当前 Windows 未授予目录 symlink 权限而 skip；
  compileall、pip check、BraveNewWorld 测试床、scripted/Codex 两套 runbook 绑定
  与 `git diff --check` 均通过。完整门禁用时 89.3 秒，测试用时 87.192 秒。
- 新恢复只接受显式引用 decision/authority artifacts 的决定转换；旧转换若缺少该
  绑定会保守要求人工处理，不根据当前文件猜测。
- ST-01 现在只剩 execution 首记录窗口。下一步需要在执行副作用之前落盘
  provider-neutral intent，并在结束后持久化足够重构 `TaskExecutionOutcome` 的证据，
  同时避免恢复器擅自重跑 Agent。

---

## DEV-2026-08-12-05 — ST-01 execution intent/outcome 恢复边界

- 状态：完成（ST-01 本地单控制器退出条件已闭合）
- 类型：执行意图契约、outcome 制品、manifest 查询、进程崩溃恢复与 ADR

### 背景与决策

execution 在此前实现中直到 Agent、补丁和验证全部完成后才追加首条 run record；
中途退出只留下空 ledger，恢复器无法区分未启动、执行中和 outcome 已完成。自动
重跑 Agent 会把 retry policy 交给恢复器并重复副作用，因此不能作为默认策略。

依据 ADR-0020，本轮建立 intent/outcome 双制品边界：调用 controller/Agent 前先
持久化完整授权输入；controller 返回严格 `TaskExecutionOutcome` 后、ledger 前再
持久化完整 outcome。只有两者唯一、有效并与 journal/workspace/patch 一致时才能
补写 execution record；只有 intent 时稳定升级人工处理并明确拒绝重跑。

### 当前落实

1. 新增 provider-neutral `TaskExecutionIntent`，模型层绑定 intent/run/task、
   invocation、validation profile、authorization 和有效时间窗口。
2. 新增 `LocalExecutionIntentEnvelope`，保存 source-controlled runbook path/hash、
   testbed 及通过的 local baseline inspection，并可确定性还原 execution context。
3. `LocalRunCoordinator.execute` 在 Agent 前注册 run-bound
   `task-execution-intent`，在严格 outcome 返回后注册
   `task-execution-outcome`；新 execution record 引用两份 manifest。
4. `ArtifactRegistry` 增加 `list_manifests` provider-neutral 查询；本地 adapter 按
   type/task/run 过滤并对每份 manifest/content 做完整验证，不跳过损坏项。
5. 恢复器新增 `resume_execution_record`：验证 intent/outcome producer/task/run、
   outcome 内全部制品、journal 最终状态、worktree 和 persisted patch 后补写首记录。
6. 后续 decision/integration/cleanup 专项恢复也会验证新 execution persistence
   artifacts；旧 Phase 0 execution record 没有新字段时仍保持兼容。
7. 真实子进程 crash harness 覆盖两侧：outcome 已落盘、ledger 前退出可自动恢复；
   controller 调用前退出只留下 intent，分类为 `manual_intervention` 且
   `recover-run` 拒绝。测试通过 mock 明确证明恢复不会调用 execute/Agent。

### 验证、限制与下一步

- 本切片变更文件：`README.md`、`docs/DEVELOPMENT_LOG.md`、
  `docs/MINIMUM_DEVELOPMENT_LOOP.md`、`docs/PROJECT_GUIDE.md`、
  `docs/ROADMAP.md`、`docs/SHORT_TERM_INFRASTRUCTURE_PLAN.md`、
  `docs/UNIFIED_ENTRYPOINT.md`、`docs/adr/README.md`、
  `docs/adr/0020-execution-intent-outcome-recovery-boundary.md`、
  `src/jobslayer/adapters/local_artifacts.py`、
  `src/jobslayer/adapters/local_recovery.py`、
  `src/jobslayer/application/execution_intent.py`、
  `src/jobslayer/application/local_run.py`、
  `src/jobslayer/artifacts/registry.py`、`src/jobslayer/domain/models.py`、
  `tests/test_artifacts.py`、`tests/test_local_run.py`。
- 定向命令 `.venv\Scripts\python.exe -m unittest
  tests.test_local_run.LocalRunCoordinatorTests.test_subprocess_execution_crash_recovers_persisted_outcome_without_rerun
  tests.test_local_run.LocalRunCoordinatorTests.test_execution_intent_without_outcome_never_reruns_agent
  -v`：2 项通过，用时 4.177 秒。
- 定向命令 `.venv\Scripts\python.exe -m unittest tests.test_artifacts
  tests.test_local_run -q`：24 项全部通过，用时 49.226 秒；额外拒绝路径覆盖 outcome
  manifest producer 改写。
- 通过统一 Windows 入口运行 `.\jobslayer.cmd check`：7/7 全部通过；完整
  unittest 为 142 项 `OK`，1 项因当前 Windows 未授予目录 symlink 权限而 skip；
  compileall、pip check、BraveNewWorld 测试床、scripted/Codex 两套 runbook 绑定
  与 `git diff --check` 均通过。完整门禁用时 80.7 秒，测试用时 78.894 秒。
- intent-only 表示“获准尝试一次”而非可重试许可；Agent 已启动但 outcome 未提交的
  run 仍需人工调查。这是显式保守边界，不会被描述为自动恢复成功。
- ST-01 在本地单控制器范围内已完成。ST-02 仍受 ST-00 语料/人工体验门禁阻塞；
  下一步先建立可保留的 20-task 内部语料生成与验收流程，不提前引入 PostgreSQL。

---

## DEV-2026-08-12-06 — ST-00 源码定义的跨平台 21-run 语料

- 状态：自动退出条件完成；真实人工决定体验仍待操作者提交
- 类型：运行语料、跨平台回归、CI matrix、入口与 ADR

### 背景与决策

`inspect-readiness` 已经能够拒绝损坏 run，但本地默认 state root 没有足够样例。
提交机器生成的绝对路径、worktree 和制品不可移植；手写通过摘要又会绕过 kernel、
验证和审计链。依据 ADR-0021，本轮把 case matrix 作为源码，生成器创建固定 Git
testbed 和绑定的 control inputs，并只调用真实 `LocalRunCoordinator` 服务形成证据。

自动 fixture 不能冒充人类。报告显式写入 `evidence_class` 和
`human_confirmation_claimed: false`；真实人工体验仍是独立门禁。

### 当前落实

1. `corpora/phase0-foundation-v1.json` 固定 21 个不同 case：20 个进入独立实现审查，
   其中分别覆盖 approve/complete、request changes、reject/cancel 和 17 个待决定；
   第 21 个通过真实失败验证进入 `Repairing`。
2. `Phase0CorpusBuilder` create-only 生成 testbed/control Git 仓库、task/profile/
   runbook/patch、run/worktree/artifacts 和最终报告；失败保留现场，既有目录拒绝覆盖。
3. `build-phase0-corpus` 进入统一 CLI。生成结束后先内部运行 readiness，再用独立
   `inspect-readiness` 二次复核。
4. `.github/workflows/phase0-corpus.yml` 对 Windows/Ubuntu 使用同一定义执行完整
   `check`、构建和二次门禁；远端状态必须等变更提交/推送后才能取得。
5. WSL 初次复核发现 POSIX 只读 manifest 的三个篡改测试在 Windows 上被权限语义
   掩盖；测试现在先显式 `chmod(0600)` 再注入篡改，生产 manifest 仍保持只读。
6. WSL 读取 Windows CRLF checkout 时原 `git diff --check` 误报整库；统一门禁改为
   `git -c core.autocrlf=true diff --check`，按 Git 文本规则规范化后仍检查普通尾随
   空白和冲突标记。

### 验证、限制与下一步

- 本切片文件：`.github/workflows/phase0-corpus.yml`、
  `corpora/phase0-foundation-v1.json`、`README.md`、
  `docs/DEVELOPMENT_LOG.md`、`docs/MINIMUM_DEVELOPMENT_LOOP.md`、
  `docs/ROADMAP.md`、`docs/SHORT_TERM_INFRASTRUCTURE_PLAN.md`、
  `docs/UNIFIED_ENTRYPOINT.md`、`docs/adr/README.md`、
  `docs/adr/0021-source-defined-cross-platform-phase0-corpus.md`、
  `src/jobslayer/application/phase0_corpus.py`、`src/jobslayer/cli.py`、
  `src/jobslayer/development/checks.py`、`tests/test_development_checks.py`、
  `tests/test_local_run.py`、`tests/test_phase0_corpus.py`、`pyproject.toml`。
- Windows `\.\jobslayer.cmd build-phase0-corpus`：58.9 秒，生成 21 个有效 run；
  随后 `\.\jobslayer.cmd inspect-readiness --state-root
  .jobslayer/phase0-corpus/state --required-reviewed-tasks 20` 返回 0。计数为
  discovered/valid 21/21、reviewed task 20、completed 1、decision-applied completed
  1、negative 3、invalid 0。
- Ubuntu 24.04 / WSL2 使用隔离 Python 3.12 venv 执行
  `JOBSLAYER_PYTHON=/var/tmp/jobslayer-posix-foundation-20260812-01/venv/bin/python
  ./jobslayer check`：7/7 通过，145 项测试 `OK`；随后用同一解释器执行
  `./jobslayer build-phase0-corpus --output-root
  /var/tmp/jobslayer-posix-foundation-20260812-01/phase0-corpus` 和相应
  `inspect-readiness`，得到与 Windows 相同的 21/20/1/3/0 门禁结果。
- 第一次 WSL `check` 暴露上述三项 POSIX 权限测试错误和 CRLF diff 误报，修复后
  完整重跑通过；没有把失败尝试记作成功证据。
- GitHub Actions matrix 已定义但尚未提交/推送，因此不能声称远端 CI 已绿。
- loopback 人工页面已为 `phase0-foundation-v1-case-03-run` 启动，决定文件仍等待
  操作者实际提交；完成前 ST-00 和 Phase 0 不标记全部完成。
- 下一步在人工体验完成后追加实际选项、理解成本、问题和改进复盘，再取得远端
  matrix 结果；自动 fixture 永远不替代这两项证据。

---

## DEV-2026-08-12-07 — ST-02 provider-neutral 事务端口与 SQLite contract adapter

- 状态：完成第一契约切片；PostgreSQL 和应用切换待 ST-00 人工门禁
- 类型：事务状态、迁移、乐观并发、append-only SQL 与 outbox

### 背景与决策

Phase 0 文件 backend 的各条链分别可靠，但 workflow/run/artifact metadata 还没有
单一事务边界。直接提前引入 PostgreSQL 驱动和服务会越过路线图退出条件。依据
ADR-0022，本轮先让 kernel 脱离具体 JSONL，并以标准库 SQLite 证明未来数据库
adapter 必须满足的事务 contract；SQLite 不作为现有 run 的异步镜像，也不冒充
生产 PostgreSQL。

### 当前落实

1. `AuditJournal` 成为 kernel 的 provider-neutral 端口；transition 构造和完整链
   复核可由 JSONL/SQL adapter 共用。`ReviewSession` 同样不再要求具体 JSONL。
2. operational record 契约改为 `RunRecord`，保留 `LocalRunRecord` 兼容别名，并
   共用 hash 构造和阶段序列验证。
3. `ControlPlaneStore`/`StateTransaction` 显式绑定 task/run expected sequence；
   journal transition、run record、artifact manifest 和 outbox event 只能显式整批
   commit，离开未提交事务则 rollback。
4. truth mutation 没有同事务 outbox 时拒绝；outbox 依 commit order 查询并支持
   幂等 publication mark。
5. SQLite migration 是源码包资源，记录 SHA-256；WAL/FULL synchronous、
   `BEGIN IMMEDIATE` 和数据库 trigger 提供本地跨进程顺序及 workflow/run/artifact
   的 UPDATE/DELETE 拒绝。

### 验证、限制与下一步

- 本切片文件：`docs/DEVELOPMENT_LOG.md`、
  `docs/SHORT_TERM_INFRASTRUCTURE_PLAN.md`、`docs/adr/README.md`、
  `docs/adr/0022-transactional-state-port-and-sqlite-contract-adapter.md`、
  `pyproject.toml`、`src/jobslayer/adapters/sqlite_state.py`、
  `src/jobslayer/application/run_records.py`、
  `src/jobslayer/persistence/__init__.py`、
  `src/jobslayer/persistence/migrations/__init__.py`、
  `src/jobslayer/persistence/migrations/001_initial.sql`、
  `src/jobslayer/supervision/session.py`、`src/jobslayer/workflow/__init__.py`、
  `src/jobslayer/workflow/journal.py`、`src/jobslayer/workflow/kernel.py`、
  `tests/test_sqlite_state.py`。
- 定向命令 `.venv\Scripts\python.exe -m unittest tests.test_sqlite_state -v`：
  8 项全部通过，用时 0.226 秒。正向覆盖重启持久化、并发 writer 顺序、outbox
  投递；拒绝覆盖重复 metadata 整批回滚、无 outbox、陈旧版本、truth UPDATE/
  DELETE 和迁移 checksum 漂移。
- 通过统一 Windows 入口运行 `\.\jobslayer.cmd check`：7/7 全部通过；完整
  unittest 为 153 项 `OK`，1 项因当前 Windows 未授予目录 symlink 权限而 skip；
  compileall、pip check、测试床、两套 runbook 和 normalized Git diff 均通过。
  完整门禁用时 121.8 秒，测试用时 119.102 秒。
- SQLite adapter 尚未接管 `LocalRunCoordinator`，也没有 PostgreSQL adapter、
  dispatcher、备份或跨主机测试，因此 ST-02 仍在施工中。
- 下一步在 ST-00 人工体验确认后，为同一端口实现 PostgreSQL schema/adapter 与
  contract suite，再把应用命令的 metadata 提交切换到唯一事务真相；禁止先做
  文件到数据库的双真相镜像。

---

## DEV-2026-08-12-08 — ST-00 真实人工决定体验与退出确认

- 状态：完成（ST-00 本地退出条件闭合）
- 类型：人工监督证据、完整性复核与临时服务回收

### 实际体验与证据

1. 操作者在 `phase0-foundation-v1-case-03-run` 的真实 loopback 审查页查看运行、
   验证、补丁和独立审查证据后，选择 `approve` 并填写理由“已提交”。
2. 页面 create-only 写入 `decision.json`；运行仍停在 `MergeReview`，决定
   `recorded=true`、`applied=false`，没有自动执行决定、Git 集成、push 或部署。
3. 重新加载 `DecisionCard`/`HumanDecision` 后复核 card id、task id、卡片 SHA-256、
   可见选项、非空理由和证据列表；决定全部绑定原卡片。
4. `verify-journal` 返回 `valid=true`、5 条记录；`inspect-run` 返回 workflow/run
   双链和全部制品有效。决定写入后现有 run 投影能够正确显示已记录但未应用。
5. 体验完成后按精确命令行确认并关闭专用 `run-ui` 监督进程及其子进程，端口
   `127.0.0.1:8765` 已释放；未停止其他服务。

### 验证、限制与下一步

- 实际命令：`.\jobslayer.cmd verify-journal
  .jobslayer\phase0-corpus\state\runs\phase0-foundation-v1-case-03-run\workflow.jsonl`
  返回 0；`.\jobslayer.cmd inspect-run ... --root . --state-root
  .jobslayer\phase0-corpus\state` 返回 0。
- 本次人工体验使用的是迁移前已启动的本地声明身份 `local-operator`。它证明卡片
  理解、提交和“记录不应用”的交互边界，但不作为 ST-03 认证证据；后续公共 UI
  启动入口已开始改为只接受签名会话。
- GitHub Actions matrix 仍因本地变更尚未提交/推送而没有远端绿色状态；已取得的
  原生 Windows 与 Ubuntu/WSL2 同语料结果继续作为本地跨平台退出证据，不能冒充
  远端 CI。
- ST-00 不再阻塞 PostgreSQL adapter。下一步并行闭合 ST-02 事务真相和 ST-03
  认证身份/可验证 authority，再进入隔离、预算与管理查询底座。

---

## DEV-2026-08-12-09 — 完整主机权限下的长时开发安全策略

- 状态：生效并贯穿后续全部短期基础设施切片
- 类型：用户安全约束、操作边界与 ADR

### 新增约束与落实

用户明确要求：由于系统权限完全开放且任务长时运行，本阶段必须采用安全策略并避免
危险操作。ADR-0023 将这一要求固化为 fail-closed 边界：主机权限不扩大授权，工作
只限仓库和显式临时目录；不做递归广域删除、系统级安装、远端发布或凭据传播；状态、
身份、sandbox、预算和 lease 缺少证据时均在副作用前拒绝。

本要求加入时，唯一仍运行的临时基础设施是 WSL `/var/tmp/jobslayer-postgres-
contract-20260812` 下的普通用户 PostgreSQL 16 contract 实例。已使用该目录内精确
`pg_ctl -D ... -m fast -w stop` 停止，输出 `server stopped`；未修改系统包数据库，
未停止其他服务。此前临时 review UI 同样已按精确父/子命令行回收并释放 8765 端口。

### 持续验证要求

- 后续 Linux sandbox 测试只使用 `/var/tmp/jobslayer-sandbox-20260812` 中解压的
  bubblewrap，不进行系统安装；Windows 能力不足时必须明确拒绝，不能降级冒充强隔离。
- PostgreSQL/CI/浏览器等外部能力不可用时保留明确证据缺口；只有取得真实结果后才
  更新退出状态。
- 完成各切片前继续运行统一 `\.\jobslayer.cmd check`，并在最终日志记录完整命令、
  结果、限制和临时资源回收状态。

---

## DEV-2026-08-12-10 — ST-02 PostgreSQL 与事务执行主线

- 状态：完成
- 类型：PostgreSQL adapter、共享 contract、Kernel 转换缓冲与阶段事务

### 背景与决策

ST-00 人工门禁完成后，依据 ADR-0024 将 SQLite 固化的事务 contract 实现到
PostgreSQL，并建立不跨越长时间 Agent/Git 的事务协调路径。Phase 0 JSONL 继续作为
旧语料与恢复路径，不异步镜像；新的事务协调器以数据库为唯一 metadata 真相。

### 当前落实

1. PostgreSQL adapter 实现 checksum migration、advisory lock、expected sequence、
   append-only trigger、同事务 artifact metadata/outbox 及幂等 publication mark，并与
   SQLite 共用 `StateStoreContractTests`。
2. `StateTransaction.append_transition_record` 只接纳 `WorkflowKernel` 产生的精确下一条
   transition；重新构造、篡改 sequence/state/hash 的记录全部拒绝。
3. `TransactionalExecutionCoordinator` 在 Agent 前单独提交 execution intent 和输入制品
   metadata；Agent 期间只在事务外产生可验证 transition buffer；终态后把 workflow、
   run record、artifact metadata 与 outbox 原子提交。
4. implementation review、签名 decision application、source integration 和 cleanup 使用
   相同阶段事务。无权限的 integration 在调用 Git 或数据库前拒绝；成功完成仍要求通过
   verification、授权 actor 和 `SourceIntegrationResult`，并只通过 Kernel 转换状态。
5. control-plane 查询新增 run index 和持久事件读取；intent-only 故障可见但不会自动重跑。

### 变更、验证与限制

- 主要文件：`src/jobslayer/persistence/`、`src/jobslayer/adapters/sqlite_state.py`、
  `src/jobslayer/adapters/postgres_state.py`、
  `src/jobslayer/application/transactional_execution.py`、
  `tests/state_store_contract.py`、`tests/test_sqlite_state.py`、
  `tests/test_postgres_state.py`、`tests/test_transactional_execution.py`、
  `docs/adr/0024-transactional-postgres-control-plane-and-buffered-coordinator.md`。
- 定向 `python -m unittest tests.test_transactional_execution -v`：4 项通过，覆盖重启、
  intent-only、治理组合和完整执行至 cleanup；完整环境结果统一记录在 DEV-14。
- PostgreSQL adapter 不包含托管服务、备份、HA 或 outbox dispatcher；这些是部署能力，
  不影响本地事务 contract 完成。

---

## DEV-2026-08-12-11 — ST-03 签名身份、RBAC 与 Agent 凭据端口

- 状态：完成（本地签名 adapter + provider-neutral 生产端口）
- 类型：认证、授权、execution/approval proof 与最小凭据租约

### 背景与决策

自由文本 `actor_id`/`authorized_by` 不能证明身份。依据 ADR-0025，公共写入口必须在
任何 workflow、executor 或 Git 副作用前校验主体与动作；控制面签名 key 和长期凭据
不得交给 Agent。

### 当前落实

1. provider-neutral identity/RBAC 契约定义 observer、executor、reviewer、approver、
   worker-admin、operator-admin 与默认拒绝的动作矩阵。
2. 本地 HMAC adapter create-only 创建受保护 key，签发最长 24 小时 session；CLI/UI
   写入口改为必需 `--identity-session/--identity-key`，自由文本身份已从公共 schema 移除。
3. approval 和 execution authority 都包含可验证 proof；execution proof 额外绑定
   task/run/risk/action/time。篡改、过期、错角色、错 task/run 在副作用前拒绝。
4. `AgentCredentialBroker` 只返回无 secret 的短期 grant 证据；治理执行器要求 delegate
   证明实际绑定相同 grant，终态后精确 revoke。

### 变更、验证与限制

- 主要文件：`src/jobslayer/identity/`、`src/jobslayer/adapters/local_identity.py`、
  `src/jobslayer/domain/models.py`、`src/jobslayer/cli.py`、
  `src/jobslayer/supervision/`、`src/jobslayer/application/local_run.py`、
  `tests/test_identity.py`、`tests/test_review_ui.py`、`tests/test_supervision.py`、
  `tests/test_local_run.py`、`docs/adr/0025-authenticated-control-plane-and-agent-credential-grants.md`。
- 正反测试覆盖有效、篡改、过期、错 action/role/task/run 和越权零副作用；完整计数与统一
  命令结果记录在 DEV-14。
- 本地 HMAC 不冒充生产 IdP；OIDC/mTLS、撤销服务和真实短期模型 credential adapter
  后置。没有真实 broker 时，严格外部模型治理路径 fail closed。

---

## DEV-2026-08-12-12 — ST-04/ST-05 强隔离、worker、预算与上下文

- 状态：完成（Linux 强隔离；Windows 通用接口 fail closed）
- 类型：sandbox composition、worker lease、执行预算、上下文完整性与治理装饰器

### 背景与决策

完整主机权限与 Codex `workspace-write` 都不能作为安全边界。依据 ADR-0026，把 sandbox、
worker ownership、budget、context 和 credential 组合到 provider-neutral
`GovernedAgentExecutor`，缺少任一能力即拒绝启动。

### 当前落实

1. `SandboxLauncher` 产生可审计 launch plan；Codex adapter 可经 launcher 组合启动并
   报告 enforcement-backed capability。Linux bubblewrap 从空 root 启动，只读绑定运行时，
   只写单个 `/workspace`，默认新建网络 namespace。
2. `prlimit` 与进程监督器实施 CPU、内存、进程数、wall timeout 和进程树终止；真实 WSL
   测试额外证明 workspace 外 host secret 不可见，消除原先只读绑定整个 host root 的缺陷。
3. SQLite worker store 实现 acquire、heartbeat、cancel-requested、release 和 orphan
   expiry；取消信号只能在持久化 cancel-requested 之后发送。
4. budget store 在启动前 reserve/authorize attempt，按 normalized usage 增量扣减 token、
   cost 和 duration，超限持久化 exhausted 后取消。task/runbook 明确 cost、input/output
   token、context、attempt/repair 上限。
5. context builder 只接纳 admitted root 下普通文件，拒绝 symlink/escape/篡改/超大小，
   component/package 均带内容哈希和固定版本。

### 变更、验证与限制

- 主要文件：`src/jobslayer/workers/`、`src/jobslayer/governance/`、
  `src/jobslayer/adapters/linux_sandbox.py`、`src/jobslayer/adapters/sqlite_workers.py`、
  `src/jobslayer/adapters/sqlite_budgets.py`、`src/jobslayer/adapters/codex_cli.py`、
  `src/jobslayer/application/context_packages.py`、
  `src/jobslayer/application/budget_policy.py`、
  `src/jobslayer/application/governed_executor.py`、`tests/test_sandbox.py`、
  `tests/test_worker_leases.py`、`tests/test_governance.py`、
  `tests/test_governed_executor.py`、`docs/adr/0026-enforcement-backed-worker-sandbox-budget-and-context.md`。
- WSL 使用既有 `/var/tmp/jobslayer-sandbox-20260812` 普通用户测试工具运行 5 项真实隔离
  测试全部通过；未安装系统组件。最终复核命令和结果记录在 DEV-14。
- 原生 Windows 没有等价强 sandbox；同一接口明确报告能力缺口并拒绝强治理任务，可路由
  WSL/Linux worker。Dagger/OCI/Kubernetes 未由缺乏需求证据的本切片提前引入。

---

## DEV-2026-08-12-13 — ST-06/ST-07 管理面、遥测与执行器比较

- 状态：完成（本地查询/界面与确定性双 adapter 回归）
- 类型：只读 Agent Dashboard、持久查询、OpenTelemetry 端口与 contract comparison

### 背景与决策

管理面不能从 UI 猜测状态或直写数据库；执行器比较也必须先证明任务和验证契约相同。
依据 ADR-0027，以经过完整性校验的持久真相建立只读投影，并把 telemetry 保持为可选端口。

### 当前落实

1. `serve-dashboard`/`dashboard` 是 Windows/POSIX 统一入口，要求签名 view session、只绑定
   loopback、没有写 API。默认查询 Phase 0 run；传入已有 SQLite DB/artifact root 时只读
   Phase 1 workflow/run/artifact/outbox 真相且不自动迁移。
2. Dashboard 汇总状态、executor、usage/cost、review/decision；详情展示完整 transitions、
   run records、持久 events 和 artifacts。制品字节在展示前重新验 hash，intent-only 可见。
3. `TelemetrySink` 默认 no-op；可选 OpenTelemetry adapter 只记录稳定标量，不收集 prompt、
   credential 或 raw log。
4. `compare-executors` 先校验 task/profile hash 完全一致，再比较终态、usage/cost、时长和
   人工干预。当前第二 adapter 是 fake Codex CLI 合同回归，不冒充付费模型质量结果。

### 变更、验证与限制

- 主要文件：`src/jobslayer/management/`、`src/jobslayer/adapters/local_management.py`、
  `src/jobslayer/adapters/persistent_management.py`、`src/jobslayer/observability/`、
  `src/jobslayer/evaluation/`、`src/jobslayer/application/executor_comparison.py`、
  `src/jobslayer/cli.py`、`tests/test_management.py`、`tests/test_observability.py`、
  `tests/test_executor_comparison.py`、`tests/test_transactional_execution.py`、
  `docs/adr/0027-persistent-management-observability-and-executor-evaluation.md`。
- HTTP/认证、损坏证据拒绝、事务详情、no-secret telemetry 和 contract mismatch 均有正反
  测试；最终统一测试计数记录在 DEV-14。
- 远程多租户平台、生产 OTel exporter、Langfuse/Phoenix、Promptfoo 和第二个真实付费
  executor 后置；这些不是本地必要基础架构的完成条件。

---

## DEV-2026-08-12-14 — 短期必要基础架构收口与跨平台验收

- 状态：完成
- 类型：兼容性修复、文档收口、Windows/WSL/PostgreSQL/可视化验收

### 收口发现与修复

1. 首次预验收 `\.\jobslayer.cmd check` 执行了 205 项测试，其中 2 项错误来自
   `_cmd_review_decision` 误读取只属于 Dashboard 的参数。该 wiring 错误已移除；随后
   `python -m unittest tests.test_supervision tests.test_transactional_execution
   tests.test_management` 13 项全部通过。失败尝试未被隐藏或记作成功。
2. 真实启动 Phase 0 Dashboard 后发现 HTTP/认证正常，但现有 21-run 语料全部被标为
   invalid。原因不是证据损坏，而是当前 `AgentRunSpec`/execution authority 新增可选默认
   字段后，inspect 把 Pydantic 解析后的 intent 与历史 raw context 做了字典逐字比较。
3. 新增严格 `LocalExecutionContext`：历史 raw context 先经过当前 frozen/extra-forbid
   契约应用兼容默认值，再与 intent 做类型相等比较；未知字段、非默认语义差异和原哈希
   篡改仍拒绝，不重写任何历史记录。新增回归测试主动移除兼容默认字段后重建有效旧记录，
   证明 inspect 可读且完整性门禁仍通过。
4. 真实 loopback 复测结果为 21 个正常 run、0 个 invalid；详情实际返回 7 条 workflow
   transition、5 条 run record、16 个 artifact。session 来源为
   `persisted_local_events`，主体为只读 observer，`mutations=false`，CSP 存在。
5. 按 Browser 技能尝试连接实际页面，但当前环境没有可用浏览器实例，因而没有生成新的
   可视截图；没有绕过规范改用另一套浏览器自动化。HTTP/静态资源和 DOM 行为仍由真实
   服务检查及管理 UI 集成测试覆盖。此前 Dashboard 截图保留为历史视觉证据，不冒充本轮。

### 变更文件与架构记录

- 本阶段主要新增/修改：`src/jobslayer/persistence/`、`identity/`、`workers/`、
  `governance/`、`management/`、`observability/`、`evaluation/`，SQLite/PostgreSQL/
  sandbox/identity/management adapters，budget/context/governed/transactional application
  services，领域契约、统一 CLI、管理 UI、runbook/task 预算字段及对应测试。
- 文档已更新：`README.md`、`docs/ROADMAP.md`、
  `docs/SHORT_TERM_INFRASTRUCTURE_PLAN.md`、`docs/UNIFIED_ENTRYPOINT.md`、
  `docs/CODEX_INTEGRATION.md`、`docs/GOVERNED_EXECUTION_LOOP.md`、
  `docs/HUMAN_SUPERVISION.md`、`docs/VISUAL_REVIEW_UI.md`、
  `docs/MINIMUM_DEVELOPMENT_LOOP.md`、Phase 0/首次 Codex 历史说明及测试床说明。
- durable decisions：ADR-0024 至 ADR-0027，并已更新 ADR 索引；ADR-0023 的长时完整权限
  安全策略贯穿全部操作。旧 ADR/日志不改写，过时命令只保留在明确的历史记录中。

### 精确验证证据

- Windows 新基础架构定向命令：
  `\.venv\Scripts\python.exe -m unittest tests.test_sqlite_state
  tests.test_identity tests.test_worker_leases tests.test_governance
  tests.test_governed_executor tests.test_sandbox tests.test_observability
  tests.test_management tests.test_executor_comparison
  tests.test_transactional_execution -v`：55 项通过，5 项 Linux-only 跳过，用时
  6.619 秒。
- Dashboard 兼容回归：`\.venv\Scripts\python.exe -m unittest
  tests.test_local_run.LocalRunCoordinatorTests.test_inspect_normalizes_compatible_defaults_in_historical_context
  tests.test_management -v`：4 项全部通过，用时 2.639 秒；真实 query 另得
  `runs=21, invalid=0`。
- WSL sandbox：设置
  `JOBSLAYER_TEST_BWRAP=/var/tmp/jobslayer-sandbox-20260812/rootfs/usr/bin/bwrap` 后运行
  `/var/tmp/jobslayer-posix-foundation-20260812-01/venv/bin/python -m unittest
  tests.test_sandbox -v`：5 项全部通过，用时 2.537 秒。覆盖默认无网络、root 不可写、
  workspace 可写、host secret 不可见、CPU/内存/process/time 和超时后无孤儿。
- WSL PostgreSQL：仅在 `/var/tmp/jobslayer-postgres-contract-20260812` 启动既有普通用户
  PostgreSQL 16，设置 socket DSN 后运行 `python -m unittest tests.test_postgres_state
  -v`：11 项全部通过，用时 0.693 秒；同一 shell 的 EXIT trap 执行精确
  `pg_ctl -D ... -m fast -w stop`，输出 `server stopped`。
- WSL 统一 `JOBSLAYER_PYTHON=/var/tmp/jobslayer-posix-foundation-20260812-01/venv/bin/python
  JOBSLAYER_TEST_BWRAP=... ./jobslayer check`：7/7 通过，用时 35.7 秒。兼容修复后的同一
  WSL 测试集合以 `python -m unittest discover -s tests -q` 复核：206 项通过、1 项因
  PostgreSQL 不常驻而 skip，用时 27.376 秒。
- Windows 最终代码统一 `\.\jobslayer.cmd check`：7/7 通过，用时 99.9 秒；随后以
  `\.venv\Scripts\python.exe -m unittest discover -s tests -q` 精确计数：206 项通过、
  7 项因 POSIX 权限、Linux sandbox 和可选 PostgreSQL 环境而 skip，用时 97.576 秒。
- `git -c core.autocrlf=true diff --check` 返回 0；当前 8765/8770/55432 无监听，WSL
  PostgreSQL `pg_ctl status` 返回 `no server running`。没有系统安装、远端 push、部署、
  删除仓库/用户数据或向 Agent 暴露签名 key/secret。
- 本条日志落盘后再次执行封板门禁：Windows `\.\jobslayer.cmd check` 7/7 通过，
  用时 99.8 秒；WSL 以同一临时 venv 和真实 bubblewrap 执行 `./jobslayer check` 7/7
  通过，用时 39.5 秒。两次都包含 unittest、compileall、pip check、testbed、两套
  source-controlled runbook 和 normalized Git diff，不以部分命令替代。

### 完成边界与下一步

短期计划 ST-00 至 ST-07 的必要基础功能与架构已闭合。仍明确后置的是生产 OIDC/mTLS、
真实短期模型凭据 adapter、远程 worker/对象存储、备份/HA/outbox dispatcher、原生
Windows 强沙箱、自动 repair 编排、第二个真实付费 executor 和远程多租户平台。这些
需要部署选择、预算或外部调用授权，不以测试替身冒充本阶段完成证据。下一步建议在真实
部署目标确定后，先实现 OIDC + secret broker + Linux worker 的纵向切片，再授权第二
executor 评测；当前不会自动发起外部模型调用、push 或部署。

---

## DEV-2026-08-12-15 — Dashboard loopback 测试的代理隔离

- 状态：完成（本地门禁恢复；远端 Windows CI 仍待独立根因确认）
- 类型：测试确定性、环境兼容与 CI 诊断

### 背景与根因

在更新后复核中，不带环境修饰运行 `./jobslayer check` 得到 6/7：206 项测试中仅
Dashboard 的两个 HTTP 测试返回 502。当前宿主代理配置不会绕过 `127.0.0.1`，而
`tests/test_management.py` 直接使用全局 `urlopen`，因此本应命中进程内 loopback
server 的请求被发送到代理。既有 review UI 测试已通过空 `ProxyHandler` 明确隔离代理，
Dashboard 测试没有采用相同边界。

### 落实与验证

1. `tests/test_management.py` 改为建立 `build_opener(ProxyHandler({}))`，全部测试请求均
   通过该无代理 opener；产品服务、运行时网络策略和环境变量均未改变。
2. 在原始代理环境下运行 `.venv/bin/python -m unittest tests.test_management -v`：
   3 项全部通过，用时 1.023 秒。
3. 不设置 `NO_PROXY`/`no_proxy` 运行完整 `./jobslayer check`：7/7 通过；206 项测试
   `OK`，5 项因当前未配置 PostgreSQL DSN 或 bubblewrap 而 skip；compile、依赖、
   BraveNewWorld 测试床、两套 runbook 和 normalized Git diff 全部通过。
4. `git diff --check` 返回 0。变更文件仅为 `tests/test_management.py` 和本追加日志。

### 限制与下一步

GitHub Actions 对提交 `f310b1c` 的首次远端运行总体失败；公开摘要确认
`governed-corpus (windows-latest)` 在 `Run complete local gate` 失败，而
`postgres-contract` 成功。当前环境没有 GitHub CLI，公开页面又不提供完整失败日志，
因此不能把本地 loopback 修复声明为远端 Windows 根因或已修复。下一步需取得该 job 的
完整日志，按实际失败补充最小修复，并重新触发 Windows/Ubuntu/PostgreSQL 远端门禁。

### 补充：远端 Windows 日志根因与 UTF-8 公共入口修复

上述“完整日志尚不可用”限制随后由已连接 GitHub 应用的只读 Actions 日志接口补足，
不再作为当前根因结论。远端 206 项测试全部通过；唯一失败发生在开发门禁第 4 步：
GitHub Windows Server 2025 runner 的 stdout 为 CP1252，`validate-testbed` 输出中文
`purpose` 时触发 `UnicodeEncodeError`。Ubuntu corpus 与真实 PostgreSQL contract job
均成功，因此该失败是公共 CLI 输出编码边界，而不是领域契约或数据库问题。

1. `src/jobslayer/launcher.py` 在所有源码、模块和安装后公共入口调用 CLI 前，把可重配置的
   stdout/stderr 明确设为 UTF-8 strict；直接调用内部 `jobslayer.cli.main` 的单元测试不受
   全局副作用影响。
2. `tests/test_unified_entrypoint.py` 新增 CP1252 子进程回归：设置
   `PYTHONIOENCODING=cp1252` 后执行 `python -m jobslayer validate-testbed`，要求退出码为
   0、UTF-8 JSON 可解析且中文字段完整。
3. `.venv/bin/python -m unittest tests.test_unified_entrypoint
   tests.test_management -v`：10 项全部通过，用时 1.711 秒；另以同一 CP1252 环境真实
   管道解析测试床输出，得到 `BraveNewWorld True`。
4. 本次最终变更文件为 `src/jobslayer/launcher.py`、`tests/test_unified_entrypoint.py`、
   `tests/test_management.py` 和 `docs/DEVELOPMENT_LOG.md`。远端状态只有在这些本地变更经
   明确提交并重新触发 workflow 后才能更新；本轮没有擅自 commit、push 或重跑远端 job。
5. 日志补充落盘前运行最终 `./jobslayer check`：7/7 全部通过；207 项 unittest
   `OK`，5 项因当前未配置 PostgreSQL DSN 或 bubblewrap 而 skip；compile、依赖、测试床、
   两套 source-controlled runbook 与 normalized Git diff 全部通过，用时 19.063 秒。

---

## DEV-2026-08-13-16 — Web Workbench 交互指南与隔离 Stage 0 原型

- 状态：完成（交互原型与文档；无控制面接线）
- 类型：interaction architecture、frontend prototype、ADR、dependency adoption

### 背景与设计决定

外部《AI Collaboration Platform Interaction Framework Design Guide》建议以
React/TypeScript/Vite 建立 Web-first 工程工作台，并复用 React Flow、Monaco、xterm.js、
ECharts、Markdown/PDF 与 Tauri。直接照搬会与本仓库“JobSlayer 拥有工程真相”、现有
loopback UI 边界和按路线图退出条件引入依赖的规则发生歧义。

依据新增 ADR-0028，本轮只在 `ui-framework/` 建立不导入 Python、不注册 CLI/HTTP route、
不连接数据库/Agent/worker/Kernel 的 Stage 0 原型。React Flow 数据只作为 view model，
写按钮只改变浏览器组件状态并持续提示未提交；Workflow IR、权限、重试、验证、审计和完成
仍由未来 provider-neutral application contract 与 `WorkflowKernel.transition` 拥有。
PDF.js、Tauri 和 dock-layout 因当前没有实际示例/桌面退出条件继续后置。

### 当前落实

1. `docs/INTERACTION_DESIGN_GUIDE.md` 把原文章整理为项目级规范：文档优先级、工作台信息
   架构、GUI/Control Plane 权限边界、Workflow IR adapter、统一事件包络、query/command
   分离、快照/事件恢复、关键界面行为、无障碍/响应式基线、依赖采用矩阵、现有 UI 关系、
   Stage 0 至 governed command slice 的升级路径和评审检查表。
2. `ui-framework/` 提供总索引与 4 个可跳转示例：Workflow Studio 使用 React Flow 和只读
   Monaco YAML；Run Inspector 使用结构化任务层级、事件、ECharts trace 与只读 xterm；
   Artifact Review 使用 Markdown、JSON、Monaco Diff、验证/authority gate 和本地决定；
   Observability 展示成功/验证差异、成本、trace、worker 和可行动提醒。全局支持 hash 导航、
   小屏导航、`Ctrl/Cmd+K` 命令面板、明确焦点和 reduced-motion。
3. `package.json`/`package-lock.json` 锁定 React、Vite、TypeScript、`@xyflow/react`、
   `@monaco-editor/react`、`@xterm/xterm`、ECharts、`react-markdown` 和 `lucide-react`。
   页面级动态导入隔离 Monaco、React Flow、xterm 和图表；ECharts 只注册 Bar/Line/Pie、
   Grid/Tooltip/Legend 与 Canvas renderer。审计发现 Monaco 的精确间接依赖 DOMPurify 3.4.8
   存在公开问题，使用 npm override 固定为 3.4.13 后审计为 0 项漏洞。
4. `README.md` 增加交互指南与原型入口；`docs/ROADMAP.md` 将 Web-first 工作台标记为
   `[~]` 原型而非已接线产品；ADR 索引登记 ADR-0028；`.gitignore` 增加 Node/Vite 临时目录。
   现有 `src/jobslayer/supervision/ui`、`management/ui` 和所有 Python 内核代码未修改。

### 变更文件

- 根与文档：`.gitignore`、`README.md`、`docs/ROADMAP.md`、
  `docs/INTERACTION_DESIGN_GUIDE.md`、`docs/adr/README.md`、
  `docs/adr/0028-isolated-web-workbench-interaction-prototype.md`、本日志；
- 前端配置：`ui-framework/package.json`、`package-lock.json`、`tsconfig.json`、
  `vite.config.ts`、`index.html`、`README.md`；
- 前端实现：`ui-framework/src/App.tsx`、`main.tsx`、`styles.css`、`types.ts`、
  `mockData.ts`，以及 `components/Overview.tsx`、`WorkflowStudio.tsx`、
  `RunInspector.tsx`、`TerminalPanel.tsx`、`ArtifactReview.tsx`、`Observability.tsx`、
  `EChart.tsx`、`CommandPalette.tsx`。

### 精确验证证据

1. 使用 nodejs.org 当前 LTS `v24.19.0` 的临时便携运行时执行
   `npm install --no-audit --no-fund`；首次安装 141 packages，未在系统或仓库提交 Node
   runtime，`node_modules` 被忽略。
2. 在 `ui-framework/` 执行 `npm run check`：TypeScript 7 `tsc --noEmit` 与 Vite 8.2.1
   production build 均通过，2716 modules transformed；路由级 chunk 正常生成，无 build
   warning。`npm audit --audit-level=moderate` 返回 `found 0 vulnerabilities`。
3. 本地 Vite 仅绑定 `127.0.0.1:4173`；PowerShell `Invoke-WebRequest` 对 `/` 和经 HTML
   引用的 `/src/main.tsx` 均返回 HTTP 200，页面 title 存在。测试后服务已终止，4173 不作为
   常驻进程保留；`Get-NetTCPConnection -LocalPort 4173 -State Listen` 最终返回 0 个监听者。
4. 尝试通过已规定的应用内 Browser 做视觉/交互 QA，但当前 browser runtime 返回空可用
   列表，未生成截图，也未以另一套浏览器工具替代。该限制不被记录成视觉通过证据。
5. Windows 根统一门禁 `\.\jobslayer.cmd check`：7/7 通过，用时 108.9 秒；207 项 unittest
   `OK`，7 项因 POSIX 权限、未配置 PostgreSQL DSN 或 Linux bubblewrap 而 skip；compile、
   Python dependency consistency、BraveNewWorld testbed、scripted/Codex runbook 和 normalized
   Git diff 全部通过。
6. 日志落盘后的 `git diff --check` 返回 0；没有 whitespace error 或 conflict marker。

### 限制与下一步

当前页面使用固定 mock data，没有 read model、WebSocket、身份、幂等 command、并发恢复、
真实 PDF 或桌面能力；本轮也没有修改 AI 项目管理内核、commit、push 或部署。下一步推荐先
组织一次桌面/窄屏人工视觉评审并记录信息架构问题；若原型方向获准，再单独定义版本化只读
snapshot/event API，只消费现有完整性检查后的 run/event/artifact 真相。写命令继续后置到该
read-only vertical slice 证明不会形成第二控制平面之后。

---

## DEV-2026-08-13-17 — 跨平台、manifest 驱动的开发环境初始化

- 状态：完成（Windows 与 Linux x86_64 真实初始化；macOS/arm64 为契约覆盖）
- 类型：developer bootstrap、dependency integrity、cross-platform tooling、ADR

### 背景与决定

Stage 0 Web Workbench 引入 Node/npm 后，已有根 `jobslayer` / `jobslayer.cmd` 只会选择
Python，不能从未准备的 checkout 创建 `.venv`、检测 Node 或安装 lockfile 依赖。上一轮
使用的临时便携 Node 也不修改 shell `PATH`，使用者因而会看到 `npm` 命令不存在。

依据 ADR-0029，新增 `init.cmd` / `init.sh` 作为项目初始化入口，两者只发现 Python 并转发
到同一标准库实现 `scripts/bootstrap.py`。Python 3.11+ 是唯一系统前置条件；脚本不调用
winget/Homebrew/apt/sudo、不安装系统软件、不写用户 `PATH`。Node 先复用合格的显式或
系统 runtime，否则依据源码控制的 `bootstrap/toolchains.json` 下载固定 Node 24.19.0
LTS，验证 SHA-256 后安装到用户级、版本/平台隔离的 JobSlayer cache。

### 当前落实

1. Python：创建/复用仓库 `.venv`，执行 editable install、`pip check` 与 import probe；
   `pyproject.toml` 哈希和 optional extras 写入忽略的 state stamp。只允许显式
   `postgres`/`observability` extras，不改变全局 Python/pip。
2. Node：支持 Windows、Linux、macOS 的 x86_64/arm64 固定发行包；下载采用 `.part`、
   250 MiB 上限、完整 SHA-256 和原子发布。zip/tar 解压前拒绝路径穿越、绝对/越界链接、
   设备与 FIFO；无效显式 `JOBSLAYER_NODE` 不静默回退。
3. UI：只执行 `npm ci`，并以 `package.json`/`package-lock.json` 哈希、平台、Node 版本、
   state stamp 和完整 `npm ls --all` 判断 readiness。manifest 未变且依赖完整时第二次
   初始化不下载、不重装。Windows `EPERM/unlink` 会提示停止占用 checkout 的 Vite/Node，
   且绝不把半安装状态记为 ready。
4. 组合接口：`--check` 严格只读，`--check --json` 返回稳定 schema/退出码；支持
   `--offline`、`--skip-ui`、`--force`、`--extra` 和 `--tool-cache`。`--` 后只允许运行
   `python`、`jobslayer`、`node`、`npm` 参数数组，因此没有全局 npm 时仍可用
   `init -- npm --prefix ui-framework run dev/check`。
5. `.venv`/`node_modules` state 记录 host platform；同一物理 checkout 的 Windows/WSL
   混用失败关闭，要求独立 clone/worktree，避免原生依赖互相覆盖。用户级 Node cache 自身
   继续按平台隔离。
6. `docs/INITIALIZATION.md` 记录快速开始、默认 cache、检测/安装协议、JSON/退出码、
   离线/可选依赖、工具运行、安全恢复和 init/application 分层；README、Workbench README、
   unified-entrypoint 文档、路线图和 ADR 索引同步更新。

### 实际故障与修正

1. Windows 首次 `npm ci` 发现现有 Vite 进程占用 Rolldown 原生模块，返回真实
   `EPERM unlink`；初始化正确失败且未写 ready stamp。只停止了已核对 command line 指向
   本 checkout 的 Vite Node PID 30512，没有关闭用户 Terminal 或其他 Node 任务；随后
   `npm ci` 安装 138 packages 成功。错误信息已补充可操作恢复说明。
2. Linux 首次实机运行在 Node 包完成校验/解压后发现 npm probe 失败。根因是 POSIX npm
   使用 `/usr/bin/env node`，探测时尚未在子进程 `PATH` 加入同发行包的 `bin`。修正后只在
   probe/install 子进程环境注入该路径，不改变用户 shell；第二次 Linux 全流程通过。

### 变更文件

- 初始化实现：`init.cmd`、`init.sh`、`scripts/__init__.py`、
  `scripts/bootstrap.py`、`bootstrap/toolchains.json`；
- 测试：`tests/test_bootstrap.py`；
- 架构/文档：`docs/INITIALIZATION.md`、
  `docs/adr/0029-cross-platform-manifest-driven-development-bootstrap.md`、
  `docs/adr/README.md`、`docs/UNIFIED_ENTRYPOINT.md`、`docs/ROADMAP.md`、`README.md`、
  `ui-framework/README.md` 和本日志；
- 仓库文本/忽略规则：`.gitattributes`、`.gitignore`。

### 精确验证证据

1. Windows 真实初始化：`.\init.cmd` 创建/更新 `.venv`，下载并验证
   `node-v24.19.0-win-x64.zip`，发布到
   `%LOCALAPPDATA%\JobSlayer\toolchains\node\v24.19.0\windows-x86_64`，随后 `npm ci`
   安装 138 packages。立即第二次 `.\init.cmd` 输出 Python/Node/UI 三项 `[ready]`，未重装。
2. `.\init.cmd --check --json` 返回 `ready=true`、Python 3.12.10、Node 24.19.0、
   npm 11.17.0 和 `source=jobslayer-cache`；`.\init.cmd --offline` 在已有完整缓存/依赖下
   成功，证明无网络复用路径。`.\init.cmd -- node --version` 返回 `v24.19.0`。
3. `.\init.cmd -- npm --prefix ui-framework run check`：TypeScript `tsc --noEmit` 与
   Vite 8.2.1 production build 通过，2716 modules transformed；
   `.\init.cmd -- npm --prefix ui-framework audit --audit-level=moderate` 返回
   `found 0 vulnerabilities`。
4. Windows 定向测试
   `.\.venv\Scripts\python.exe -m unittest tests.test_bootstrap
   tests.test_unified_entrypoint -v`：16 项通过。覆盖支持/拒绝平台、版本下限、正常与路径
   穿越 archive、篡改离线缓存、显式 Node fail-closed、外平台/不完整 venv 拒绝、只读 JSON
   check、CMD/SH 行尾与既有统一入口。
5. WSL 只读当前 Windows checkout：
   `wsl.exe -e sh -lc "cd /mnt/d/projects/JobSlayer/JobSlayer &&
   sh ./init.sh --check --json"` 返回 1/`ready=false` 且未写文件，正确拒绝把 Windows
   `.venv`/`node_modules` 作为 Linux 环境。
6. WSL 独立 `/var/tmp/jobslayer-init-e2e-20260813-02` checkout 真实执行
   `sh ./init.sh`、`sh ./init.sh --check --json`、
   `sh ./init.sh -- npm --prefix ui-framework run check`：创建 Python 3.12.3 venv，下载并
   验证 `node-v24.19.0-linux-x64.tar.xz`，安装 Node 24.19.0/npm 11.17.0 和 138 packages，
   JSON `ready=true`，同一 2716-module Vite build 通过；EXIT trap 后
   `test ! -e /var/tmp/jobslayer-init-e2e-20260813-02` 通过，临时 venv/cache/dependencies
   已清理。
7. Windows 根统一门禁 `.\jobslayer.cmd check`：7/7 通过，用时 137 秒；216 项 unittest
   `OK`，7 项因 POSIX 权限、未配置 PostgreSQL DSN 或 Linux bubblewrap 而 skip；compile、
   Python dependency consistency、BraveNewWorld testbed、scripted/Codex runbook 和
   normalized Git diff 全部通过。
8. 日志落盘后的 `git diff --check` 返回 0；最终 `.\init.cmd --check --json` 经 Python
   管道断言四级 readiness 均为 true。当前 checkout 相关 Node 进程为 0，4173/5173 UI
   listener 合计为 0，没有为验证遗留常驻预览服务。

### 限制与下一步

本轮没有 macOS 或 arm64 实机；其平台映射、固定 archive/checksum 和拒绝路径由单元测试
覆盖，不能冒充实机验证。Python 3.11+ 仍需由宿主预先提供；这是 JobSlayer 的运行前提，
初始化不会以特权 package manager 安装。optional extras 在既有 venv 中是增量安装，若要
严格最小环境应删除 `.venv` 后重建。初始化没有修改 WorkflowKernel、运行状态、权限、审计、
identity、run/artifact 数据，也没有 commit、push 或部署。

下一步推荐把 `init --check --json` 接入 IDE workspace onboarding 和 GitHub Actions 的
环境诊断步骤，并在可用的 macOS x86_64/arm64 runner 上执行独立 checkout 的真实 init +
UI build；Node 版本/checksum 后续升级必须作为显式 toolchain 决策评审。

---

## DEV-2026-08-17-18 — UI_Framework 合并复核与外部 UI 栈统一门禁

- 状态：完成（本地依赖、production build 与统一门禁；远端 CI 待提交后触发）
- 类型：merge assessment、frontend dependencies、development gate、CI、ADR

### 合并事实与缺口

1. `git fetch --all --prune` 后确认 `main`/`origin/main` 同为 merge commit
   `dd50079`；GitHub PR #1 `UI_Framework -> main` 于 2026-08-17 02:19:36 UTC 合并，来源
   commit 为 `6c4001b`。合并新增 `ui-framework/`、交互指南、ADR-0028 与跨平台初始化。
2. Stage 0 已在实际组件中导入 React Flow、Monaco、xterm.js、ECharts、
   `react-markdown`、Lucide 和 React/Vite/TypeScript，但原根级 `jobslayer check` 只有 7 个
   Python/控制面步骤；GitHub matrix 也只执行 `pip install -e .`，前端可在完整门禁之外漂移。
3. 依据新增 ADR-0030，外部 UI 库仍只拥有通用展示能力，不进入 `jobslayer.domain`，也不把
   mock 写操作接入 Kernel；本轮只闭合依赖、production build 和完成信号，不越级宣称
   Stage 1 read model 或 governed command 已完成。

### 落实

1. 执行 `sh ./init.sh`，按 `bootstrap/toolchains.json` 下载并校验 Node 24.19.0，使用
   npm 11.17.0 和 `package-lock.json` 安装 138 packages。随后 `--check --json` 返回
   Python、Node 与 UI 全部 `ready=true`。
2. `DevelopmentCheckRunner` 新增第 4 个 `ui` 步骤，通过 bootstrap 的 `--offline` 路径运行
   `npm --prefix ui-framework run check`；检查期间不联网升级依赖，TypeScript 或 Vite bundle
   任一失败都会令根级完成门禁失败。对应单元测试锁定 8 步顺序、命令参数和汇总语义。
3. `phase0-corpus.yml` 的 Ubuntu/Windows matrix 先执行 manifest 初始化，再从同一 bootstrap
   环境调用 `jobslayer check`、语料构建与 readiness；PostgreSQL contract job 保持专用
   Python extra 和独立契约测试，不承担无关 UI 初始化。
4. 新增 ADR-0030，并同步统一入口、初始化、Workbench README、根 README、路线图和 ADR
   索引；明确外部 UI build 已进入完成门禁，但页面仍为 `PROTOTYPE · MOCK`。

### 变更文件

- 实现与 CI：`src/jobslayer/development/checks.py`、
  `tests/test_development_checks.py`、`.github/workflows/phase0-corpus.yml`；
- 架构与文档：`docs/adr/0030-unified-gate-for-locked-ui-dependencies.md`、
  `docs/adr/README.md`、`docs/UNIFIED_ENTRYPOINT.md`、`docs/INITIALIZATION.md`、
  `docs/ROADMAP.md`、`ui-framework/README.md`、`README.md` 和本日志。

### 精确验证证据

1. `sh ./init.sh -- npm --prefix ui-framework run check`：TypeScript 7 `tsc --noEmit` 和
   Vite 8.2.1 production build 通过，2716 modules transformed；路由级外部库 chunks 正常
   生成。
2. `sh ./init.sh -- npm --prefix ui-framework audit --audit-level=moderate` 返回
   `found 0 vulnerabilities`；`npm outdated --json` 返回 `{}`，合并时锁定的直接依赖没有
   可报告的新版本。
3. `.venv/bin/python -m unittest tests.test_development_checks tests.test_bootstrap -v`：
   12 项全部通过，用时 0.051 秒。
4. 文档落盘前运行 `./jobslayer check`：8/8 全部通过；216 项 unittest `OK`，5 项因当前
   未配置 PostgreSQL DSN 或 bubblewrap 而 skip；新增 UI 步骤离线复用已校验依赖并再次
   完成 2716-module production build，其余 compile、pip、testbed、两套 runbook 与 Git
   diff 步骤全部通过，用时 26.460 秒。
5. 全部 ADR/文档落盘后再次运行 `./jobslayer check`：8/8 全部通过；216 项 unittest
   `OK`、5 项 skip，UI production build 再次转换 2716 modules；完整命令退出码为 0，
   用时 18.973 秒。最终 `git diff --check` 也返回 0。

### 限制与下一步

本轮没有把 Stage 0 mock data 替换为真实 read model，没有增加 WebSocket、身份或写命令，
也没有执行浏览器视觉验收。`.github/workflows/phase0-corpus.yml` 的新初始化路径只有在这些
本地变更获得明确提交/push 后才会产生远端 Ubuntu/Windows 证据；本轮不自行 commit、push
或部署。下一步应先提交并观察双平台 CI，再单独设计 Stage 1 版本化只读 snapshot/event
vertical slice，继续证明 React 工作台不会形成第二控制平面。

---

## DEV-2026-08-17-19 — 版本化协作式任务编排框架

- 状态：完成（本地垂直切片、真实 HTTP/proxy 联调与统一门禁）
- 类型：task orchestration、application service、local adapter、authenticated API、UI、ADR

### 决策与边界

1. 采用 ADR-0031：任务编排是执行前的版本化规划工件，不是 `TaskState` 的替代物。Agent
   只能返回 provider-neutral 的完整候选图；用户必须显式应用候选、固化路径，且只有
   JobSlayer 应用服务可以追加新修订。规划完成不会触发执行、验证、审批或
   `WorkflowKernel.transition`，从而不建立第二控制平面。
2. 图契约保持为可扩展 DAG：节点支持 task、milestone、validation 和 human gate，边支持
   sequence、dependency、branch 和 subtask。每次修改都生成不可变修订，记录 actor、操作、
   前一条哈希和当前 SHA-256 哈希；finalized 修订可继续派生新的 draft，同时保留最近一次固化
   修订号，避免静默覆写已确认路径。
3. 本地首个 agent adapter 是确定性的 `local-planning-fixture-v1`，用于离线开发和可重复测试；
   它支持多轮细化、修改/删除、依赖、支线和子任务意图，但不冒充真实 Codex 集成。后续外部
   provider 必须继续位于 `PlanningAgent` 协议之后，并把 raw interaction 留作工件。

### 落实

1. 新增 provider-neutral 编排模型、图验证、store/agent 协议，以及单进程 JSONL 本地存储；
   存储采用逐计划 append-only hash chain、序号并发检查、临时文件原子发布和 `0600` 权限，
   读取时会验证全链并拒绝篡改历史。
2. 新增 `TaskOrchestrationService`：覆盖初始任务描述、Agent 候选、多轮讨论、显式应用、节点
   CRUD、branch/subtask 分裂、固化和固化后派生修改；所有写操作都要求 expected revision，
   stale writer 或候选基线不一致不会追加修订。
3. 新增 loopback-only JSON API 和 `serve-task-orchestration` CLI。API 使用进程随机
   `X-JobSlayer-Session`；启动时验证签名 identity session 与 `MANAGE_TASK_PLAN`，每次写操作
   检查 principal 仍有效和随机 token，限制 JSON 大小/字段并设置 no-store、nosniff、DENY、
   referrer 和 CSP 响应头；新增最小权限 `planner` role，observer 保持拒绝。
4. React Workbench 新增 Task Orchestration 顶级页面：任务输入、讨论线程、候选/已应用拓扑、
   节点选择与编辑、创建/删除、支线/子任务、显式应用、最终固化和修订/哈希历史均接到真实
   本地 API。React Flow 中的位置仅为展示布局，不持久化为工程事实；Vite 只代理指定 API
   前缀到 `127.0.0.1:8780`。
5. README、交互指南、统一入口、路线图、专门运行手册和 ADR 索引已同步；新增测试覆盖允许/
   拒绝权限、proposal 不直接修改、完整 CRUD/分裂/固化、并发冲突、候选基线冲突、DAG
   cycle 拒绝、hash-chain 篡改和认证 API 全流程。

### 变更文件

- 领域契约与应用服务：`src/jobslayer/orchestration/__init__.py`、
  `src/jobslayer/application/task_orchestration.py`；
- adapters/API/CLI/identity：`src/jobslayer/adapters/local_orchestration.py`、
  `src/jobslayer/adapters/local_planning_agent.py`、`src/jobslayer/orchestration/web.py`、
  `src/jobslayer/cli.py`、`src/jobslayer/identity/__init__.py`、
  `src/jobslayer/adapters/local_identity.py`；
- 测试：`tests/test_orchestration.py`、`tests/test_orchestration_web.py`、
  `tests/test_identity.py`；
- UI：`ui-framework/src/components/TaskOrchestration.tsx`、`ui-framework/src/App.tsx`、
  `ui-framework/src/components/CommandPalette.tsx`、`ui-framework/src/components/Overview.tsx`、
  `ui-framework/src/types.ts`、`ui-framework/src/styles.css`、`ui-framework/vite.config.ts`、
  `ui-framework/README.md`；
- 架构与文档：`docs/TASK_ORCHESTRATION.md`、
  `docs/adr/0031-versioned-collaborative-task-orchestration.md`、`docs/adr/README.md`、
  `docs/INTERACTION_DESIGN_GUIDE.md`、`docs/UNIFIED_ENTRYPOINT.md`、`docs/ROADMAP.md`、
  `README.md` 和本日志。

### 精确验证证据

1. `.venv/bin/python -m unittest tests.test_orchestration tests.test_orchestration_web
   tests.test_identity tests.test_unified_entrypoint -v`：27 项全部通过，用时 1.073 秒。
2. `sh ./init.sh -- npm --prefix ui-framework run check`：TypeScript `tsc --noEmit` 和
   Vite 8.2.1 production build 通过，2717 modules transformed；生成独立
   TaskOrchestration route chunk。
3. 真实本地端到端联调：在临时目录签发 `planner` identity session，启动
   `.venv/bin/python -m jobslayer serve-task-orchestration --port 8780` 和 Vite 4173；通过
   Vite `/api/orchestration` proxy 获取 session、创建 `plan-e2e` 并读取 history，确认首条
   `plan.created_with_agent_proposal` 修订、5 个候选节点、0 个已应用节点以及匹配哈希。随后
   停止两个服务，确认 4173/8780 无 listener，并清理临时身份、状态和日志目录。
4. 文档落盘前运行 `./jobslayer check`：8/8 全部通过；225 项 unittest `OK`，5 项因未配置
   PostgreSQL DSN 或 bubblewrap 而 skip；UI production build 转换 2717 modules；compile、
   dependency consistency、BraveNewWorld testbed、scripted/Codex runbook 和 Git diff 均通过，
   完整命令退出码为 0，用时 19.556 秒。
5. 本条开发日志落盘后再次运行 `./jobslayer check`：8/8 全部通过；225 项 unittest
   `OK`、5 项 skip，UI production build 再次转换 2717 modules；完整命令退出码为 0，
   用时 18.915 秒。随后 `git diff --check` 返回 0。

### 限制与下一步

当前没有真实 Codex/外部模型调用、provider raw-log artifact、图形拖拽布局持久化、通用边
CRUD、多人事务存储、生产静态资源托管或 plan-to-workflow IR 编译；本地 JSONL store 只承诺
单进程 writer。浏览器层完成了实际 HTTP/Vite proxy 联调和 production build，但没有执行
截图式视觉验收。固化路径仍是规划证据，不代表任务完成、通过验证或获批执行。

工作树同时保留前一轮尚未提交的 UI dependency unified-gate 变更和本轮编排变更；未获得
commit/push 授权，因此没有创建提交、推送分支或触发远端 CI。下一步建议在明确外部调用预算、
凭据和审计策略后，先实现 Codex planning adapter 与 raw interaction artifact，再设计
transactional plan store 和显式 plan-to-workflow compilation/approval 边界。

---

## DEV-2026-08-17-20 — 交互式任务规划工作台闭环完善

- 状态：完成（本地契约、真实 HTTP/Vite proxy 联调与统一门禁；截图式视觉验收受环境限制）
- 类型：task planning、application service、authenticated API、Workbench UI、兼容性、ADR

### 决策与边界

1. 采用 ADR-0032：在真实 Codex adapter 之前先稳定交互式规划契约和使用体验。新增结构化
   节点信息、确定性完整度评估、语义边 CRUD、proposal reject、revision derive 与
   archive/restore；所有写操作继续要求认证 planner、随机提交 token 和 `expected_revision`，
   并追加新哈希链记录。
2. plan assessment 只评估执行前设计质量。pending proposal、空图、归档状态和 validation
   节点缺少验证要求是 blocker；warning/info 提示缺少人工门、验收标准、交付物或存在孤立
   节点。finalization 拒绝 blocker，但不触发 `WorkflowKernel.transition`，也不声称执行验证通过。
3. React Flow 拖动坐标按 plan 存在浏览器 local presentation metadata，不进入
   `TaskPlanSnapshot`、JSONL 或未来 Workflow IR。Agent 候选仍以完整图应用或拒绝，不获得
   逐项写权限。

### 落实

1. `TaskPlanNode` 增加验收标准、交付物、约束、风险、验证要求和人工决策标记；新增
   `TaskPlanAssessment`/issue 契约与纯确定性评估。fixture 为初始五阶段图提供可用的结构化
   内容，多轮提案更新时保留这些字段。
2. `TaskOrchestrationService` 和 loopback API 新增 assessment、proposal reject、edge
   create/update/delete、历史 revision 派生和 archive/restore。归档计划只读；历史派生、
   归档和恢复都创建新 draft revision，保留 `latest_finalized_revision` 和完整历史。
3. 新字段采用默认值兼容旧记录。`LocalTaskPlanStore` 读取时先对磁盘原始 JSON 精确计算旧
   SHA-256，再进行当前模型升级和链校验，避免默认字段导致旧 revision 误报，同时不接受在
   旧记录中注入新字段而不更新哈希。
4. Workbench 新增多计划搜索/切换/归档、内联创建、proposal 图差异和应用/拒绝、完整度问题
   导航、结构化节点 Inspector、React Flow 通用连线与 edge Inspector、浏览器本地布局、
   revision compare 与从历史版本派生新草案。Workflow Studio 和执行内核保持隔离。
5. README、交互指南、路线图、任务编排手册和 ADR 索引已经同步；本轮复用既有 React Flow、
   React、Vite 和 Python 栈，没有修改 lockfile 或增加外部依赖。

### 变更文件

- 领域与应用：`src/jobslayer/orchestration/__init__.py`、
  `src/jobslayer/application/task_orchestration.py`；
- adapters/API：`src/jobslayer/adapters/local_orchestration.py`、
  `src/jobslayer/adapters/local_planning_agent.py`、`src/jobslayer/orchestration/web.py`；
- UI：`ui-framework/src/components/TaskOrchestration.tsx`、`ui-framework/src/taskPlan.ts`、
  `ui-framework/src/types.ts`、`ui-framework/src/styles.css`、`ui-framework/README.md`；
- 测试：`tests/test_orchestration.py`、`tests/test_orchestration_web.py`；
- 文档：`docs/adr/0032-governed-interactive-planning-workbench.md`、`docs/adr/README.md`、
  `docs/TASK_ORCHESTRATION.md`、`docs/INTERACTION_DESIGN_GUIDE.md`、`docs/ROADMAP.md`、
  `README.md` 和本日志。

### 精确验证证据

1. `.\.venv\Scripts\python.exe -m unittest tests.test_orchestration
   tests.test_orchestration_web -v`：13 项全部通过，用时 0.591 秒；覆盖 proposal 不直接改图、
   apply/reject、结构化节点、边 CRUD 与环拒绝、finalization blocker、归档只读、历史派生、
   stale revision、旧记录升级和原始哈希篡改拒绝。
2. `.\init.cmd -- npm --prefix ui-framework run check`：TypeScript `tsc --noEmit` 与 Vite
   8.2.1 production build 通过，2718 modules transformed；没有安装或升级依赖。
3. 真实本地纵向联调：在隔离临时目录签发 30 分钟 `planner` identity，启动
   `.\jobslayer.cmd orchestration-api --state-root <temp> --port 8780` 和 Vite 4173；所有请求经
   `/api/orchestration` same-origin proxy 执行。`plan-ui-e2e` 连续形成 10 个 revision，覆盖创建、
   应用五节点候选、讨论产生 `+1` 节点候选、拒绝后仍为五节点、edge create/update、从 v2
   派生、assessment ready、v8 finalization、v9 archive 和 v10 restore；最终
   `latest_finalized_revision=8`，history 数量为 10，末端 record hash 完全匹配。随后停止本轮
   进程，确认 4173/8780 无 listener，并删除临时 key/session/state/log。
4. 浏览器技能按本地 URL 完成连接恢复检查，但运行环境返回可用浏览器列表 `[]`，因此不能
   生成截图式布局和点击验收证据；没有改用无关浏览器后端冒充验收。
5. 文档落盘前运行 `.\jobslayer.cmd check`：8/8 全部通过；230 项 unittest `OK`、7 项因
   Windows/POSIX 权限、未配置 PostgreSQL DSN 或 bubblewrap 而 skip；UI production build
   转换 2718 modules，compile、dependency consistency、BraveNewWorld testbed、两套 runbook
   与 Git diff 检查全部通过；完整命令退出码为 0，用时 99.5 秒。
6. 本条开发日志落盘后再次运行 `.\jobslayer.cmd check`：8/8 全部通过；230 项 unittest
   `OK`、7 项 skip，UI production build 再次转换 2718 modules；完整命令退出码为 0，
   用时 98.6 秒。最终 `git diff --check` 返回 0。

### 限制与下一步

当前仍没有真实 Codex planning adapter、provider raw interaction artifact、逐项 proposal
合并、多人事务 store、生产静态托管或 plan-to-Workflow IR 编译。浏览器本地布局是可丢弃
展示数据；finalized plan 仍不代表任务已执行、验证、批准或完成。本轮没有修改
`WorkflowKernel`、执行状态、运行权限或 Agent runtime，也没有 commit、push 或部署。

下一步先在有可用浏览器的环境补一次桌面/窄屏截图式验收和键盘/可访问性检查；随后在明确
外部调用预算、凭据、raw-log 制品与审计策略后实现真实 Codex `PlanningAgent` adapter，最后
再设计显式、只预览的 plan-to-Workflow IR 确定性编译边界。

---

## DEV-2026-08-17-21 — 证据绑定的 Codex 规划适配器

- 状态：完成（假 CLI 契约、相关回归与统一完整门禁；未发起真实/付费模型调用）
- 类型：task planning、Codex adapter、artifact evidence、authenticated API、CLI、ADR

### 决策与边界

1. 采用 ADR-0033。`PlanningAgent` 现在返回非权威 `TaskPlanProposalDraft`；只有
   `TaskOrchestrationService` 可以分配 `proposal_id`、绑定 base revision、adapter 和时间，模型
   不能拥有权威 proposal envelope、计划 store 或应用/定稿决定。
2. `CodexPlanningAgent` 使用官方非交互 `codex exec` JSONL、output Schema 和 final-message
   文件，固定 read-only sandbox、ephemeral/ignore-user-config、单次调用、显式 model、超时和
   prompt/output 上限。它复用最小 Codex 环境和跨平台 `ProcessSupervisor`，不继承 ambient API key。
3. 每次已启动调用把精确 prompt、raw JSONL、stderr 和 final JSON 登记为四个内容寻址不可变
   制品。成功 draft 保存 invocation/artifact ID；失败制品仍可查询，但 provider 故障返回 `502`
   且不会创建或追加 plan revision。
4. adapter 输出先通过严格字段 Schema，再重建 provider-neutral node/edge 并验证长度、枚举、
   引用和 DAG 无环。JobSlayer 保留同 ID 节点已有 attributes。没有自动 retry，也没有把模型
   叙述、usage 或置信度当作应用、验证或完成证据。
5. `orchestration-api` 默认仍为离线 `LocalPlanningAgent`。真实 provider 必须同时指定
   `--planning-agent codex`、`--allow-external-planning-agent` 和显式 `--codex-model`；本轮只运行
   假 CLI，没有发起真实或付费调用，也没有增加 Python/npm 依赖。

### 变更文件

- 共享 Codex 进程边界与新 adapter：`src/jobslayer/adapters/codex_common.py`、
  `src/jobslayer/adapters/codex_cli.py`、`src/jobslayer/adapters/codex_planning_agent.py`；
- 规划契约/应用/API/CLI：`src/jobslayer/orchestration/__init__.py`、
  `src/jobslayer/adapters/local_planning_agent.py`、
  `src/jobslayer/application/task_orchestration.py`、`src/jobslayer/orchestration/web.py`、
  `src/jobslayer/cli.py`；
- 测试：`tests/test_codex_planning_agent.py`、`tests/test_orchestration_web.py`；
- Workbench evidence 提示：`ui-framework/src/types.ts`、
  `ui-framework/src/components/TaskOrchestration.tsx`、`ui-framework/src/styles.css`、
  `ui-framework/README.md`；
- 架构与文档：`docs/adr/0033-evidence-bound-codex-planning-adapter.md`、
  `docs/adr/README.md`、`docs/TASK_ORCHESTRATION.md`、`docs/INTERACTION_DESIGN_GUIDE.md`、
  `docs/ROADMAP.md`、`README.md` 和本日志。

### 精确验证证据

1. `codex exec --help`：本机 CLI 明确提供非交互 stdin prompt、`--json` JSONL、
   `--output-schema`、`--output-last-message`、`--ephemeral`、`--ignore-user-config`、`--model`、
   `--sandbox read-only` 和 `--cd`；该命令只读取帮助，没有调用模型。
2. `.\.venv\Scripts\python.exe -m unittest tests.test_codex_planning_agent
   tests.test_orchestration tests.test_orchestration_web tests.test_codex_cli
   tests.test_unified_entrypoint -v`：38 项全部通过，用时 7.245 秒。覆盖 JobSlayer-owned proposal、
   完整 raw artifact、ambient API key 隔离、显式 CLI gate、Schema/JSONL 拒绝、非零退出不重试、
   超时终止、file-backed output 上限、provider `502` 不落 revision，以及既有 Codex executor 回归。
3. `.\init.cmd -- npm --prefix ui-framework run check`：TypeScript `tsc --noEmit` 与 Vite 8.2.1
   production build 通过，2718 modules transformed；没有安装或升级依赖。
4. `git diff --check`：返回 0；仅显示既有工作树 CRLF/LF 转换提示，无 whitespace error。
5. 本条初稿落盘后运行 `.\jobslayer.cmd check`：8/8 全部通过；240 项 unittest `OK`、
   7 项因 Windows/POSIX 权限、未配置 PostgreSQL DSN 或 bubblewrap 而 skip；UI production
   build 转换 2718 modules，compile、dependency consistency、BraveNewWorld testbed、两套
   runbook 和 Git diff 检查全部通过；完整命令退出码为 0，用时 100.1 秒。
6. 本条首轮结果回填后再次运行 `.\jobslayer.cmd check`：8/8 全部通过；240 项 unittest
   `OK`、7 项 skip，UI production build 再次转换 2718 modules；完整命令退出码为 0，用时
   100.1 秒。随后仅回填本项结果，并再次运行 `git diff --check`。
7. 最终发现工作区实际位于 `main`，而约定的本地 `UI_Framework` 停在其严格祖先 `6c4001b` 且
   无独有提交；将本地分支安全快进到已验证基线 `35a66cd` 后切换到 `UI_Framework`。未 commit、
   push 或修改 `origin/UI_Framework`，所有未提交变更原样保留。

### 限制与下一步

当前没有执行真实/付费 Codex planning smoke test。CLI 能强制显式 model、单次调用、时间和 I/O
上限，但当前 Codex CLI 接口不能由本 adapter 预先强制美元/token 上限；生产启用前仍需明确
账户预算、短期凭据/授权和制品保留/访问策略。`CODEX_HOME` 登录上下文是当前允许的认证来源，
ambient `OPENAI_API_KEY` 会被剥离。

失败制品目前只能通过本地 registry 查询，Workbench 只显示 invocation 和 artifact 数量，尚无
通用 Artifact Viewer API。多人事务 store、逐项 proposal 合并和 plan-to-Workflow IR 仍未实现；
finalized plan 仍不代表执行、验证、批准或完成。下一步在用户明确真实调用预算后，对隔离的临时
计划执行一次 read-only smoke test 并复核四类制品，然后优先把 planning artifacts 接到只读
Artifact Viewer，而不是扩大模型权限。

---

## DEV-2026-08-19-22 — 认证、有界的规划制品查看器

- 状态：完成（本地应用/API/UI 垂直切片、真实 CLI/API 联调与统一完整门禁）
- 类型：planning evidence、artifact query、authenticated read API、Workbench UI、ADR

### 决策与边界

1. 采用 ADR-0034。规划证据通过独立 `PlanningArtifactQuery` application service 读取既有
   `ArtifactRegistry`，不让 HTTP handler 或浏览器直接访问 backing file URI，也不增加制品修改、
   删除或工作流命令能力。
2. 查询只允许 ADR-0033 定义的 prompt、raw JSONL、stderr 和 final JSON 四类
   `task_plan.agent.*` 制品，并同时绑定 `plan_id`。registry 会对完整对象复核大小和 SHA-256；
   浏览器最多接收 1 MiB UTF-8 文本预览以及 COMPLETE/TRUNCATED 标记。
3. 两个只读 GET 仍要求未过期 planner session 和进程随机 `X-JobSlayer-Session`。公共 descriptor
   仅包含 artifact/invocation ID、类型、producer、大小、哈希、时间和 metadata，明确排除宿主
   `uri`。制品文本继续按不可信纯文本渲染，不能成为 proposal 应用、验证或完成证据。

### 落实

1. 新增 `PlanningArtifactDescriptor`、`PlanningArtifactPreview` 和 query service；实现允许类型
   过滤、plan binding、稳定排序、完整性失败关闭、去 URI 投影和有界预览。
2. task orchestration loopback API 新增
   `GET /plans/{plan_id}/artifacts` 与
   `GET /plans/{plan_id}/artifacts/{artifact_id}`，session capability 明确报告 viewer 是否配置；
   CLI 无论使用 local fixture 还是显式 Codex adapter，都为同一 artifact root 配置只读 query。
3. Interactive Task Planning 顶栏新增“规划证据”入口。查看器列出四类证据、invocation、大小与
   SHA-256，并显示哈希验证后的纯文本；本地 fixture 的空列表明确说明不会伪造模型证据。
4. README、编排手册、交互指南、路线图和 ADR 索引已同步。本轮复用现有 React/Lucide/CSS 和
   artifact registry，没有修改 lockfile、增加基础设施依赖或调用外部模型。

### 变更文件

- 应用/API/CLI：`src/jobslayer/application/planning_artifacts.py`、
  `src/jobslayer/orchestration/web.py`、`src/jobslayer/cli.py`；
- 测试：`tests/test_planning_artifacts.py`、`tests/test_orchestration_web.py`；
- Workbench：`ui-framework/src/components/TaskOrchestration.tsx`、
  `ui-framework/src/types.ts`、`ui-framework/src/styles.css`、`ui-framework/README.md`；
- 架构/文档：`docs/adr/0034-authenticated-bounded-planning-artifact-viewer.md`、
  `docs/adr/README.md`、`docs/TASK_ORCHESTRATION.md`、
  `docs/INTERACTION_DESIGN_GUIDE.md`、`docs/ROADMAP.md`、`README.md` 和本日志。

### 精确验证证据

1. `.venv/bin/python -m unittest tests.test_planning_artifacts
   tests.test_orchestration_web tests.test_codex_planning_agent
   tests.test_unified_entrypoint -v`：25 项全部通过，用时 2.476 秒。覆盖允许类型过滤、去 URI、
   plan binding、1 MiB 截断、篡改拒绝、GET token、错误 plan 404，以及 Codex/CLI 既有回归。
2. `sh ./init.sh -- npm --prefix ui-framework run check`：TypeScript `tsc --noEmit` 与 Vite
   8.2.1 production build 通过，2718 modules transformed；TaskOrchestration lazy chunk 为
   32.46 kB，未安装或升级依赖。
3. 真实 CLI/API 联调：在 `/tmp/jobslayer-planning-viewer-e2e.*` 签发短期 planner identity，
   启动 `./jobslayer orchestration-api`，创建 `plan-viewer-e2e`，通过真实
   `LocalArtifactRegistry` 登记 final-output 制品，再用带 session token 的两个 GET 校验列表和
   内容。断言 artifact 数量为 1、`content_verified=true`、`truncated=false`、内容匹配且列表/
   preview 均无 `uri`；随后 TERM 本轮服务、确认 8780 无 listener 并删除全部临时文件。
4. `git diff --check`：返回 0。
5. 本条开发日志落盘后运行 `./jobslayer check`：8/8 全部通过；244 项 unittest `OK`，
   5 项因未配置 PostgreSQL DSN 或 bubblewrap 而 skip；UI production build 转换 2718
   modules，compile、dependency consistency、BraveNewWorld testbed、scripted/Codex runbook 和
   Git diff 均通过；完整命令退出码为 0，用时 20.590 秒。随后再次运行
   `git diff --check` 返回 0。
6. 最终复核时将可选的 artifact list refresh 与核心 plan accept/load 成功路径解耦：读取证据
   失败只进入 viewer 自身的错误状态，不再把已经成功的计划命令表现为失败。该边界调整后再次
   运行 `./jobslayer check`：8/8 全部通过；244 项 unittest 用时 19.189 秒，`OK
   (skipped=5)`；UI production build 转换 2718 modules；完整命令退出码为 0，用时 21 秒。
   随后运行 `git diff --check` 返回 0。

### 限制与下一步

当前查看器只覆盖已有 plan ID 的 Codex 规划文本证据，不是通用 run Artifact Review read
model；首次创建在 revision 落盘前失败时留下的未知/孤立 plan 证据仍只能直接查询 registry。
没有 raw download/range、二进制/PDF、远程对象存储、retention policy 或 operator-wide failure
index。预览上限为 1 MiB，完整对象仍会先读取并校验。顶级 Artifact Review 页面继续使用固定
样例；本轮完成 TypeScript/build 和 HTTP 联调，但没有浏览器截图式视觉验收。

本轮没有执行真实/付费 Codex 调用，没有修改 WorkflowKernel、TaskState、执行权限、验证或完成
规则，也没有 commit、push 或部署。下一步仍需用户明确 `--codex-model`、单次调用预算和登录/
制品保留策略后运行一次隔离 read-only Codex planning smoke test；若继续避免外部调用，则先设计
operator-wide 失败制品索引或 plan-to-Workflow IR 的确定性只预览编译契约。

## DEV-2026-08-19-23 — GPT-5.6 Sol/xhigh 真实规划 smoke 首次尝试

- 状态：受阻（本机 Codex CLI 版本不支持目标模型；等待操作者批准升级后重试）
- 类型：explicit model policy、reasoning effort、real external smoke、failure evidence
- 关联决策：[ADR-0033](adr/0033-evidence-bound-codex-planning-adapter.md) 的
  “2026-08-19 补充：显式 reasoning effort”

### 决策与实现

1. 操作者明确授权使用本机 `CODEX_HOME` 登录、`gpt-5.6-sol`、`xhigh` 和合理的单次调用预算。
   首次 smoke 采用 1 次调用、300 秒 wall timeout、read-only sandbox、ephemeral session、无重试；
   prompt 实际为 2,055 bytes。API 等价预算参考按约 20k input + 8k output tokens 估算不超过
   0.35 美元，但 Codex 订阅调用不等同于逐 token API 扣费，且当前 CLI 不能预先强制美元/token
   上限，因此硬护栏仍是调用次数、timeout、prompt/output bytes 和结构化 Schema。
2. 发现 adapter 固定 `--ignore-user-config` 后不会继承本机 `model_reasoning_effort`。新增可选
   `--codex-reasoning-effort`，只允许 `none/low/medium/high/xhigh/max`，并以经过 strict-config
   验证的单次 `model_reasoning_effort=...` override 传入 Codex CLI；登录上下文仍来自本机
   `CODEX_HOME`。省略参数时继续使用模型默认值，不隐式读取 ambient 行为配置。
3. 测试补充 xhigh 参数传递、CLI parser 和非法 effort 拒绝；编排手册与 ADR-0033 已同步。

### 真实 smoke 证据

1. 本机预检：`codex-cli 0.142.4`；本机用户级 npm registry 查询到当前
   `@openai/codex 0.148.0`。启动隔离 loopback API 时显式传入
   `--codex-model gpt-5.6-sol --codex-reasoning-effort xhigh
   --codex-timeout-seconds 300`，服务正常启动。
2. 向 `POST /api/orchestration/plans` 仅提交一次
   `plan-real-codex-smoke-001`。约 12 秒后 adapter 返回 502，未重试。raw JSONL 明确返回 HTTP
   400：`gpt-5.6-sol` 要求更新版本的 Codex CLI；同时报告旧 models cache 缺少
   `base_instructions`。没有 `turn.completed` usage，故没有可报告 token 消耗。
3. 失败调用仍登记 prompt、raw events、stderr、空 final output 四类不可变制品，完整读取后大小/
   SHA-256 全部验证通过。隔离 `LocalTaskPlanStore.list_plan_ids()` 为空，证明失败没有创建权威
   revision。服务已正常停止并确认 8781 无 listener。
4. 受限现场保留在 `/tmp/jobslayer-codex-smoke.XidGPg`：目录 mode 700，identity key/session mode
   600。升级/重试获批前不删除，以保留可核验失败证据。

### 变更与验证

- 变更：`src/jobslayer/adapters/codex_planning_agent.py`、`src/jobslayer/cli.py`、
  `tests/test_codex_planning_agent.py`、`docs/TASK_ORCHESTRATION.md`、
  `docs/adr/0033-evidence-bound-codex-planning-adapter.md` 和本日志。
- `.venv/bin/python -m unittest tests.test_codex_planning_agent
  tests.test_unified_entrypoint -v`：17 项全部通过，用时 2.159 秒。
- `git diff --check`：返回 0。

### 当前阻塞与下一步

更新用户级全局 Codex CLI 会修改仓库外环境，不在首次单次模型调用授权内。等待操作者明确批准
将 `/home/fangzhou/.npm-global` 中的 `@openai/codex` 从 0.142.4 更新至 0.148.0；批准后先验证
版本和登录可用性，再按同一模型/effort/300 秒护栏只重试一次，核对 usage、四类制品、pending
proposal、空权威图与 append-only revision，最后运行完整 `./jobslayer check`。

## DEV-2026-08-19-24 — GPT-5.6 Sol/xhigh 真实规划 smoke 成功闭环

- 状态：完成（更新后单次重试、真实认证 API/制品/审计核验与本地回归）
- 类型：real Codex planning、subscription login、structured proposal、budget evidence
- 纠正/承接：DEV-2026-08-19-23 的版本阻塞已由操作者更新本机 CLI 后解除；该失败记录不重写
- 关联决策：[ADR-0033](adr/0033-evidence-bound-codex-planning-adapter.md) 的
  “2026-08-19 实测补充：GPT-5.6 Sol/xhigh”

### 更新预检与调用护栏

1. `codex --version` 返回 `codex-cli 0.148.0`，`codex login status` 返回
   `Logged in using ChatGPT`，用户级 npm 安装也确认 `@openai/codex@0.148.0`。没有读取或输出
   登录凭据。
2. 沿用 DEV-23 的隔离目录和同一 plan ID，只授权一次重试。真实服务显式配置
   `gpt-5.6-sol`、`xhigh`、300 秒 timeout、read-only sandbox、ephemeral、strict config、
   `--ignore-user-config` 和本机 `CODEX_HOME` 登录；没有自动或并行重试。
3. 输入仍是“只规划、不实施”的 operator-wide 规划失败制品索引任务，明确 JobSlayer-owned
   state/completion、append-only audit、provider-neutral domain、无新基础设施依赖、确定性验证
   和人工确认约束。

### 成功证据与控制面结果

1. 唯一重试约 202 秒成功返回 `plan.created_with_agent_proposal`，首条 revision sequence 为 1。
   proposal 由 `codex-cli-planning-v1` 生成 11 个节点和 16 条边，包含 2 个 validation 节点与
   1 个 `requires_human_decision=true` 的 human gate；领域重建已通过字段、引用与 DAG 校验。
2. revision snapshot 的权威 nodes/edges 均为空，只保存 pending proposal；没有执行
   proposal apply/reject/finalize。确定性 assessment 只返回 `proposal.pending` blocker，
   `ready_to_finalize=false`。history 只有 sequence 1、`previous_hash=null`，读取时 record hash
   重新验证通过。
3. 成功 invocation 为 `planning-c406595badc44b819d196498dbad4365`，proposal 精确绑定它的四个
   artifact ID。连同 DEV-23 的失败 invocation，共 2 次 invocation/8 个制品/49,986 bytes；
   每组均精确包含 prompt、raw JSONL、stderr、final output。通过真实认证 artifact GET 与本地
   registry 完整读取双重验证大小和 SHA-256；descriptor/preview 均不包含 backing `uri`。
4. `turn.completed` usage 为 input 15,254、cached input 0、cache-write input 0、output 9,402、
   reasoning output 4,142 tokens。按官方 API 公布的每百万 input 5 美元/output 30 美元只作等价
   估值约 0.35833 美元；ChatGPT 月订阅的实际额度/计量不由 JobSlayer 推断。output 略高于预估
   8k，证明当前调用次数、timeout、bytes/Schema 是硬护栏，而 token/美元仍只是外部授权预算。
5. 成功后同一 plan ID 使 plan-bound viewer 能同时列出首次失败与本次成功的 8 个制品；这不
   解决“只有失败、从未形成 plan revision”时的 operator-wide 孤立制品发现问题。
6. API 服务已正常停止，并确认 8781 无 listener。受限现场继续保留在
   `/tmp/jobslayer-codex-smoke.XidGPg`；目录 mode 700，短期 identity key/session mode 600。

### 文档与验证

- `docs/TASK_ORCHESTRATION.md`、`docs/ROADMAP.md`、
  `docs/INTERACTION_DESIGN_GUIDE.md` 和 ADR-0033 已从“尚未真实 planning smoke”更新为本次
  证据，同时保留默认 local fixture、显式 opt-in 和生产预算/凭据/保留限制。
- `.venv/bin/python -m unittest tests.test_codex_planning_agent
  tests.test_planning_artifacts tests.test_orchestration_web
  tests.test_unified_entrypoint -v`：26 项全部通过，用时 2.669 秒。
- `git diff --check`：返回 0。

### 限制与下一步

本次只证明一个显式授权的 planning proposal 路径，不把外部调用加入离线 CI，不应用模型提案，
也不把计划当成执行/验证/批准/完成。当前 Codex CLI 仍没有由该 adapter 预先强制的 token/美元
上限；模型/CLI 兼容关系由外部服务演进，项目没有根据一次失败硬编码版本映射。现场在 `/tmp`
且没有生产 retention/远程对象存储。下一步优先实现 operator-wide 失败制品索引，或把已确认
计划确定性编译为只预览 Workflow IR；仍未执行 commit、push 或部署。

### 最终统一门禁

- 本条日志落盘后运行 `./jobslayer check`：8/8 全部通过；245 项 unittest 用时 19.235 秒，
  `OK (skipped=5)`；UI production build 转换 2718 modules；compile、dependency consistency、
  BraveNewWorld testbed、scripted/Codex runbook 和 Git diff 全部通过。完整命令退出码为 0，用时
  21 秒。随后再次运行 `git diff --check` 返回 0。

## DEV-2026-08-19-25 — 小时级 Agent 工作的本地可恢复控制面

- 状态：完成（provider-neutral 契约、SQLite event/checkpoint、lease 恢复和确定性门禁）
- 类型：long-running execution、multidimensional budget、checkpoint、recovery、ADR
- 关联决策：[ADR-0035](adr/0035-provider-neutral-resumable-long-running-control-plane.md)

### 决策与边界

1. 不把 token、工具调用、墙钟或订阅费用互相换算。policy 对 task/attempt elapsed、model/tool/wait、
   input/output token、cost 和 tool calls 分别声明 hard、soft、observe-only 或 unavailable；
   JobSlayer 本地派生 task/attempt elapsed 两个 hard gate，非 metered 账户禁止 cost hard gate。
2. 每个 attempt 在任何 provider side effect 前持久化唯一 `provider_start_key`。adapter 必须用
   `start_or_locate` 幂等启动或找回同一外部 run；explicit retry 才增加 attempt 并生成新 key，
   orphan takeover 不增加 attempt。
3. start 与 observation 必须先登记绑定 task/run 且哈希可验证的 raw artifact。normalized cursor、
   usage 或 provider terminal status 不能脱离原始证据改变控制面状态。
4. 新的 long-run status 只表示运行控制面，不是 `TaskState`，不会调用或绕过 WorkflowKernel、
   verification、approval 和 completion。checkpoint 同样只证明进度，不证明任务完成。
5. 采用已有 SQLite、`LocalArtifactRegistry` 和 `SqliteWorkerLeaseStore`，不增加依赖。事件按连续
   sequence 和 SHA-256 chain 追加；event/checkpoint update/delete 由 trigger 拒绝，projection
   读取时与 event truth 复核。
6. 推荐本地订阅基线记录为 task 8h hard、attempt 3h hard、2 attempts、90s lease、30s 内 poll、
   15min checkpoint、20min stall warning、output 100k/tool calls 400 soft，其他来源指标只观测，
   cost unavailable。它是可收紧的起点，不是一次 prompt 的固定或账户侧配额。

### 落实与变更文件

1. `src/jobslayer/long_running/__init__.py` 新增 policy、usage、persisted start request、provider
   reference/observation、run handle、checkpoint、hash-chained event、store 和 resumable adapter
   contracts。
2. `src/jobslayer/application/long_running_execution.py` 实现 admit/start request/bind/observe/
   checkpoint/cancel/finish/recover/explicit retry；hard overage 先持久化 lease cancellation，
   takeover 要求旧 lease 过期、provider identity/证据/单调 cursor 与预算全部成立。
3. `src/jobslayer/adapters/sqlite_long_runs.py` 实现 WAL/FULL synchronous SQLite projection、
   append-only events/checkpoints、optimistic version、hash-chain 和篡改失败关闭；
   `src/jobslayer/workers/__init__.py` 在 lease port 暴露恢复所需的只读 `get`。
4. `tests/test_long_running_execution.py` 新增 10 项测试，覆盖有效/无效 policy、完整 lifecycle、
   start key 崩溃窗口、provider identity、允许/拒绝转换、hard cancel、soft/stall warning、artifact
   ownership、restart orphan takeover、bounded retry、append-only trigger 和 projection tamper。
5. 新增 `docs/LONG_RUNNING_EXECUTION.md` 与 ADR-0035，并同步 README、ADR 索引、路线图和任务编排
   手册。现有规划 adapter 没有被冒充成支持 start-or-locate。

### 精确验证证据

1. `.venv/bin/python -m unittest -v tests.test_long_running_execution`：10 项全部通过，用时
   2.528 秒。
2. `.venv/bin/python -m unittest -v tests.test_long_running_execution
   tests.test_worker_leases tests.test_governed_executor tests.test_transactional_execution`：21 项全部
   通过，用时 4.706 秒，覆盖新增控制面和既有 lease/governed/transactional 回归。
3. `.venv/bin/python -m compileall -q src tests` 与 `git diff --check`：均返回 0。
4. 本条日志落盘后运行 `./jobslayer check`：8/8 全部通过；255 项 unittest 用时
   21.422 秒，`OK (skipped=5)`；UI production build 转换 2718 modules；compile、dependency
   consistency、BraveNewWorld testbed、scripted/Codex runbook 和 Git diff 全部通过。完整命令
   退出码为 0，用时 23.2 秒。

### 限制与下一步

本轮没有外部模型调用、自动 retry、后台 daemon/scheduler、远程对象存储或分布式依赖。long-run
event store 与 worker lease store 当前是两个本地 SQLite 事务边界；bind 有补偿，但两个提交间的
进程崩溃仍要 reconciliation，不能宣称 exactly-once。具体 executor 只有实现持久
`start_or_locate` 才能宣称可恢复；当前同步 `CodexPlanningAgent` 中断后不能自动 attach。

下一步按用户确认的方案 prompt 验证“输入/讨论 → Codex pending proposal → DAG/assessment/raw
artifacts → 用户 apply/finalize”的任务图闭环。首次验证不自动应用、不自动重试，也不把 plan
finalized 当成 workflow completed；长任务装配与 prompt→图产品闭环分别验收。

## DEV-2026-08-19-26 — 悬挂系统方案 prompt 到可视任务图真实闭环

- 状态：完成（真实 pending proposal、制品/审计核验与 Workbench 交互端在线）
- 类型：real planning prompt、task graph、artifact verification、interactive Workbench
- 关联决策：[ADR-0033](adr/0033-evidence-bound-codex-planning-adapter.md)、
  [ADR-0034](adr/0034-authenticated-bounded-planning-artifact-viewer.md)；长任务边界继续遵循
  [ADR-0035](adr/0035-provider-neutral-resumable-long-running-control-plane.md)

### 调用授权与护栏

1. 操作者提供精确任务描述“开发一个悬挂系统可视化案例。”并要求随后开启可视化交互端。本轮
   只授权生成任务图，不授权 apply、finalize、执行节点、修改案例代码或把计划标记完成。
2. 官方模型页面确认 `gpt-5.6-sol` 支持 structured outputs，reasoning effort 包含 `xhigh`；
   本机 `codex-cli 0.148.0` 与 ChatGPT login status 正常。真实 API 显式配置 model
   `gpt-5.6-sol`、effort `xhigh`、read-only、ephemeral、单次调用和 900 秒 timeout；无自动 retry。
3. 规划状态隔离在 `.jobslayer/orchestration-suspension-demo`，使用 480 分钟短期 planner session；
   API 仅绑定 `127.0.0.1:8780`，Vite 仅绑定 `127.0.0.1:4173`。
4. 首次本地 HTTP 客户端错误继承代理环境，使 `/session` 在进入 JobSlayer 前返回 `502`；artifact
   数量为 0，证明没有启动 provider 或消费模型调用。诊断后立即重启 API 轮换进程随机提交 token，
   并用显式 no-proxy 客户端提交；这不是模型 retry。

### 闭环结果与证据

1. 唯一 provider invocation `planning-73af8530ed7f41fbb2218782fdbee021` 约 108 秒成功。
   plan `suspension-visualization-case-20260819` 追加 sequence 1、operation
   `plan.created_with_agent_proposal`，record hash 为
   `157d3bdb0724b06224f972e3d78bfc682f45f2aa2bda9e85d7e7e4f35b5e86d2`。
2. proposal `proposal-988ab7290dab47ba954a6dcda2c6bb03` 包含 11 nodes/11 edges，其中
   3 个 validation、2 个 human gate，边为 7 条 sequence 和 4 条 dependency。范围门禁建议先
   确认被动式四分之一车辆二自由度案例，再规划确定性模型、交互可视化、测试、文档、完整验证和
   授权完成决策。
3. revision 的权威 nodes/edges 均为 0；assessment 只有 `proposal.pending` blocker，
   `ready_to_finalize=false`。没有 apply/reject/finalize，没有调用 WorkflowKernel 或执行任何节点。
4. 同一 invocation 精确登记四类制品，共 42,112 bytes；prompt 1,009 bytes、raw JSONL
   21,189 bytes、stderr 0 bytes、final JSON 19,914 bytes。registry 完整读取和认证 viewer preview
   均复核大小/SHA-256，proposal evidence ID 与四个 manifest 一致，公共 descriptor/preview 无
   backing `uri`。
5. `turn.completed` usage 为 input 15,014、cached/cache-write input 0、output 6,421、reasoning
   output 1,552 tokens。该数字只记录一次调用用量，不推断 ChatGPT 月订阅美元余额。
6. Workbench `http://127.0.0.1:4173/#/orchestration` 已启动；通过 Vite 同源代理读取 session、plans
   和上述 11 节点 proposal 均为 HTTP 200，agent adapter 为 `codex-cli-planning-v1`，artifact
   viewer capability 为 true。API 与 UI 在本条完成时保持运行，供操作者交互审查。

### 验证与限制

1. `LocalTaskPlanStore.history(plan_id)` 重新验证 sequence、previous hash 与 record hash；独立脚本
   断言权威空图、proposal 11/11、四类 manifest 集合、证据 ID、完整内容 hash、prompt 原文、
   assessment blocker 和 viewer 去 URI 全部一致。
2. UI 端到端探测断言根页面、代理 session、plans 均为 HTTP 200，目标计划唯一且 pending nodes
   为 11；`ss` 确认只有 loopback 4173/8780 listener。
3. 本次没有把同步 `CodexPlanningAgent` 接成 ADR-0035 的 `start_or_locate` adapter；控制器或本地
   CLI 中断仍不能宣称跨重启 attach。当前成果是 prompt→pending DAG→证据→人工审查闭环。
4. 本条日志落盘后运行 `./jobslayer check`：8/8 全部通过；255 项 unittest 用时
   21.623 秒，`OK (skipped=5)`；UI production build 转换 2718 modules；compile、dependency
   consistency、BraveNewWorld testbed、scripted/Codex runbook 和 Git diff 全部通过。完整命令
   退出码为 0，用时 23.4 秒；API 与 Vite 服务未被门禁中断。

## DEV-2026-08-19-27 — 主线收紧为 TaskManager 聚焦应用

- 状态：完成（聚焦产品面、真实 planning 闭环、明确执行边界和完整门禁）
- 类型：focused product surface、application facade、authenticated API、React Flow UI
- 关联决策：[ADR-0036](adr/0036-focused-task-manager-product-surface.md)

### 决策与边界

1. TaskManager 第一阶段是同一仓库/进程内的独立 application facade、API namespace 和 UI route，
   不是微服务。它复用 `TaskOrchestrationService` 的追加式计划真相，不建立第二份可写状态，也不
   提前增加数据库、队列或后台调度依赖。
2. read contract 预留 running/verifying/completed 等执行阶段，但当前 projector 只产生
   proposal_pending/planning/ready/archived。未装配执行 adapter 时，session capability 和 detail
   都明确返回 unavailable/blocker；UI 禁用执行按钮，不把 finalized 计划伪装成 run。
3. 主 Web 产品面收紧为左侧 DAG/Backlog/总 Log、右侧信息/Agent 的双栏结构。候选图、已应用图、
   assessment、完整对话和 append-only revision log 来自同一个 detail；原实验室 hash routes 保留，
   但移出主导航。
4. 未来 run 必须绑定用户固化的 plan id、revision 和 record hash，规划调整形成新 revision；执行、
   验证、批准和完成仍必须进入既有 application service 与 `WorkflowKernel.transition`。

### 落实与变更文件

1. 新增 `src/jobslayer/task_manager/__init__.py` 的 provider-neutral task/node stage、summary、
   Backlog、log 和 revision-bound detail contracts；新增
   `src/jobslayer/application/task_manager.py`，把 planning snapshot/assessment/history 投影为统一
   多任务 read model，并复用原 create/discuss/apply/reject/finalize 命令。
2. `src/jobslayer/orchestration/web.py` 新增认证的 `/api/task-manager` session/tasks/detail/artifact
   读取和写命令；任务读取也要求进程随机 token。旧 `/api/orchestration` 保持兼容。CLI 新增
   `task-manager-api` alias，启动提示和浏览器入口改为聚焦应用。
3. 新增 `ui-framework/src/components/TaskManager.tsx`，接入 React Flow DAG、任务切换、候选图决定、
   Backlog、总 Log、节点详情、完整 Agent 对话和固化动作；`App.tsx` 默认/主导航改为 TaskManager，
   Vite 增加 same-origin proxy，types/styles/command palette 同步。高级实验页未删除。
4. 新增 `tests/test_task_manager.py`，扩展 `tests/test_orchestration_web.py` 和
   `tests/test_codex_planning_agent.py`，覆盖 proposal→applied→finalized 允许路径、过期 revision 拒绝、
   完整 conversation/log、明确执行 blocker、TaskManager read authentication 和 CLI alias。
5. 新增 `docs/TASK_MANAGER.md` 与 ADR-0036，并同步 README、路线图、ADR 索引和 UI 运行说明。

### 当前验证证据

1. `.venv/bin/python -m unittest tests.test_task_manager tests.test_orchestration_web`：9 项全部通过。
2. `.venv/bin/python -m unittest tests.test_task_manager tests.test_orchestration_web
   tests.test_codex_planning_agent`：19 项全部通过；实现阶段用时 1.867 秒，最终清理后复跑用时
   1.941 秒。
3. `sh ./init.sh -- npm --prefix ui-framework run build`：TypeScript 和 Vite production build 通过，
   Node 24.19.0/npm 11.17.0，转换 2719 modules；TaskManager lazy chunk 17.36 kB（gzip 5.92 kB）。
   系统 Node 18 直接运行 Vite 不满足项目 `>=22.12` 要求，因此最终证据使用仓库统一初始化入口。
4. `./jobslayer check`：8/8 全部通过；258 项 unittest 用时 21.415 秒，`OK (skipped=5)`；
   Python compile、dependency consistency、UI TypeScript/Vite production build（2719 modules）、
   BraveNewWorld testbed、scripted/Codex runbook 和 Git diff 全部通过。完整命令退出码为 0，用时
   23.2 秒。
5. 完整门禁后用 `/tmp/jobslayer-task-manager-smoke.*` 临时身份和独立 state 启动
   `task-manager-api`/Vite，经 `http://127.0.0.1:4173/api/task-manager` same-origin proxy 创建
   `task-manager-runtime-smoke`：revision 1、proposal_pending、5 nodes、5 backlog、execution false
   与明确 blocker；headless Chrome 1600×1000 实际渲染并人工检查双栏、候选 DAG 和对话。随后
   关闭 8780/4173 listener 并删除全部临时身份、state、artifact、浏览器 profile 和截图。

### 限制与下一步

当前没有执行 TaskManager 节点、创建 workflow run、自动 apply proposal、自动 retry 或自动完成；
Backlog 中 finalized 节点保持 ready，并明确等待 governed execution adapter。TaskManager 第一阶段
也没有把旧实验室的 node/edge 表单全部搬入主产品面，精细调整先通过带 selected-node context 的
Agent discussion 完成。

下一步实现 finalized revision 到受治理 run assembly 的单向编译/绑定：每个节点映射稳定 task
identity，执行事件和原始制品投影回 TaskManager，允许/拒绝转换经过 Kernel，verification 和授权
approval 仍是完成硬门禁。完成前不得启用“开始执行”。

## DEV-2026-08-19-28 — TaskManager 固化计划到受治理运行装配与反馈投影

- 状态：完成（plan-bound run 装配、Kernel 节点历史、幂等执行端口、API/UI 投影）
- 类型：governed run assembly、provider-neutral executor port、evidence feedback、TaskManager UI
- 关联决策：[ADR-0037](adr/0037-plan-bound-task-manager-run-assembly.md)，产品边界继续遵循
  [ADR-0036](adr/0036-focused-task-manager-product-surface.md) 与
  [ADR-0035](adr/0035-provider-neutral-resumable-long-running-control-plane.md)

### 决策与边界

1. run 必须绑定 active finalized plan 的精确 `plan_id + revision + record_hash`；同一固化 revision
   只能装配一次。规划后续调整不会改写已经装配的 run。
2. 装配为每个 DAG node 派生稳定 workflow task id，并只通过 `WorkflowKernel.transition` 写入
   `Draft -> Planned`。每个节点保存可验证的 transition chain；provider success 只推进到
   `Verifying`，不允许 executor 自行完成。
3. 每次外部启动先在 run journal 持久化稳定 `provider_start_key`，再调用 provider-neutral
   `start_or_locate`；崩溃/连接失败恢复复用相同 key，只有显式 retry 才产生新 attempt identity。
4. provider reference/observation 必须携带非空、task/run-bound、hash-verified artifact evidence；
   normalized status 或 cursor 不能脱离原始证据推进状态。
5. 默认 `task-manager-api` 启用本地 run store 和装配 capability，但不装配具体 executor。UI 可以
   创建/恢复 run，却不会调用本机 Codex；节点执行接口还要求 `EXECUTE_TASK`，planner-only session
   被确定性拒绝。
6. 没有从通用 DAG 猜测 repository、base commit、允许写路径、validation profile 或预算。真实
   Codex executor 必须等这些 execution binding 被显式固化后再接入。

### 落实与变更文件

1. 新增 `src/jobslayer/task_manager/execution.py`，定义 run/node snapshot、stage、provider request、
   reference、observation、executor/store protocols；扩展 `src/jobslayer/task_manager/__init__.py` 的
   TaskManager detail 绑定。
2. 新增 `src/jobslayer/adapters/local_task_manager_runs.py`，实现 immutable plan binding、连续
   revision、SHA-256 previous-hash chain 与原子完整 generation 发布。
3. 新增 `src/jobslayer/application/task_manager_execution.py`，实现 assemble/start-or-locate/observe/
   retry 的治理服务；更新 `src/jobslayer/application/task_manager.py`，把 planning 和 execution truth
   投影为 DAG、Backlog、总 Log、capability 和 blocker。
4. 更新 `src/jobslayer/orchestration/web.py`，新增 run assembly 与 node start/observe/retry API，
   区分 403 权限、409 stale/state、502 provider/evidence 和 503 capability；更新
   `src/jobslayer/cli.py`，默认建立 `<state-root>/task-manager-runs`，但 executor 保持未配置。
5. 更新 `ui-framework/src/types.ts`、`components/TaskManager.tsx` 和 `styles.css`。复用已经锁定的外部
   React Flow 库显示 READY/WAITING/RUNNING/VERIFYING 状态，在状态栏显示 run revision，在节点面板
   显示 workflow/provider evidence，并按真实 capability 启用 run assembly 或节点命令。
6. 新增 `tests/test_task_manager_execution.py`，扩展 `tests/test_task_manager.py` 和
   `tests/test_orchestration_web.py`；新增 ADR-0037，并同步 README、TaskManager 手册、交互指南、
   路线图、Workbench README 与 ADR 索引。

### 精确验证证据

1. `PYTHONPATH=src .venv/bin/python -m unittest -v tests.test_task_manager_execution`：5 项全部通过，
   覆盖允许装配/启动/反馈路径，以及未 finalized、stale revision、依赖未完成、无 adapter、journal
   篡改和 provider start 崩溃恢复拒绝路径。
2. `PYTHONPATH=src .venv/bin/python -m unittest -v tests.test_task_manager_execution
   tests.test_orchestration_web`：12 项全部通过，用时 0.742 秒；API 覆盖 run assembly capability、
   planner 越权执行 403 和 authenticated detail 投影。
3. `sh ./init.sh -- npm --prefix ui-framework run build`：TypeScript 与 Vite production build 通过，
   Node 24.19.0/npm 11.17.0，转换 2719 modules；TaskManager chunk 19.78 kB（gzip 6.58 kB）。
4. `PYTHONPATH=src .venv/bin/python -m compileall -q src tests` 与 `git diff --check`：均返回 0。
5. 本条日志落盘后最终运行 `./jobslayer check`：8/8 全部通过；263 项 unittest 用时 21.471 秒，
   `OK (skipped=5)`；compile、dependency consistency、UI production build（2719 modules）、
   BraveNewWorld testbed、scripted/Codex runbook 和 Git diff 全部通过。

### 限制与下一步

当前没有 concrete TaskManager executor、自动轮询/调度、确定性 verifier/reviewer 推进、human-gate
决定或完成路径；所以默认 run 最多装配到 `Ready`，测试 adapter 的 success 也只到 `Verifying`。
run JSONL 与 artifact registry 是两个本地事务边界，依靠稳定 start key 和重放缩小崩溃窗口，不宣称
exactly-once。当前 UI 只能显示当前 plan revision 精确绑定的 run，历史 run 全局检索仍待补充。

下一步先定义 plan-to-execution binding：显式固化 repository/base commit、allowed/forbidden paths、
validation profile、风险与长任务 budget，然后让第一个 Codex `start_or_locate` adapter 复用
ADR-0035 的 lease/checkpoint/recovery 控制面。随后接 verifier/reviewer/human gate，使首个节点能够在
真实证据和授权下通过完整 Kernel 路径，而不是放宽当前门禁。

## DEV-2026-08-19-29 — BraveNewWorld 悬架目标源绑定与候选 DAG 修订

- 状态：完成（目标源绑定、BNW 预检、真实 pending DAG、API/UI 与完整门禁）
- 类型：execution target binding、BNW compiler gate、Codex profile、TaskManager API/UI、真实 planning
- 关联决策：[ADR-0038](adr/0038-source-pinned-bravenewworld-execution-target.md)，并继续遵循
  [ADR-0037](adr/0037-plan-bound-task-manager-run-assembly.md) 与
  [ADR-0035](adr/0035-provider-neutral-resumable-long-running-control-plane.md)

### 决策与边界

1. 首个 TaskManager 真实目标固定为 `brave-new-world-suspension-v1`。目标不是由 DAG 文本推断，而是
   host 显式映射到 source-controlled runbook，再严格绑定 testbed、task、validation profile 和
   invocation。
2. 四个输入文件分别计算 SHA-256，并合成为 source bundle
   `0e0bf2c72e3217676ba35789f876dfaa87901df28f7d879d6674a540b93eb994`。计划保存 target ID 与该
   hash；run 装配会嵌入完整 binding，后续 revision 禁止改写。
3. 本地 BraveNewWorld inspection 确认 checkout clean、HEAD/tag 都是
   `fb43878c9f0164deef272e55969c0fc134a6d6a3`、origin 已登记。target preflight 阻止 JobSlayer
   自身命令/领域指令，并要求图中包含悬架场景命令和完整 `./bnw check`。
4. 源控 runbook 显式选择本机登录 Codex 的 `gpt-5.6-sol/xhigh`，单次三小时、一次 attempt、
   300k input、50k output、4 MiB context；任务的 10 USD 是内部预算元数据，不宣称订阅余额可见或
   已被精确扣减。
5. 当前不接 execution adapter，不改 BraveNewWorld 源码，不创建 run，不自动 apply/finalize。
   真实模型只在 BNW checkout 上以 read-only planning adapter 生成 pending proposal。

### 落实与变更文件

1. 新增 `tasks/bnw-suspension-visualization-001.json`、
   `validation-profiles/brave-new-world-suspension-v1.json` 和
   `runbooks/bnw-suspension-visualization-001-codex.json`，固定二自由度被动四分之一车辆范围、允许/
   禁止路径、确定性场景/API/UI 同源要求、真实指标、验证命令和长任务预算。
2. 新增 `src/jobslayer/task_manager/binding.py` 与
   `src/jobslayer/adapters/local_task_manager_targets.py`；扩展 orchestration snapshot/服务、
   TaskManager run/request、run journal 和 execution service，使 target selection、source pin、基线
   inspection、target assessment 与 immutable run binding 全部进入应用真相。
3. 扩展 `src/jobslayer/application/runbook.py`、`adapters/codex_cli.py` 和
   `application/local_run.py`，把显式模型和 reasoning effort 从 runbook 映射为 Codex CLI 参数，同时
   保持默认 profile 兼容。
4. 扩展 `src/jobslayer/orchestration/web.py` 和 `src/jobslayer/cli.py`，新增认证 targets 读取、target
   选择命令和 capability，并在默认 TaskManager startup 时解析 BNW 目标；基线或源输入无效会启动
   失败关闭。
5. 扩展 TaskManager read contract 和 `ui-framework/src/components/TaskManager.tsx`/types/styles，
   显示 target、基线、模型、时限、验证命令与 source hash；target blocker 会禁用固化/装配。
6. 新增 `tests/test_task_manager_targets.py` 与 `tests/task_manager_fixtures.py`；扩展 orchestration、
   run assembly、Web API、Codex CLI 和 development-check tests，覆盖选择允许路径、pending/stale
   拒绝、source drift/cross-project 阻断、binding 不可变与 `xhigh` 参数。
7. 新增 ADR-0038，并同步 README、TaskManager 手册、路线图、BraveNewWorld 方案和 ADR 索引；统一
   `./jobslayer check` 增加第三个真实 Codex runbook 校验，门禁由 8 步增加到 9 步。

### 真实计划闭环证据

1. 现有 `suspension-visualization-case-20260819` 先追加 target selection，再由一次只读
   `gpt-5.6-sol/xhigh` invocation `planning-fd1aadbe254145248a996c8eb9039251` 生成 proposal
   `proposal-d640723e393f4ffe9d7cbc4b5f49fe95`，最后以追加 revision 5 固定 source bundle。当前 record
   hash 为 `5f8957c3dd865228a1cc6e6cb7d1d838dd3bf51018ebac57a7d18bcdf15081ab`。
2. 新候选图为 11 nodes/11 edges，移除了 `./jobslayer check`、`jobslayer.domain` 和
   `WorkflowKernel` 等跨项目内容，包含两条精确 BNW 命令；target assessment 为 `ready=true`、
   issues 0。proposal 保持 pending，权威已应用 DAG 尚未改变。
3. invocation 登记四类 hash-verified 制品，共 130,631 bytes：prompt 24,412、raw JSONL 74,584、
   stderr 0、final output 31,635。usage 为 input 163,648、cached input 128,512、output 13,477、
   reasoning output 4,206、cache-write input 0；这里只记录一次实际调用，不换算订阅美元余额。

### 验证、限制与下一步

1. `PYTHONPATH=src .venv/bin/python -m unittest -v tests.test_orchestration
   tests.test_task_manager_targets tests.test_task_manager_execution tests.test_orchestration_web`：24 项全部
   通过，用时 1.133 秒；覆盖目标选择允许/拒绝与 run binding 主路径。
2. `PYTHONPATH=src .venv/bin/python -m unittest -v tests.test_orchestration
   tests.test_task_manager_targets tests.test_codex_cli tests.test_task_manager_execution
   tests.test_orchestration_web`：修复单个时间字段断言前 33 项中 32 项通过；修复后相关目标测试单独
   通过。该中间结果不作为最终完成门禁。
3. `./jobslayer check`：9/9 全部通过，退出码 0，用时 23.6 秒；267 项 unittest 用时
   21.817 秒，`OK (skipped=5)`；Python compile、dependency consistency、UI TypeScript/Vite
   production build（2719 modules）、BraveNewWorld testbed、scripted runbook、既有 Codex runbook、
   新悬架 Codex runbook 和 Git diff 全部通过。当前明确限制是没有 concrete TaskManager
   `start_or_locate` Codex adapter、自动 checkpoint/reattach、确定性 verifier/reviewer、human-gate
   决定或完成路径。
4. 下一步必须由用户审查并 apply/reject 当前 proposal。只有 apply 后 target assessment、完整度检查
   和显式 finalize 都通过，才能装配 run；之后再实现真实可恢复 executor，不能复用旧的进程内
   Codex adapter 冒充长任务恢复能力。
5. 上述日志结果回填后再次运行 `./jobslayer check`：9/9 全部通过，退出码 0，用时 24.2 秒；
   267 项 unittest 用时 22.393 秒，`OK (skipped=5)`，其余门禁结果与首轮一致。
6. 用已有 `suspension-demo-planner` 短期身份启动新版 loopback API 8780 和 Vite 4173；经 Vite
   same-origin proxy 读取 session/targets/tasks/detail 均为 HTTP 200。真实 detail 为 revision 5、
   `proposal_pending`、proposal `proposal-d640723e393f4ffe9d7cbc4b5f49fe95`、11 nodes、目标基线
   ready、target issues 0；根 HTML 为 HTTP 200。两个 listener 在本条完成时保留，供用户审查；
   external execution adapter 仍明确未连接。

## DEV-2026-08-19-30 — 用户应用真实 DAG 与可恢复 TaskManager Codex worker

- 状态：完成（proposal apply 已核验、持久 start-or-locate、run worktree、原始证据、RBAC/CLI 门禁）
- 类型：real plan decision、durable Codex adapter、detached worker、node dispatch gate、TaskManager UI
- 关联决策：[ADR-0039](adr/0039-durable-task-manager-codex-worker.md)，并继续遵循
  [ADR-0038](adr/0038-source-pinned-bravenewworld-execution-target.md)、
  [ADR-0037](adr/0037-plan-bound-task-manager-run-assembly.md) 与
  [ADR-0035](adr/0035-provider-neutral-resumable-long-running-control-plane.md)

### 真实计划状态与决策

1. 用户在可视化端应用 proposal 后，读取 append-only plan store 核验最新 revision 6：operation
   `agent_proposal.applied_by_user`、record hash
   `a69d110f4b860a30fff260b9d32072774213ed7238e5ab8b0bf18922186a6ab5`、11 nodes/11 edges、无
   pending proposal，target 仍为 `brave-new-world-suspension-v1`。计划仍是 `draft`，本条没有代替
   用户点击固化、没有装配 run，也没有运行或修改 BraveNewWorld。
2. `validation` node 现在确定性拒绝 Agent dispatch，与既有 `human_gate` 拒绝路径并列；UI 也禁用
   两类节点的 executor 按钮并显示专用治理路径。普通 task/milestone 的既有允许路径没有放宽
   `WorkflowKernel`、依赖、revision 或 evidence 门禁。
3. concrete adapter 采用稳定 `provider_start_key` 和 provider id。完整 request、合成 prompt、launch
   argv、worktree manifest 与 evidence 在启动前落盘；独立 worker 用 create-exclusive claim 确保
   重放只有一个 Codex 进程获得启动权。相同 key 与不同请求/launch/prompt 绑定时失败关闭。
4. 每个 TaskManager run 从 target 固定 commit 建立一个独立 Git worktree，顺序 node 共享该 workspace；
   原始 Codex JSONL、stderr 和 terminal result 保留并登记为 task/run-bound 内容寻址制品。normalized
   observation 用内容 cursor 持久化，相同 cursor 重读返回完全相同的 evidence 与时间。
5. API 只有同时提供 `--allow-external-task-execution` 和签名 session 的 `executor` 权限才实例化
   adapter；默认仍不连接。实际 model/reasoning 不再只藏在 profile 名中，而是以
   `executor_model=gpt-5.6-sol`、`executor_reasoning_effort=xhigh` 进入 source-pinned binding 和 UI。

### 落实与变更文件

1. 新增 `src/jobslayer/adapters/task_manager_codex.py` 与
   `src/jobslayer/adapters/task_manager_codex_worker.py`；新增
   `tests/test_task_manager_codex.py`，覆盖真实 detached fake-Codex 进程、API adapter 重建定位、单次
   启动、稳定 observation、证据哈希、隔离 worktree、request drift 与非 task 拒绝。
2. 更新 `src/jobslayer/application/task_manager_execution.py`、`application/task_manager.py` 与
   `tests/test_task_manager_execution.py`，补充 executor/target adapter 匹配及 validation/human gate
   拒绝；所有 workflow state 仍只经 `WorkflowKernel.transition`。
3. 更新 `src/jobslayer/task_manager/binding.py`、`adapters/local_task_manager_targets.py`、
   `tests/test_task_manager_targets.py` 和 `src/jobslayer/cli.py`，固化/展示实际模型配置，并加入双重
   opt-in + RBAC 执行开关。
4. 更新 `ui-framework/src/types.ts` 与 `components/TaskManager.tsx`，显示精确 model/reasoning，并为
   validation/human gate 显示不可交给 Agent 的准确状态；新增 ADR-0039，同步 TaskManager 手册、
   README、ROADMAP 和 ADR 索引。

### 验证证据、限制与下一步

1. `PYTHONPATH=src .venv/bin/python -m unittest -v tests.test_task_manager_codex
   tests.test_task_manager_execution tests.test_task_manager_targets tests.test_orchestration_web`：18 项全部
   通过，用时 1.701 秒。detached fake Codex 的重启定位用例证明同一 start key 只有一次外部启动；
   node kind 测试同时覆盖允许的既有 executor 路径和 validation/human gate 拒绝路径。
2. `PYTHONPATH=src .venv/bin/python -m compileall -q src tests`、`git diff --check` 均返回 0；
   `sh ./init.sh -- npm --prefix ui-framework run build` 通过，转换 2719 modules，TaskManager chunk
   21.57 kB（gzip 7.09 kB）。
3. `./jobslayer check`：9/9 全部通过，退出码 0，用时 24.8 秒；271 项 unittest 用时
   22.961 秒，`OK (skipped=5)`；compile、dependency consistency、UI production build（2719
   modules）、BraveNewWorld testbed、三份 runbook binding 和 Git diff 门禁全部通过。结果回填后对
   最终工作树再次运行同一命令，仍为 9/9、退出码 0，用时 24.2 秒。
4. 当前 adapter 解决 API 进程重启，不宣称机器重启后透明续跑；worker 消失且没有 terminal evidence
   会失败关闭并要求新 attempt。wall-clock timeout 是硬限制，token/成本是输入和事后证据，不伪称能
   精确读取或预扣 200 美元订阅余额。
5. 下一步是实现首个 `human_gate` 授权决定以及 validation/verifier/reviewer/integration 完成路径。
   当前真实计划故意停在 draft，现有 planner-only session 也不能启用执行；需要用户显式固化计划并
   换用同时具备 `planner`、`executor` 的短期 session 后，才能装配和启动普通 task node。
6. 用现有 planner-only 签名 session 重启 loopback API 8780，Vite 4173 保持运行。经 same-origin
   proxy 实测 session/detail HTTP 200：真实 revision 6、无 pending proposal、plan/target assessment
   均 ready、UI 收到精确 `gpt-5.6-sol/xhigh`；`task_execution=false`、`execution_available=false`，
   blocker 仍要求用户先固化计划。没有提供 executor opt-in，也没有产生 BNW worktree 或外部调用。

## DEV-2026-08-19-31 — 固化运行装配与根人工范围门禁

- 状态：完成（真实 run 装配、根门禁应用、首个 durable Codex 节点启动与证据反馈）
- 类型：real finalized run、RBAC executor opt-in、human gate state、decision evidence、TaskManager UI
- 关联决策：[ADR-0040](adr/0040-plan-finalization-bound-root-human-gate.md)，并继续遵循
  [ADR-0039](adr/0039-durable-task-manager-codex-worker.md) 与
  [ADR-0037](adr/0037-plan-bound-task-manager-run-assembly.md)

### 真实状态与授权边界

1. 用户在 UI 显式固化并明确允许启用执行后，核验计划 revision 7：operation
   `plan.finalized_by_user`、record hash
   `3ec6c00c7a374ce3b019d66f450898c90af3aa63f6a6c041175eb2e1c08ff0fd`、status finalized、
   `latest_finalized_revision=7`、11 nodes/11 edges、target/source pin 未变化且 assessment ready。
2. 按用户授权创建 create-only、8 小时本地签名 session `suspension-demo-operator`，角色严格为
   `planner + executor`；重启 API 时显式提供 `--allow-external-task-execution`。live session/detail
   显示 `task_execution=true`、durable Codex connected；没有增加 approver、reviewer 或 admin 权限。
3. 通过 API 为 revision 7 装配唯一 run `tmrun-f7891eb4a2cd4f0ca1aff2dec8073064`。run revision 1
   精确绑定上述 plan hash，stage ready；装配不调用 Codex、不创建 BNW worktree。首节点
   `scope-decision` 是 human gate，因此没有自动启动外部执行。

### 决策与实现

1. 新增 `TaskState.GATE_APPROVED` 终态，专门表达受治理的人类门禁已批准；它不是 `Completed`，不
   代表代码实现、验证、集成或整个 run 完成。只有 human/policy 能从 `PlanReview` 进入，且必须有
   immutable evidence；Agent 自批和无证据批准由 `WorkflowKernel` 拒绝。
2. 新 run 装配把 human gate 经 Kernel 置为 `PlanReview`。依赖满足规则仅接受代码节点
   `Completed` 或门禁 `GateApproved`；旧 run 中仍为 `Planned` 的根门禁会先追加 `PlanReview`，不
   改写既有 revision。
3. 新增 `confirm_scope_gate` 应用命令和 HTTP route。它只接受无依赖根 human gate、非空理由和
   planner 权限，把 plan id/revision/hash、node、actor、理由与时间登记为 task/run-bound 内容寻址
   制品，再经 Kernel 追加 `GateApproved`。尾部最终验收门禁有依赖，确定性拒绝复用该路径。
4. UI 为根门禁显示理由输入与“确认已固化范围”；validation 和非根 human gate 继续禁用 executor。
   更新 `domain/models.py`、`workflow/kernel.py`、TaskManager execution/facade/web/read model、前端
   types/component/styles、测试、TaskManager 手册、ADR-0040 与 ADR 索引。

### 当前验证与下一步

1. `PYTHONPATH=src .venv/bin/python -m unittest -v tests.test_workflow
   tests.test_task_manager_execution tests.test_task_manager tests.test_orchestration_web`：30 项全部通过，
   用时 1.831 秒；同时覆盖 GateApproved 允许路径、Agent/无证据拒绝、root gate evidence、依赖解锁
   及非根 final gate 拒绝。
2. `PYTHONPATH=src .venv/bin/python -m compileall -q src tests`、`git diff --check` 返回 0；UI production
   build 通过，转换 2719 modules，TaskManager chunk 22.35 kB（gzip 7.31 kB）。
3. `./jobslayer check`：9/9 全部通过，退出码 0，用时 25.5 秒；compile、dependency consistency、
   UI production build（2719 modules）、BraveNewWorld testbed、三份 runbook binding 和 Git diff
   门禁全部通过。回填真实执行证据后再次运行，仍为 9/9、退出码 0，用时 24.7 秒；最终单独运行
   `PYTHONPATH=src .venv/bin/python -m unittest discover -s tests`，274 项用时 22.426 秒，
   `OK (skipped=5)`。
4. 重启 API 加载门禁实现后，将用户“已固化，并允许启用执行”的明确指令连同详细固定范围理由写入
   `scope-decision`。真实 run revision 2 的该节点经三条 Kernel transition 进入 `gate_approved`；最后
   一条证据绑定 plan hash 与 `artifact-fe88170abf50437e88e0bf7ec0bae47c`，下一节点变为 READY。
5. 随后授权 `repository-reconnaissance`，形成稳定 start key
   `tmstart-4c17541f9ee74d437be1d710ca677a9c`、provider run
   `codex-task-9627fb237b0b581db70b19767ef3c6ce0781ee8a44e016f6a77cbf9dbfc58a88` 和 start evidence
   `artifact-ca25ffc5485c473eb94bc6ce9e013f2d`。实际命令为本机登录 Codex
   `gpt-5.6-sol/xhigh`，运行在独立 worktree `tmws-346d2d5f7e292c0a8bfdd744`。
6. provider 以 exit 0、无 timeout、无 protocol failure 终止；usage 为 input 193,026、cached input
   152,832、output 8,209、reasoning output 3,470、cache-write input 0。最终观察把 run 推进到 revision
   8、node `Verifying`，携带原始 JSONL 与 observation evidence。报告确认 BNW-0 head/tag、主检出和
   run worktree 都无变更，定位契约/CLI/scenario/HTTP/UI/测试的真实扩展点，并通过 7 项契约测试与
   4 项一阶数值测试。
7. worker 的 workspace-write sandbox 禁止创建 loopback socket；Agent 明确记录该限制，没有把动态
   HTTP 探测的 `PermissionError` 误报为产品失败。HTTP 面仍只有源码与既有回归证据，本节点没有运行
   会写 bytecode 的完整 `./bnw check`，符合只读勘察范围。
8. 当前不会自动启动 `simulation-visualization-design`：勘察节点已到 `Verifying`，但 TaskManager 尚无
   把节点 acceptance criteria 编译成确定性 verification report、review 与中间阶段验收的实现。
   provider success 仍不等于 workflow completion；下一步应补这条路径，而不是直接把节点标为完成。
9. 终态观察落盘后再次停止并重启 TaskManager API。重新读取仍得到 run revision 8、stage
   `verifying`、相同 provider run/start key、`succeeded` observation 与内容 cursor；没有产生新的
   Codex 进程或重复启动证据，验证本次真实运行可跨 API 进程恢复查询。

## DEV-2026-08-19-32 — 确定性阶段验证与 Reviewer 交付物接受

- 状态：完成（无源码差异的阶段性交付物闭环；源码集成与 validation 专用执行仍待后续）
- 类型：workflow terminal state、structured workspace evidence、RBAC reviewer、TaskManager API/UI
- 关联决策：[ADR-0041](adr/0041-verified-artifact-deliverable-acceptance.md)，并继续遵循
  [ADR-0039](adr/0039-durable-task-manager-codex-worker.md) 与
  [ADR-0040](adr/0040-plan-finalization-bound-root-human-gate.md)

### 决策与实现

1. 新增 `TaskState.DELIVERABLE_ACCEPTED` 终态，专门表达无源码差异的 task/milestone 已先通过
   `VerificationReport`，再由 human/policy reviewer 以不可变证据接受。它不是源码 `Completed`、
   source integration 或最终 run approval；Agent、自缺失 report 或无 review evidence 的转换均由
   `WorkflowKernel` 拒绝。
2. 扩展 provider-neutral executor contract，adapter 只返回 source commit、workspace inspection、changed
   paths、可选 patch hash 和 task/run-bound artifacts。TaskManager verifier 自己校验 provider terminal、
   workspace binding、路径策略与 evidence integrity，才把 `Verifying` 推进到 `Reviewing` 或
   `Repairing`；adapter 不拥有 pass/fail。
3. `accept_node_review` 把 plan id/revision/hash、node、report、交付物、验收标准、reviewer 和理由登记为
   review artifact，再经 Kernel 进入 `DeliverableAccepted`。只要 changed paths 非空、patch hash 存在
   或 workspace 不 clean，就确定性拒绝 artifact-only 路径，保留既有源码 review/integration 门禁。
4. 增加 `verify` 与 `accept-review` HTTP 命令；前者要求 `EXECUTE_TASK`，后者要求
   `REVIEW_IMPLEMENTATION`。UI 按 `Verifying -> Reviewing` 显示两步操作、接受理由、check 数量、差异
   数量和 review artifact；validation 与 human gate 仍走专用路径。
5. 新增可选 run snapshot 字段后，真实旧 journal 首次重启按设计拒绝了 revision 1 hash 重算。修复为
   history verification 使用 `exclude_unset` 重建原记录字段面：不改写旧 JSONL，也不把新增默认字段
   追溯加入旧 hash。新增回归测试模拟 pre-verification schema 并证明原 hash 保持可读。

### 真实运行与授权证据

1. 根据用户“接受勘察结果并允许中间 reviewer 路径”的确认，create-only 签发 8 小时 session
   `suspension-demo-reviewer`，角色严格为 `planner + executor + reviewer`；未授予 approver、admin、
   decision application 或 source integration 权限。
2. API 用新 session 恢复既有 run 后，对 `repository-reconnaissance` 运行确定性验证。run revision 9
   的四项 required checks（provider terminal、workspace binding、changed-path policy、immutable evidence）
   全部 passed；workspace changed paths 为空且 clean，report
   `tmverify-b442d7abbb354cf1b9800e7eb62d046b` 及 artifact
   `artifact-5130592e54fa433daf32f0537a20f4a5` 已绑定到 workflow task/run。
3. 将用户确认、四项 passing checks 和无源码差异事实写入 reviewer evidence
   `artifact-12a19f2b66e54c6da849e8405000441a`。run revision 10 中勘察节点经 Kernel 进入
   `deliverable_accepted`，run 回到 `ready`，且 `simulation-visualization-design` 成为唯一就绪后继。
4. 随后授权 `simulation-visualization-design`，稳定 start key 为
   `tmstart-0ddd680f38d547cbccbf2a52c546b4b0`，provider run 为
   `codex-task-55a4ed2a07ebc857e8020b609d381a8fdac5250cb00c9ced7c6df3679aadb18e`。provider
   exit 0、无 timeout、无 protocol failure；usage 为 input 561,745、cached input 477,696、output
   23,238、reasoning output 10,736、cache-write input 0。run revision 20 的节点进入 `Verifying`。
5. 设计节点只在隔离 BNW worktree 追加 `docs/DEVELOPMENT_LOG.md` 并新增 proposed ADR
   `docs/adr/0002-quarter-car-simulation-contract.md`，没有修改实现、测试或主检出。报告固定二自由度
   方程、SI 单位、RK4 边界、版本化联合契约、统一分派、轨迹/指标/hash、兼容矩阵和视觉缩放边界；
   12 项非 socket 基线测试、编译和 diff 检查通过，完整 `./bnw check` 的 4 项 HTTP 测试因 Agent
   sandbox 禁止 loopback socket 而未通过，未被误报为产品回归。
6. 对该节点运行确定性 verifier 后，run revision 21 的四项 required checks 全部 passed；报告
   `tmverify-3f6fb04c00d44c7e85333de0935f2477`、artifact
   `artifact-6a7b33a72c7e41cbbeb69409c6e9c8b7` 与 patch SHA-256
   `cecd4021d9afadc05d65463dc878b078f22a7761fae4e343434e8ffbe38d35d0` 已登记。changed paths 精确为
   上述两份允许路径文档，workspace 因候选差异不 clean，节点停在 `Reviewing`。

### 验证与限制

1. 聚焦测试覆盖允许路径以及 Agent/缺 evidence/源码差异拒绝路径、真实 Git workspace evidence、旧
   schema hash 兼容和 reviewer RBAC；UI TypeScript 与 production build 通过，转换 2719 modules，
   TaskManager chunk 23.92 kB（gzip 7.76 kB）。
2. `PYTHONPATH=src .venv/bin/python -m compileall -q src tests` 与 `git diff --check` 返回 0。
3. `./jobslayer check`：9/9 全部通过，退出码 0，用时 25.6 秒；279 项 unittest 用时 23.815 秒，
   `OK (skipped=5)`，compile、dependency consistency、UI production build、BraveNewWorld testbed、
   三份 runbook binding 和 Git diff 门禁全部通过。
4. 当前 reviewer session 没有 approver 或 source integration 权限，TaskManager 也尚未暴露源码 review
   package、批准与精确 patch integration 命令。因此不会把设计节点转为 `DeliverableAccepted`，也不会
   修改 BNW 主检出。下一步需用户明确授权落实源码 review/integration 路径及后续批准边界。

## DEV-2026-08-19-33 — 独立源码审查、审批与隔离运行分支检查点

- 状态：完成（实现、全量门禁及真实设计节点独立审查/审批/checkpoint 均已验证）
- 类型：source review package、independent approval、idempotent Git checkpoint、RBAC、TaskManager UI
- 关联决策：[ADR-0042](adr/0042-independent-source-review-and-isolated-run-checkpoint.md)，并继续遵循
  [ADR-0037](adr/0037-plan-bound-task-manager-run-assembly.md)、
  [ADR-0039](adr/0039-durable-task-manager-codex-worker.md) 与
  [ADR-0041](adr/0041-verified-artifact-deliverable-acceptance.md)

### 授权边界与实现

1. 用户明确允许继续落实 `source reviewer review -> independent Approver approval -> exact patch
   checkpoint in isolated run branch`；范围明确不含 BraveNewWorld 主干 merge/checkout mutation、push
   或 deploy，并要求使用独立、短期、最小权限 approver session。
2. 新增 provider-neutral `ManagedCheckpointRequest/Result` 和 `TaskManagerSourceIntegrator`；run node
   原子持久化 `ReviewReport`、review/approval artifacts、独立 approver、integration key 与
   `SourceIntegrationResult`。所有状态仍只经 Kernel 的
   `Reviewing -> MergeReview -> Integrating -> Completed` 推进。
3. `review_source_node` 只接受 passing report 所绑定的非空 source patch；
   `approve_source_checkpoint` 确定性拒绝 reviewer 自批，并在任何 Git 副作用前把 stable key、精确
   plan/review/report/patch/path/target/ref 和理由追加到 run 哈希链；`integrate_source_checkpoint` 再逐项
   比对 adapter result，证据不一致时失败关闭。
4. 新增 `LocalTaskManagerGitCheckpointIntegrator`：canonical request create-once，同 key 输入漂移拒绝；
   只对既有 run worktree 的 reviewed paths 执行 Git add/commit；重试校验唯一 parent、branch、clean
   tree 与 patch hash/path 后返回同一 durable result。adapter 不 checkout/merge 主干，不 push/deploy。
5. durable Codex verification 改为相对当前 run-branch HEAD 采集 node-local patch，使前一节点检查点完成
   后，后续节点不会把整个历史累计差异误当成自己的 patch。
6. HTTP/UI 增加 `review-source`、`approve-checkpoint`、`integrate-checkpoint`，分别要求
   `REVIEW_IMPLEMENTATION`、`APPLY_DECISION`、`INTEGRATE_SOURCE`；CLI 增加显式
   `--allow-task-manager-checkpoint-integration`。server 只要求 control-plane view 身份，所有 planning
   mutation 改为逐命令检查 `MANAGE_TASK_PLAN`，因此 approver-only session 不能修改计划。

### 验证、真实应用与下一步

1. `sh ./init.sh -- python -m unittest tests.test_task_manager_execution
   tests.test_task_manager_codex tests.test_orchestration_web`：22 项全部通过，用时 2.774 秒。覆盖完整允许
   路径、reviewer 自批拒绝、planner API 越权拒绝、真实临时 Git repo 的 exact commit、同请求幂等、
   同 key request drift 拒绝，以及主检出 HEAD/status 不变。
2. `sh ./init.sh -- python -m compileall -q src tests` 返回 0；
   `sh ./init.sh -- npm --prefix ui-framework run build` 通过，转换 2719 modules，TaskManager chunk
   25.23 kB（gzip 8.17 kB）。
3. 主要变更文件：`src/jobslayer/task_manager/execution.py`、
   `application/task_manager_execution.py`、`application/task_manager.py`、
   `adapters/task_manager_codex.py`、`adapters/task_manager_git_checkpoint.py`、`orchestration/web.py`、
   `cli.py`、`ui-framework/src/types.ts`、`ui-framework/src/components/TaskManager.tsx`、三份聚焦测试、
   TaskManager 手册、ADR-0042 与 ADR 索引。
4. 本路径只形成隔离运行分支 checkpoint，不声明项目主干已集成或可发布；跨机器调度、远端 push、
   deployment 和最终 human gate 仍不在本次范围。
5. 首次 `./jobslayer check`：9/9 全部通过，退出码 0，用时 25.9 秒；281 项 unittest 用时
   23.843 秒，`OK (skipped=5)`；compile、dependency consistency、UI production build、BNW testbed、
   三份 runbook binding 和 diff 门禁全部通过。
6. 重启现有 `planner + executor + reviewer` API 加载新链路；Reviewer 逐项检查两份文档差异后，将
   report `tmverify-3f6fb04c00d44c7e85333de0935f2477`、patch
   `cecd4021d9afadc05d65463dc878b078f22a7761fae4e343434e8ffbe38d35d0` 与两条 changed paths 固化为
   review `tmreview-5e00d0045f8d43c0b6036a7b37f1c4f6` / artifact
   `artifact-61a55b4cff7e46b98aec563006314ce9`。run revision 22 进入 `MergeReview`；该 session 的 approval
   capability 为 false。
7. create-only 签发 60 分钟 `suspension-demo-approver` session，角色严格只有 `approver`。实测其
   planning/discussion/execution/review capability 全为 false，只有 checkpoint approval/integration 为
   true。审批把限定为隔离分支、明确排除 main/push/deploy 的理由写入 artifact
   `artifact-85ea894193984e6db6f846a8a5ad19f9`，并在 Git 副作用前将 stable key
   `tmintegrate-56120a58e8172cd392b0476387d86366` 追加为 run revision 23 `Integrating`；此时 worktree
   HEAD 仍是 `fb43878c9f0164deef272e55969c0fc134a6d6a3` 且两份差异仍未提交。
8. integration 将精确 reviewed patch 提交到已有分支
   `jobslayer/tmws-346d2d5f7e292c0a8bfdd744`，commit
   `60e1af0fb7cdc96cad427f24570b2c8fa42477f2` 的唯一 parent 是固定 `bnw-0` baseline，changed paths 仅
   `docs/DEVELOPMENT_LOG.md` 与 `docs/adr/0002-quarter-car-simulation-contract.md`；integration artifact
   为 `artifact-58b4155f4c084757bbcd1142baf9cec7`。run revision 24 的设计节点经 Kernel 进入
   `Completed`；重复同命令保持 revision 24 和同一 commit，worktree clean。
9. 全程检查 BraveNewWorld 主检出仍在 `fb43878c9f0164deef272e55969c0fc134a6d6a3` 且 status clean；没有
   checkout/merge main、push 或 deploy。随后停止 approver API，恢复原 reviewer/executor API；run stage
   为 ready，`suspension-simulation-model` 是唯一 READY 后继。下一步可在再次通过完整门禁后授权该
   模型实现节点，其 verification patch 将以 `60e1af0...` 当前 run-branch HEAD 为 node-local base。
10. 回填上述真实证据后再次运行 `./jobslayer check`：9/9 全部通过，退出码 0，用时 26.7 秒；281 项
    unittest 用时 24.743 秒，`OK (skipped=5)`，其余 compile/dependencies/UI/testbed/runbooks/diff 门禁
    同样全部通过。

## DEV-2026-08-19-34 — 悬架内核与交互可视化的真实执行闭环

- 状态：进行中（内核与 UI 两个源码节点均已完成独立 review/approval/checkpoint；后续测试、文档、全库验证与最终人工门禁仍按 DAG 推进）
- 类型：durable Codex execution、deterministic verification、browser evidence、independent checkpoint
- 继续遵循：[ADR-0039](adr/0039-durable-task-manager-codex-worker.md)、
  [ADR-0041](adr/0041-verified-artifact-deliverable-acceptance.md) 与
  [ADR-0042](adr/0042-independent-source-review-and-isolated-run-checkpoint.md)

### `suspension-simulation-model` 真实执行与检查点

1. 从已完成的设计检查点 `60e1af0fb7cdc96cad427f24570b2c8fa42477f2` 授权模型节点，start key
   `tmstart-d65faf12ad8ecc43ab47d5fba798494a`、provider run
   `codex-task-c8d4b2ca05b11cd2955b81d530add4a8e31c8b8b80cc9198226b86123217af7d`。本机登录
   Codex `gpt-5.6-sol/xhigh` 从 10:49:10 UTC 运行至 11:00:28 UTC，exit 0、无 timeout 或 protocol
   failure；usage 为 input 1,634,346、cached input 1,515,264、output 34,798、reasoning output
   12,096、cache-write input 0。
2. 节点实现严格四分之一车辆契约、确定性余弦凸包、固定步长 RK4、公共分派、版本化场景及
   CLI/HTTP/manifest 路由，并保留一阶兼容。默认悬架场景为 2501 点，trace hash
   `0aa84b98b3e635d2d7aee9489b6ec49f71e341952eeefd98d725b64538b6fdbf`。宿主 `./bnw check`
   为 4/4，36/36 unittest 通过；worker 中唯一 4 项失败是 sandbox 禁止 loopback socket，未被误报。
3. verifier report `tmverify-d3b50a591e61413fbf3a5a8ae5e5f308` 将 13 个允许路径和 patch
   `b27d0d6af62c59aaf0541da2142855a091c295d7614e8bc20170e011ff474142` 绑定到 source commit
   `60e1af0...`。Reviewer 固化 review `tmreview-551de7fdc15e4e65bbd9db6470cde092` / artifact
   `artifact-47555033b1054bb29308a02609432337`；独立 approver-only session 固化 approval artifact
   `artifact-1ed3af24733d4db5951139800845cb57` 和 integration key
   `tmintegrate-c1bbaba62d45aeffdcb5f5e706f922c3`。
4. 精确补丁提交为隔离分支 commit `063f443710dc54db56077d8056245663b5a7d319`，integration artifact
   `artifact-0b81d8cd57274bc6b638d70d55afce97`；重复 integrate 保持 run revision 31、相同 commit 和
   integration ID `tmintegration-cd22f156ac567bac026fa7df8aa46ec8`。

### `interactive-visualization-case` 真实执行与浏览器复核

1. 依赖完成后授权 UI 节点，start key `tmstart-01149d9fe948de5c219aded2613f7f57`、provider run
   `codex-task-188db211725610a744a1bbd4f319de8ca400128de89a79eee63daec41d3e68b8`。Codex 从
   11:05:09 UTC 运行至 11:20:31 UTC，exit 0、无 timeout 或 protocol failure；usage 为 input
   2,429,615、cached input 2,321,152、output 47,317、reasoning output 9,379、cache-write input 0。
2. 节点新增 manifest 驱动案例/参数、同一 API trace 驱动的机械 Canvas、三组曲线、量化指标、
   开始/暂停/播放复位、原子参数更新、错误清理、SI 单位、非实物比例和键盘/非颜色可访问表达；新增
   9 项固定 trace 展示测试。worker 的 41/45 与 Chrome 启动限制同样来自 socket sandbox；宿主完整
   `./bnw check` 为 4/4，45/45 unittest 通过。
3. 宿主真实 Chrome 首轮烟雾检查发现 `.empty-result { display: flex; }` 覆盖成功响应后的 `hidden`
   属性，导致旧请求空态与结果同时显示。Reviewer 在验证前以最小 CSS 修正加入显式
   `.empty-result[hidden]` 规则、增加回归断言并追加 BNW 开发日志；最终门禁仍为 4/4。
4. 修正后以真实 loopback API 和 Chrome DevTools 验证：键盘方向键从一阶案例切换到悬架案例，得到
   2501 点与 hash 前缀 `0aa84b98b3e6`；15 个参数、8 项指标、6 条曲线及两个 Canvas 一致显示；空格键
   播放和暂停共同停在 `0.190 s` 的第 96 个样本。跨字段非法请求显示领域错误、清空旧结果并禁用
   播放/导出，键盘恢复默认后重新取得完整默认结果。
5. 最终 verifier report `tmverify-387bbcd6e3c44f7aa944a159c42db3bb` 将 5 个路径和 patch
   `b0ddec165a176db96ba305e9da2dcb56f3ce62d11685b35e6f6fdd20b11b1b03` 绑定到 `063f443...`。
   Reviewer 固化 review `tmreview-07aa2046fb934c5386482cd21a7bab6b` / artifact
   `artifact-d4e426a4f6604369b75b1bba3fa39beb`；独立批准 artifact
   `artifact-dc1e961da99d4cd099f1853ebb58dabb` 和 key
   `tmintegrate-c2e0368ac1cfe76cba79a9e98316966f` 在 Git 副作用前落盘。
6. 精确 UI 补丁提交为隔离分支 commit `11058bbeac74e01d16eeee3636c3ec936275f72c`，integration artifact
   `artifact-5976b802ba8a4400a591061269b02a3d`；重复 integrate 保持 run revision 45、相同 commit 和
   integration ID `tmintegration-15bf11f82a33e500572f1a5699f35353`。提交后 `./bnw check` 仍为
   4/4、45/45，worktree clean。

### 边界与下一步

1. 两个节点始终只写同一隔离分支 `jobslayer/tmws-346d2d5f7e292c0a8bfdd744`。BraveNewWorld 主检出
   始终保持 `main@fb43878c9f0164deef272e55969c0fc134a6d6a3` 且 clean；没有 merge、push、deploy 或远端变更。
2. run revision 45 回到 `ready`；`focused-automated-tests` 是唯一依赖已满足的后继。下一步恢复
   reviewer/executor API 后授权该节点，继续沿 verifier -> source review -> independent approval ->
   isolated checkpoint 闭环推进；最终完成仍由后续 validation 和 human gate 决定。
3. 当前 JobSlayer 工作树的完整 `./jobslayer check` 将在本条真实证据回填后执行，并把精确结果追加在
   本节；此前最近一次完整结果为 DEV-33 记录的 9/9。

## DEV-2026-08-19-35 — TaskManager 源码绑定的确定性 validation node

- 状态：完成（实现、拒绝路径、UI/API、完整门禁及真实 BNW 验证节点均已验证）
- 类型：provider-neutral validation adapter、policy-constrained commands、Kernel verification、RBAC、UI
- 关联决策：[ADR-0043](adr/0043-source-bound-deterministic-validation-nodes.md)，并继续遵循
  [ADR-0037](adr/0037-plan-bound-task-manager-run-assembly.md) 与
  [ADR-0041](adr/0041-verified-artifact-deliverable-acceptance.md)

### 决策与实现

1. `TaskManagerValidator` 与 `ManagedValidationCheckEvidence` 只表达 adapter 观察到的原始命令和
   workspace 事实；没有 SDK/provider 对象进入 domain，也不允许 adapter 决定 pass、review 或
   completion。普通 task 继续拒绝走 validation adapter，validation/human gate 继续拒绝 Codex dispatch。
2. 新增 `LocalTaskManagerValidationRunner`：从已有 run workspace 读取 manifest，绑定当前隔离分支
   HEAD，开始及采证时都要求 clean；只通过 `GovernedLocalCommandRunner` 执行 finalized target 的
   `ValidationProfile`。每条 stdout/stderr、完整 hash、截断、退出码与计时均登记为 task/run-bound
   artifact，且验证运行不允许产生 changed paths 或 source patch。
3. TaskManager 先通过 Kernel 将显式人类授权与稳定 `tmvalidate-*` key 写入 run 哈希链，再调用
   `start_or_locate`。adapter 对 canonical request、reference、terminal result 使用 create-once durable
   state；相同 terminal 幂等复用、同 key 输入漂移拒绝。同步本地 runner 在 terminal 原子落盘前崩溃
   时可能重跑，因此当前只支持幂等、非发布型 validation profile，限制已写入 ADR/手册。
4. runner `Succeeded` 只表示命令已终止。application service 精确核对 finalized check id/order、
   required、argv、cwd、task/workspace/policy 绑定，再把 command status 与四项结构事实编译成
   `VerificationReport`，经 Kernel 进入 `Reviewing` 或 `Repairing`；只有授权 Reviewer 接受 passing
   report 才进入 `DeliverableAccepted`。
5. HTTP 新增 `run-validation`，要求 `EXECUTE_TASK`；CLI 新增显式
   `--allow-task-manager-local-validation` 且启动时再次校验 executor role。TaskManager session 暴露独立
   `node_validation` capability，React UI 为 ready validation node 提供专用运行、observe、verify、
   review 路径，不提供 Agent start 或直接完成。

### 文件与验证

1. 主要变更：`src/jobslayer/task_manager/execution.py`、
   `application/task_manager_execution.py`、`application/task_manager.py`、
   `adapters/task_manager_validation.py`、`orchestration/web.py`、`cli.py`、
   `ui-framework/src/types.ts`、`ui-framework/src/components/TaskManager.tsx`、
   `tests/test_task_manager_execution.py`、`tests/test_task_manager_validation.py`、
   `tests/test_orchestration_web.py`、TaskManager 手册、ADR-0043 与 ADR 索引。
2. `sh ./init.sh -- python -m unittest tests.test_task_manager_execution
   tests.test_task_manager_validation tests.test_orchestration_web`：24 项通过，用时 3.637 秒。覆盖允许
   路径、required check 失败进入 `Repairing` 且不得接受、缺 adapter 不改变 revision、稳定键幂等、
   request drift、脏 workspace、raw artifacts，以及 planner 对 validation 命令的 403 拒绝。
3. `sh ./init.sh -- python -m compileall -q src tests` 与 `git diff --check` 返回 0；
   `sh ./init.sh -- npm --prefix ui-framework run build` 使用 Node 24.19.0/npm 11.17.0 通过，转换
   2719 modules，TaskManager chunk 25.63 kB（gzip 8.31 kB）。直接使用系统 Node 18.19.1 会因项目
   已声明的 `node >=22.12` 前置条件在 Rolldown 启动时失败，统一 bootstrap 随后正确解析固定 Node。
4. `./jobslayer check`：9/9 全部通过，退出码 0，墙钟 29.1 秒；287 项 unittest 用时 27.166 秒，
   `OK (skipped=5)`；compile、dependency consistency、锁定 UI production build、BNW testbed、三份
   runbook binding 和 Git diff 门禁全部通过。

### 真实 DAG 证据与边界

1. 在既有 run `tmrun-f7891eb4a2cd4f0ca1aff2dec8073064` revision 45 上，用显式 validation 开关
   授权 `focused-automated-tests`。run revision 47 绑定 key
   `tmvalidate-992aa90dd3d7452f5fe5562e4f1f70ae`、adapter `local_validation`、provider run
   `validation-a8278014c6e8eeb60f028a86c1dfb4775ec2cb89f268f8a4316d673756f8584f` 和 start artifact
   `artifact-0172e8ecd8024cf0b063a4a8e2bf4965`；未发起 Codex 调用。
2. finalized checks `suspension-scenario` 与 `complete-bnw-suite` 分别用时 114 ms、2577 ms，exit 0；
   raw command artifacts 为 `artifact-3334d68cffb1448597c90b423b4d8330` 与
   `artifact-d999d07cee18471da849e13756c81d4b`。workspace 精确位于隔离 commit
   `11058bbeac74e01d16eeee3636c3ec936275f72c`，clean、无 changed paths。
3. report `tmverify-31552001b4054f6eb8987904244d5dd7` / artifact
   `artifact-964fb0320a62479f87d53d55a805dab6` 的两项命令与 terminal/workspace/path/evidence 四项结构
   checks 全部 required/passed。Reviewer evidence `artifact-d40921b074d44836b8caba732fbcd448`
   使 revision 50 经 Kernel 进入 `DeliverableAccepted`，并解锁 `documentation-and-development-log`。
4. 整个 validation 路径未改动 BNW worktree、未创建源码 commit，也未 merge/push/deploy。BNW 主检出
   仍保持 `main@fb43878c9f0164deef272e55969c0fc134a6d6a3` clean；后续文档源码节点仍必须走独立
   review/approval/isolated checkpoint，最终完成继续由剩余两次 validation 与 human gate 决定。

## DEV-2026-08-19-36 — 最终证据门禁与悬架 DAG 完整闭环

- 状态：完成（最终门禁实现、拒绝/兼容路径、全量门禁及真实 11-node DAG 均已闭环）
- 类型：final human approval、evidence binding、legacy run compatibility、TaskManager API/UI、真实运行
- 关联决策：[ADR-0044](adr/0044-evidence-bound-final-completion-gate.md)，并继续遵循
  [ADR-0041](adr/0041-verified-artifact-deliverable-acceptance.md) 与
  [ADR-0043](adr/0043-source-bound-deterministic-validation-nodes.md)

### 最终门禁基础设施

1. 新增 `approve_completion_gate` application command 与 `approve-completion` HTTP/UI 路径。它只接受
   有依赖、无下游消费者的 sink human gate，要求除自身外所有节点已受治理终态，且每个直接依赖都有
   passing verification report、verification artifact 与 reviewer acceptance 或 integration artifact。
2. 命令要求 `APPLY_DECISION`，并确定性拒绝直接依赖的最后 Reviewer 自批。decision artifact 固定
   plan id/revision/hash、run、gate、actor、理由和依赖 report/acceptance evidence；随后只通过
   `WorkflowKernel.transition` 进入 `GateApproved`，run stage 从全部节点终态确定性派生为 `Completed`。
3. 根范围 gate、非 sink 中间 gate、未完成依赖、缺少 report/acceptance、自批与空理由均不改变 run。
   旧 run 在尾部 gate 自动初始化为 `PlanReview` 规则加入前已装配时，允许先经 Kernel 追加
   `Planned -> PlanReview` 系统转换，再执行同一人类批准；不重写旧 transition history。
4. session capability 增加独立 `completion_approval`；TaskManager UI 只对 sink human gate 显示最终验收
   理由和批准动作。planner Web 请求返回 403；approver-only session 不需要 executor、validator 或
   source integration adapter。

### 后续 BNW 节点与真实证据

1. `documentation-and-development-log` 以 Codex `gpt-5.6-sol/xhigh` 从 11:50:16 至 11:57:53 UTC
   执行，exit 0、无 timeout/protocol failure；usage 为 input 1,774,159、cached input 1,661,440、
   output 22,404、reasoning output 5,723。候选只修改 README、架构、既有悬架 ADR 与追加式日志；
   worker sandbox 内 41/45 的 4 项 socket 限制被如实记录，宿主 `./bnw check` 为 4/4、45/45。
2. report `tmverify-a2eb26b87e5e4903b3a956706ef56233` 将 4 个路径与 patch
   `cada4e2414f9df270ae945bab8100305a9e920c92306e923d1be6c8ea46620bc` 绑定到 `11058bb...`；
   review `tmreview-5f779e4f29024ec1976a7e409ad2775b` 后，新的 60 分钟 approver-only session
   `suspension-demo-docs-approver` 固化 approval artifact
   `artifact-dc59196290354ea48f87ec7c20ae03e8`。隔离 commit
   `efa41058af1ef604a7e7c14d39ff5341d2081d8d` 的唯一前序为 `11058bb...`，重复集成保持 revision 62。
3. `full-repository-validation` 以 key `tmvalidate-21890685c0ecebe8b2570e529db89eeb` 在
   `efa41058...` 运行；场景/完整门禁分别 114/2575 ms、exit 0。report
   `tmverify-1d63e35e1d11454e8d99fefe2471e22c` 六项 required checks 全过，Reviewer evidence
   `artifact-00395d426eb2408f949f891bd5a4cb6b` 使 revision 67 进入 `DeliverableAccepted`。
4. `development-log-finalization` 从 12:00:59 至 12:04:50 UTC 执行，exit 0；usage 为 input
   636,770、cached input 561,920、output 11,231、reasoning output 5,061。它只在 BNW 日志末尾追加
   55 行，逐项转录原始命令时间、hash、stdout truncation、45/45、4/4 和相对 `bnw-0` 的 20 个候选
   文件，没有推写被截断的 metrics/trace hash。宿主再次 `./bnw check` 为 4/4、45/45。
5. 该单文件 patch `a3dca1451ac467651a6636b62cf0b61feccdba5745e5b898c74d4db46cda48f3`
   经 report `tmverify-1b78f9ab2b704255b1a2aaddbabca642`、review
   `tmreview-3175271bbdf1406b8bd252a514caa096`、approval artifact
   `artifact-8babe976072e4d44b1a43dfa2b905648` 后提交为隔离 commit
   `d17fd9486f0bcbfa01075ca11b8e350dc6c99d96`，唯一前序 `efa41058...`，run revision 76。
6. `final-consistency-validation` 再次在 `d17fd948...` 执行场景/完整门禁，均 exit 0、114/2575 ms；
   report `tmverify-7bfb839f6c5b4dbaa5ab8188315a36bb` 与 artifact
   `artifact-8c98d4497fa6456a836760537044dcc7` 的六项 required checks 全过，Reviewer artifact
   `artifact-2eb660cb73a746d7b8e874e22fae74b8` 使 revision 81 解锁最终 gate。
7. approver-only session 依据上述 report/review 和全部终态节点批准最终 gate。真实旧 run 先追加
   `Planned -> PlanReview`（system），再追加 `PlanReview -> GateApproved`（human
   `suspension-demo-docs-approver`）；decision artifact 为
   `artifact-6c201c813b564d8f98da85c0ef20fe23`。run revision 82 的 11 个节点全部终态，stage 为
   `completed`，并准确显示终态 blocker；该完成只属于隔离 TaskManager run。

### 验证、文件与边界

1. 最终门禁主要变更：`src/jobslayer/application/task_manager_execution.py`、
   `application/task_manager.py`、`orchestration/web.py`、`ui-framework/src/types.ts`、
   `ui-framework/src/components/TaskManager.tsx`、`tests/test_task_manager_execution.py`、
   `tests/test_orchestration_web.py`、TaskManager 手册、ADR-0044 与 ADR 索引。
2. 聚焦 `python -m unittest tests.test_task_manager_execution tests.test_orchestration_web`：22 项通过，
   用时 4.245 秒；覆盖未完成依赖与 reviewer 自批不写 revision、独立 Approver 允许路径、Kernel evidence
   和 planner 403。Node 24.19.0 的 UI production build 通过，转换 2719 modules，TaskManager chunk
   26.27 kB（gzip 8.46 kB）；compileall 与 `git diff --check` 返回 0。
3. `./jobslayer check`：9/9 全部通过，退出码 0，墙钟 29.3 秒；288 项 unittest 用时 27.369 秒，
   `OK (skipped=5)`；compile、dependencies、UI、BNW testbed、三份 runbook binding 和 diff 门禁全过。
4. BNW 隔离分支最终 clean，HEAD 为 `d17fd948...`；主检出仍是
   `main@fb43878c9f0164deef272e55969c0fc134a6d6a3` 且 clean。全程没有 checkout/merge 主干、push、
   deployment、release 或远端变更；将完整候选带入主干仍需另行、明确的集成授权。
