# ADR-0015：用证据化运行语料门禁 Phase 1 基础设施

- 状态：Accepted
- 日期：2026-08-11

## 背景

Phase 1 依赖稳定的 Phase 0 闭环和至少 20 个内部样例复盘，但此前这一退出条件只存在于路线图文本中。开发者可以看到单个 run，却不能确定本地语料是否完整、是否包含真实决定后的完成路径、是否保留失败证据，也无法用机器门禁防止在证据不足时过早引入数据库或分布式基础设施。

## 决定

1. 新增只读 `Phase0ReadinessEvaluator`，通过 `RunInspector` 协议消费已有 run 摘要；本地 adapter 复用 `LocalRunCoordinator.inspect`，不解析出第二套工作流状态。
2. 自动门禁要求：所有发现的 run 通过 record chain、workflow journal 和 artifact 完整性检查；至少 20 个不同 task 已完成实现审查；至少一个 run 在决定应用和源码集成后完成；至少一个 run 保留 failed、repairing 或 cancelled 负路径。
3. 自动报告不拥有阶段状态，也不签发人工确认。真实人工体验复盘和 Windows/POSIX CI 确认固定保留为报告中的手工要求。
4. `inspect-readiness` 是只读命令；证据不足时返回非零和结构化缺口，不创建、修复、删除或转换任何 run。

## 理由

- 复用已经验证的 run inspection 可以保持单一工程真相，并让损坏证据自然阻塞门禁。
- 把自动证据和人工复盘分开，避免一个计数器伪造产品体验或审批事实。
- 在 PostgreSQL、Dagger、OpenTelemetry 等依赖进入前先量化真实样例，可以让后续 schema 和事件设计来自观察到的运行，而不是假设。

## 后果

- 新 checkout 或没有持久 run 语料的环境会按预期得到失败报告；这不是开发测试失败。
- 当前本地 JSONL 仍不是生产事务存储；门禁只能检查已有证据，不能弥补跨文件提交窗口。
- 下一项工作是定义持久化/恢复协议和故障注入，达到退出条件后再为 PostgreSQL adapter 编写独立 ADR。

## 未选择的方案

- 把 readiness 加入 `jobslayer check`：拒绝。源码完整性检查不应因为本地运行语料缺失而失败。
- 只统计目录或 JSONL 行数：拒绝。计数不能证明哈希链、制品和工作流绑定有效。
- 自动把阶段标记为 Phase 1：拒绝。阶段决定仍需要人工复盘和路线图更新。
