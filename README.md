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
- 一个可运行的闭环演示和标准库测试；
- 项目指导、架构决策和分阶段路线图。

## 快速开始

需要 Python 3.11 或更高版本。安装并激活项目环境后，Windows 与 POSIX 使用
完全相同的公共接口：

```text
jobslayer <子命令> [参数]
python -m jobslayer <子命令> [参数]
```

例如两端均可运行 `jobslayer check` 或 `python -m jobslayer check`。前者是
安装包生成的 console script，后者是源码/模块入口；两者进入同一个 launcher。
平台脚本只用于尚未激活环境时的 bootstrap。

POSIX（Linux/macOS/WSL）初始化使用：

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
./jobslayer check
./jobslayer validate-testbed testbeds/brave-new-world.json
./jobslayer inspect-testbed testbeds/brave-new-world.json
./jobslayer validate-runbook runbooks/bnw-scenario-slow-001.json
./jobslayer validate-runbook runbooks/bnw-filter-demo-001-codex.json
./jobslayer inspect-run .jobslayer/runs/bnw-scenario-slow-001-run-01
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

Windows PowerShell 使用原生 Python，不要求 WSL：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\jobslayer.cmd check
.\jobslayer.cmd validate-testbed testbeds/brave-new-world.json
.\jobslayer.cmd serve-dashboard `
  --state-root .jobslayer/phase0-corpus/state `
  --identity-session .jobslayer/identity/observer.json `
  --identity-key .jobslayer/identity/key.json `
  --open-browser
```

下文的 `./jobslayer` 在 Windows 上均对应 `.\jobslayer.cmd`。JobSlayer
控制平面和完整开发门禁可原生运行；当前 BraveNewWorld 源控 runbook 仍绑定
测试床自身的 `./bnw` POSIX 验证入口，因此实际执行这些 BNW runbook 时仍需
兼容该命令的环境，直到测试床另行提供 Windows 验证入口。

演示会依次经过：

```text
Draft -> Planned -> Implementing -> Verifying -> Reviewing
      -> MergeReview
```

它只演示到 `MergeReview` 的控制平面，不会伪造集成证据、调用真实模型、修改外部仓库或合并代码。完整本地成功路径见下方体验手册。

## 文档入口

- [项目开发指导](docs/PROJECT_GUIDE.md)
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

## 首个实验项目

BraveNewWorld 是 JobSlayer 的首个外部测试床，用于验证真实仓库中的任务治理、隔离执行和证据闭环。仓库地址、BNW-0 固定 commit、`bnw-0` 标签和验证入口已登记在 [`testbeds/brave-new-world.json`](testbeds/brave-new-world.json)。

```bash
git clone https://github.com/fangzhouRWTH/BraveNewWorld.git
```

BraveNewWorld 有独立的教学产品目标，但不属于 JobSlayer 控制平面的源码；JobSlayer 只保存它的项目登记、任务规格、运行记录和验证证据。当前 BNW-0 已在本机固定，但尚未推送，登记中的 `published` 因而保持 `false`；在首次发布前，其他开发端通过上述地址只能看到空远端。

## 当前边界

当前具备本地认证、事务控制平面和项目 Dashboard，但仍不是远程多用户生产平台。PostgreSQL adapter 已由真实 PostgreSQL 16 合同测试覆盖；SQLite 是无需外部服务的开发后端。Linux worker 可使用 bubblewrap 的默认拒绝式网络、挂载和资源隔离；原生 Windows 没有被冒充成等价强沙箱，要求强隔离的任务会失败关闭，可由同一接口路由到 WSL/Linux worker。

尚未实现的能力包括远程对象存储、真实短期模型凭据下发 adapter、第二个真实模型执行器、自动修复编排、Dagger、Temporal、Ray、Kubernetes、push 和部署。决定应用、集成与清理都要求签名身份和各自权限；集成仍只允许在证据完全匹配时执行本地 fast-forward。完整阶段状态以[短期基础设施计划](docs/SHORT_TERM_INFRASTRUCTURE_PLAN.md)和[路线图](docs/ROADMAP.md)为准。
