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

新增或取代 ADR 时，同时更新本索引和 `docs/DEVELOPMENT_LOG.md`。Accepted ADR 不通过静默编辑改变原决定；需要改变方向时新增 superseding ADR。
