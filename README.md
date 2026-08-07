# JobSlayer

JobSlayer 是一个面向复杂工程项目的 AI 协同开发控制平面。它把 AI 执行器视为可替换的劳动力，把任务状态、权限、验证、证据与完成判定保留在确定性代码中。

仓库已完成 **Phase 0 初步框架**，正在用真实任务进入 Phase 1 验证。已经提供：

- 提供方无关的任务、执行、事件、制品和验证契约；
- 由代码控制的任务状态机；
- 带哈希链校验的追加式 JSONL 审计日志；
- 从固定 commit 创建、检查和安全清理的任务级 Git worktree；
- 按任务路径策略收集并哈希化补丁；
- 按可信命令规则执行、超时终止并生成输出证据的本地 runner；
- 版本化 `ValidationProfile` 和把失败也保留下来的验证引擎；
- 内容寻址、持久清单并在读取时复核哈希的本地制品注册表；
- 基于同一结构化决策卡的人工监督 CLI 和 loopback 极简可视化界面；
- 带事件序列、取消和原始日志证据的 `AgentExecutor`/Codex CLI adapter；
- 串联工作区、Agent、补丁、验证与合并决策卡的应用控制器；
- 统一源码、UI、完整开发验证与安装后 CLI 的根入口脚本；
- 由版本化 task/profile/runbook 驱动的本地真实运行协调器；
- 必须由外部显式授权、且仍服从相同工作树/验证/审查门禁的真实 Codex runbook；
- append-only 运行记录链、确定性补丁重放 adapter 和 run 级监督入口；
- 审批后复核补丁/基线、创建单一提交、只做本地 fast-forward 并保留集成证据的 adapter；
- 一个可运行的闭环演示和标准库测试；
- 项目指导、架构决策和分阶段路线图。

## 快速开始

需要 Python 3.11 或更高版本。

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
./jobslayer check
./jobslayer validate-testbed testbeds/brave-new-world.json
./jobslayer inspect-testbed testbeds/brave-new-world.json
./jobslayer validate-runbook runbooks/bnw-scenario-slow-001.json
./jobslayer validate-runbook runbooks/bnw-filter-demo-001-codex.json
./jobslayer inspect-run .jobslayer/runs/bnw-scenario-slow-001-run-01
./jobslayer demo --journal .jobslayer/demo.jsonl
./jobslayer review-decision examples/decision-card.example.json \
  --actor-id local-reviewer --output .jobslayer/example-decision.json
./jobslayer ui examples/decision-card.example.json \
  --actor-id local-reviewer \
  --output .jobslayer/example-visual-decision.json \
  --open-browser
```

演示会依次经过：

```text
Draft -> Planned -> Implementing -> Verifying -> Reviewing
      -> MergeReview
```

它只演示到 `MergeReview` 的控制平面，不会伪造集成证据、调用真实模型、修改外部仓库或合并代码。完整本地成功路径见下方体验手册。

## 文档入口

- [项目开发指导](docs/PROJECT_GUIDE.md)
- [初步实施路线图](docs/ROADMAP.md)
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

本阶段刻意不包含 PostgreSQL、远程/多用户 Web 平台、OpenHands adapter、生产级容器隔离、Dagger、Temporal、Ray 或 Kubernetes。当前只有 loopback 单决策审查页，不具备认证身份、远程共享或项目仪表盘能力。决定应用必须由外部有效 authority 授权，批准后只进入 `Integrating`；另一个显式命令才会在补丁、提交树、基线和干净目标全部匹配时执行本地 fast-forward。真实 Codex 候选仍停在 `MergeReview`，没有决定、提交或合并；执行前成本强制、外层网络/资源隔离、自动修复、push 和部署仍未实现。
