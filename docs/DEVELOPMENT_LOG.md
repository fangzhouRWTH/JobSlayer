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
