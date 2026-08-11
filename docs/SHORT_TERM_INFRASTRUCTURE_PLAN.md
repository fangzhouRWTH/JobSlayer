# JobSlayer 短期基础设施开发计划

## 目标

在进入更多 Agent、领域验证和产品功能前，把 JobSlayer 从本地 Phase 0 研究闭环推进到可恢复、可授权、可观测的 Phase 1 工程 MVP。计划以退出证据为准，不以功能数量或日期宣称完成。

所有实现继续遵守三个约束：工作流状态只能由 `WorkflowKernel.transition` 改变；外部 Agent 不拥有权限、预算、重试或完成判定；新基础设施先定义提供方无关端口，再选择 adapter。

## 当前基线

- 本地单任务路径已经覆盖固定基线 worktree、Codex/scripted executor、补丁、确定性验证、独立审查、决定应用、本地 fast-forward、完成证据与 cleanup。
- Windows/POSIX 已有统一公共 CLI 和进程监督协议。
- 本地 JSONL、内容寻址制品和单任务审查 UI 可用，但没有多进程事务、认证身份、强沙箱、执行前预算或项目仪表盘。
- Phase 1 的入口条件仍要求稳定闭环、至少 20 个经过复盘的内部样例，以及一次真实人工计划/决定体验。

## 实施顺序

| ID | 状态 | 基础设施切片 | 可验证退出条件 |
|---|---|---|---|
| ST-00 | 执行中 | Phase 0 证据门禁与样例语料 | `inspect-readiness` 校验全部 run 的双哈希链、制品、审查、成功闭环和负路径；至少 20 个不同 task 已审查；人工复盘另行确认 |
| ST-01 | 执行中 | 持久化端口、恢复协议与故障注入 | 每个阶段中断后可只读判定一致/可恢复/不可恢复；恢复操作幂等；不重复执行 Agent 或 Git 集成 |
| ST-02 | 被 ST-00/ST-01 阻塞 | PostgreSQL 元数据、迁移与事务 outbox | 重启后状态和证据不丢失；并发写入有序；workflow/run/artifact 元数据在一个明确事务边界内提交 |
| ST-03 | 待开始 | 身份、授权与凭据端口 | UI/CLI 写操作绑定认证主体；越权不改变状态；authority 可验证；Agent 只得到短期、最小权限凭据 |
| ST-04 | 待开始 | Sandbox/worker 与资源政策 | 网络、挂载、CPU、内存、超时和进程树限制可验证；worker 有 lease、心跳、取消和孤儿回收 |
| ST-05 | 待开始 | 预算、上下文包和有界修复 | 执行前预算预留、执行中扣减、超限取消；上下文有版本/哈希/大小；修复次数由确定性政策限制 |
| ST-06 | 被 ST-02/ST-03 阻塞 | 查询 API、OpenTelemetry 与管理 Dashboard | 可查看任务/run/事件/验证/制品/成本/审批；实时视图来自持久事件；所有写操作仍经过 kernel 和授权服务 |
| ST-07 | 被样例语料阻塞 | 双执行器对比与回归评测 | 两种 executor 使用相同任务/验证契约；结果、成本和人工干预率可重复比较 |

## ST-00 当前执行项

已实现或正在实现：

- [x] 用只读 `RunInspector` 端口复用现有 `LocalRunCoordinator.inspect`，不创建第二套运行真相。
- [x] `jobslayer inspect-readiness` 汇总有效、已审查、已完成、决定已应用完成和负路径 run。
- [x] 任一 run 的 ledger、journal、制品或摘要绑定无效时自动门禁失败。
- [x] 自动门禁与人工确认分离；命令不会替代真实人工复盘或改变阶段状态。
- [ ] 建立至少 20 个可保留的内部样例 run，覆盖成功、验证失败、要求修改和取消。
- [ ] 完成一次真实人工计划/决定体验并把复盘追加到开发日志。
- [ ] 在 Windows 和 POSIX CI 中对同一语料运行门禁。

运行方式：

```text
jobslayer inspect-readiness --state-root .jobslayer --required-reviewed-tasks 20
```

自动证据不足时命令返回非零并列出未满足条件。即使自动条件通过，报告仍固定标记 `manual_confirmation_required: true`。

## ST-01 设计边界

下一个切片只定义持久化/恢复语义和故障测试，不立即引入数据库：

1. 为 run 聚合、transition、artifact metadata 和可靠事件定义提供方无关端口。
2. 明确每个命令的幂等键、预期前置阶段和提交后事实。
3. 在 journal 已追加而 run ledger 未追加、制品已写而记录未提交、Git commit 已创建而 run record 未落盘等位置注入故障。
4. 恢复器只能根据可验证证据前进；证据不足时停止并输出人工恢复卡，不猜测完成。
5. ST-01 通过后再用 ADR 选择 PostgreSQL schema、迁移工具和 outbox adapter。

当前进展：

- [x] 定义 `RunRecoveryManager`、结构化 assessment 和一致/可恢复/人工处理/无效证据四种状态。
- [x] 支持从权威 review ledger 幂等重建缺失的 `decision-card.json` 派生投影。
- [x] 写入故障会移除本次创建的不完整投影，保留再次安全恢复的条件。
- [x] 卡片篡改、符号链接、journal/ledger 不一致和无执行记录均拒绝自动恢复。
- [ ] 为 execution、decision application、source integration 和 cleanup 的提交窗口定义幂等键与恢复动作。
- [ ] 增加进程级 crash harness，验证真实进程退出而不只是文件级故障注入。

## 短期明确后置

Temporal、Ray、Kubernetes、远程 GPU、多租户平台、A2A、生产部署和复杂前端框架不属于本计划前置项。Langfuse/Phoenix、Promptfoo 和第二个 Agent adapter 也要等统一事件、样例语料和持久查询边界稳定后再评估。

## 完成定义

每个切片完成时必须追加 `docs/DEVELOPMENT_LOG.md`，记录变更文件、正反测试、完整 `./jobslayer check` 结果、限制和下一步。涉及持久化真相、权限或基础设施选型的决定必须新增 ADR；不能通过修改旧记录掩盖方向变化。
