# ADR-0016：本地恢复只重建可证明的派生投影

- 状态：Accepted
- 日期：2026-08-11

## 背景

Phase 0 的 workflow journal、run ledger、artifact registry 和便于 UI 消费的 `decision-card.json` 分别落盘。正常路径会先由控制器注册审查/卡片制品并转换到 `MergeReview`，再追加 run record，最后写出卡片投影。如果进程在最后一步前退出，权威证据完整但 UI 投影缺失；此前重试 `review-run` 会因 stage 已推进而拒绝，操作员也无法区分这种安全可恢复缺口与 journal/ledger 不一致或文件篡改。

## 决定

1. 定义提供方无关的 `RunRecoveryManager`、`RecoveryAssessment` 和四种状态：`consistent`、`recoverable`、`manual_intervention`、`invalid_evidence`。
2. 本地 adapter 必须先复用 `LocalRunCoordinator.inspect` 验证 run ledger、workflow journal、制品和状态绑定；恢复层不推导第二套工作流状态。
3. 首个自动恢复动作只支持：权威 `ReviewDisposition.merge_review_package` 已进入 ledger、全部证据有效，但 create-only `decision-card.json` 投影不存在时，按 ledger 中的严格 `DecisionCard` 重建投影。
4. 恢复写入使用 `O_EXCL`、完整写入和 `fsync`。重复恢复健康 run 是幂等只读操作；并发出现可信投影也视为成功。
5. 已存在但无效/不匹配/为符号链接的投影绝不覆盖。journal 与 ledger 不一致、空 run 目录或无法重构的内容升级为人工处理，不自动补写状态或记录。
6. `inspect-recovery` 只分类；`recover-run` 只执行明确支持的安全动作。二者都不能调用 `WorkflowKernel.transition`、重新执行 Agent、重新评审或猜测 Git 完成事实。

## 理由

- `decision-card.json` 是由已验证 ledger 内容产生的便捷投影，不是新的工程真相；缺失时可确定性重建。
- 覆盖一个可疑文件会销毁事故或篡改证据，因此必须停止而不是“修好”。
- 先实现一个窄、可故障注入的恢复动作，可以为后续事务存储和幂等键提供真实语义，而不提前引入数据库。

## 后果

- review 后、投影写入前的崩溃窗口可以恢复，且不重复 Agent、验证、审查或状态转换。
- 其他跨文件窗口暂时只分类为人工处理；ST-01 后续切片需要逐一建立幂等提交事实。
- 本地文件系统仍没有跨文件事务；PostgreSQL/outbox adapter 仍被 Phase 0 语料和恢复协议门禁阻塞。

## 未选择的方案

- 失败时重新运行 `review-run`：拒绝，会重复业务动作并与已推进的 kernel 状态冲突。
- 自动截断或重写 journal/ledger：拒绝，会破坏追加证据和工程真相。
- 删除可疑投影后重建：拒绝，除非未来有显式人工恢复决定和保留原文件的隔离流程。
