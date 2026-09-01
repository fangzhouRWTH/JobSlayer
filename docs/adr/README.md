# JobSlayer 架构决策索引

| ADR | 状态 | 决定 |
|---|---|---|
| [ADR-0001](0001-owned-control-plane.md) | Accepted | 项目拥有控制平面和领域模型，外部 Agent/运行时通过适配器接入 |
| [ADR-0002](0002-task-isolated-git-worktrees.md) | Accepted | 每个可写任务从固定 commit 建立独立 Git worktree 和任务分支 |
| [ADR-0003](0003-governed-local-command-runner.md) | Accepted | 本地 runner 只执行可信政策允许的命令，并明确不等同于安全沙箱 |
| [ADR-0004](0004-cli-first-human-supervision.md) | Accepted | 先用结构化决策卡和 CLI 建立人工监督闭环，Web UI 后置 |
| [ADR-0005](0005-codex-cli-executor-adapter.md) | Accepted | 通过 `codex exec --json` 接入可取消执行器并保留原始事件证据 |
| [ADR-0006](0006-evidence-backed-application-controller.md) | Accepted | 用内容寻址制品、版本化验证和确定性控制器串联一次受治理实现闭环 |
| [ADR-0007](0007-loopback-visual-review-surface.md) | Accepted | 用 loopback、零前端依赖的极简页面展示真实证据并生成 create-only 人工决定 |
| [ADR-0008](0008-unified-source-and-installed-entrypoint.md) | Accepted | 源码脚本、模块、安装后 CLI、UI 和完整开发验证汇合到同一公共 launcher |
| [ADR-0009](0009-fixed-local-testbed-baseline.md) | Accepted | 显式区分本地/已发布测试床基线，并用只读 Git adapter 检查 commit、tag、origin 和工作树 |
| [ADR-0010](0010-source-controlled-local-run-assembly.md) | Accepted | 用版本化 runbook、确定性 replay adapter 和双哈希链装配可恢复的本地真实运行 |
| [ADR-0011](0011-explicitly-authorized-codex-runbooks.md) | Accepted | 真实 Codex runbook 必须由运行时外部人类显式授权，并继续服从同一治理闭环 |
| [ADR-0012](0012-evidence-gated-local-fast-forward-integration.md) | Accepted | 人工批准先进入 Integrating，只有审核补丁完成受控本地快进并形成证据后才能 Completed |
| [ADR-0013](0013-cross-platform-local-control-plane.md) | Accepted | 用双平台入口、进程监督和字节稳定 I/O 支持原生 Windows 与 POSIX 本地控制平面 |
| [ADR-0014](0014-unified-cli-and-process-supervisor-interface.md) | Accepted | 对外统一 jobslayer CLI，对内用可注入 ProcessSupervisor 隔离平台进程语义 |
| [ADR-0015](0015-evidence-backed-phase0-readiness-gate.md) | Accepted | 用完整性校验后的运行语料量化 Phase 0 自动退出条件，并保留人工确认边界 |
| [ADR-0016](0016-evidence-bounded-local-run-recovery.md) | Accepted | 本地恢复只重建权威证据可证明的派生投影，不覆盖篡改或猜测工作流状态 |
| [ADR-0017](0017-read-only-git-attestation-before-integration-record-recovery.md) | Accepted | 源码集成记录恢复前只读证明 Git、完成转换和原始集成制品完全一致 |
| [ADR-0018](0018-prefix-preserving-atomic-jsonl-publication.md) | Accepted | 本地 workflow/run 追加链通过前缀保持的原子 generation 发布消除 partial JSON |
| [ADR-0019](0019-replay-free-decision-and-cleanup-record-recovery.md) | Accepted | 决定应用与 workspace cleanup 只根据原转换/制品/Git 后置事实补写记录，不重放副作用 |
| [ADR-0020](0020-execution-intent-outcome-recovery-boundary.md) | Accepted | Agent 前持久化不可变 intent，只有严格 outcome 已落盘时才自动补写 execution 首记录 |
| [ADR-0021](0021-source-defined-cross-platform-phase0-corpus.md) | Accepted | 用源码定义经真实服务重建跨平台 Phase 0 语料，并明确隔离 fixture 与真实人工确认 |
| [ADR-0022](0022-transactional-state-port-and-sqlite-contract-adapter.md) | Accepted | 先用 provider-neutral 事务端口和 SQLite contract adapter 固化版本、回滚、迁移与 outbox 语义 |
| [ADR-0023](0023-fail-closed-long-running-development-safety.md) | Accepted | 完整主机权限不扩大授权；长时开发限制在显式作用域、默认拒绝并及时回收临时服务 |
| [ADR-0024](0024-transactional-postgres-control-plane-and-buffered-coordinator.md) | Accepted | PostgreSQL/SQLite 共享事务契约，Kernel 缓冲 transition 在长动作后与 run/artifact/outbox 原子提交 |
| [ADR-0025](0025-authenticated-control-plane-and-agent-credential-grants.md) | Accepted | 公共写入口使用认证 RBAC 与签名 authority，Agent 只接受短期最小权限 grant |
| [ADR-0026](0026-enforcement-backed-worker-sandbox-budget-and-context.md) | Accepted | 强沙箱、worker lease、预算与内容寻址上下文由统一治理执行器在 launch 前强制 |
| [ADR-0027](0027-persistent-management-observability-and-executor-evaluation.md) | Accepted | 管理面只读事务事件真相，并提供 OTel 端口与同契约执行器比较 |
| [ADR-0028](0028-isolated-web-workbench-interaction-prototype.md) | Accepted | 在隔离目录用成熟 Web 组件验证工程工作台交互，UI 不拥有控制面语义或状态 |
| [ADR-0029](0029-cross-platform-manifest-driven-development-bootstrap.md) | Accepted | 用统一 init、固定校验的用户级 Node 和仓库 venv 提供跨平台、只读可检测的开发环境初始化 |
| [ADR-0030](0030-unified-gate-for-locked-ui-dependencies.md) | Accepted | 把 lockfile 外部 UI 栈的类型检查和 production build 纳入统一完成门禁与 CI |
| [ADR-0031](0031-versioned-collaborative-task-orchestration.md) | Accepted | 用版本化 DAG、Agent 待应用提案和追加式定稿记录建立协作任务编排纵向切片 |
| [ADR-0032](0032-governed-interactive-planning-workbench.md) | Accepted | 用结构化节点、计划评估、语义边、版本派生和非权威本地布局完善交互式规划工作台 |
| [ADR-0033](0033-evidence-bound-codex-planning-adapter.md) | Accepted | Codex 只返回经 Schema/DAG 校验的提案草稿，原始交互绑定制品且必须显式启用 |
| [ADR-0034](0034-authenticated-bounded-planning-artifact-viewer.md) | Accepted | 规划制品只通过认证、plan-bound、去 URI 且哈希验证的有界文本预览进入 Workbench |
| [ADR-0035](0035-provider-neutral-resumable-long-running-control-plane.md) | Accepted | 长任务用持久 start identity、多维预算、lease、证据 checkpoint 与显式 retry 实现保守恢复 |
| [ADR-0036](0036-focused-task-manager-product-surface.md) | Accepted | 主产品面收紧为 TaskManager facade，复用既有规划/执行真相并显式暴露执行缺口 |
| [ADR-0037](0037-plan-bound-task-manager-run-assembly.md) | Accepted | TaskManager run 精确绑定 finalized revision，以 Kernel 状态、幂等启动键和制品证据推进反馈 |
| [ADR-0038](0038-source-pinned-bravenewworld-execution-target.md) | Accepted | 以源包哈希、固定 BNW 基线和确定性预检把 TaskManager 计划绑定到显式 BraveNewWorld 目标 |
| [ADR-0039](0039-durable-task-manager-codex-worker.md) | Accepted | 以持久启动身份、独立 worker、运行级 worktree 和原始证据实现 API 重启可定位的本机 Codex 任务执行 |
| [ADR-0040](0040-plan-finalization-bound-root-human-gate.md) | Accepted | 用独立 GateApproved Kernel 终态和不可变决定证据把根范围门禁绑定到显式 finalized revision |
| [ADR-0041](0041-verified-artifact-deliverable-acceptance.md) | Accepted | 用确定性 workspace 证据和独立 Reviewer 接受无源码差异的阶段性交付物 |
| [ADR-0042](0042-independent-source-review-and-isolated-run-checkpoint.md) | Accepted | 用独立源码审查/审批和幂等 run-branch 检查点完成源码节点，不触碰主干发布边界 |
| [ADR-0043](0043-source-bound-deterministic-validation-nodes.md) | Accepted | 用 finalized profile、受策略约束的本地 runner 与 TaskManager-owned report 执行 validation node |
| [ADR-0044](0044-evidence-bound-final-completion-gate.md) | Accepted | 用最终 passing/acceptance 证据和独立 Approver 决定 sink human gate 与 run 完成 |
| [ADR-0045](0045-focused-serial-task-manager-and-anygine-app-testbed.md) | Accepted | 中期主线收紧为串行 TaskManager，并把 BraveNewWorld 重置为固定 Anygine 公共接口的小 App 测试床 |
| [ADR-0046](0046-content-bound-local-dependency-attachments.md) | Accepted | 用源控需求、operator 路径、内容摘要和前后漂移证据将 Anygine 依赖接入隔离 validation |
| [ADR-0047](0047-single-screen-task-graph-preview.md) | Accepted | 当前 Web App 只装配单屏任务图预览，以左 2/3 DAG 和右 1/3 节点详情/Agent 对话验证规划交互 |

新增或取代 ADR 时，同时更新本索引和 `docs/DEVELOPMENT_LOG.md`。Accepted ADR 不通过静默编辑改变原决定；需要改变方向时新增 superseding ADR。
