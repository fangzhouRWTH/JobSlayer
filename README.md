# JobSlayer

JobSlayer 是一个面向复杂工程项目的 AI 协同开发控制平面。它把 AI 执行器视为可替换的劳动力，把任务状态、权限、验证、证据与完成判定保留在确定性代码中。

仓库当前处于 **Phase 0：研究骨架与契约验证**。已经提供：

- 提供方无关的任务、执行、事件、制品和验证契约；
- 由代码控制的任务状态机；
- 带哈希链校验的追加式 JSONL 审计日志；
- 一个可运行的闭环演示和标准库测试；
- 项目指导、架构决策和分阶段路线图。

## 快速开始

需要 Python 3.11 或更高版本。

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/jobslayer demo --journal .jobslayer/demo.jsonl
```

演示会依次经过：

```text
Draft -> Planned -> Implementing -> Verifying -> Reviewing
      -> MergeReview -> Completed
```

它只演示控制平面，不会调用真实模型、修改外部仓库或合并代码。

## 文档入口

- [项目开发指导](docs/PROJECT_GUIDE.md)
- [初步实施路线图](docs/ROADMAP.md)
- [控制平面架构决策](docs/adr/0001-owned-control-plane.md)
- [示例任务](examples/task.example.json)

## 当前边界

本阶段刻意不包含 PostgreSQL、Web UI、Codex/OpenHands 真实适配器、容器隔离、Dagger、Temporal、Ray 或 Kubernetes。这些能力必须建立在稳定的领域契约和首个真实闭环之上，按路线图逐步引入。

