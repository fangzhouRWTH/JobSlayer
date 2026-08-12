# JobSlayer 短期基础设施开发计划

## 目标

在进入更多 Agent、领域验证和产品功能前，把 JobSlayer 从本地 Phase 0 研究闭环推进到可恢复、可授权、可观测的 Phase 1 工程 MVP。计划以退出证据为准，不以功能数量或日期宣称完成。

所有实现继续遵守三个约束：工作流状态只能由 `WorkflowKernel.transition` 改变；外部 Agent 不拥有权限、预算、重试或完成判定；新基础设施先定义提供方无关端口，再选择 adapter。

## 本阶段安全策略

当前开发会话即使具有完整系统权限，也只把它视为执行能力而不是扩大授权。全部长时
开发遵循 [ADR-0023](adr/0023-fail-closed-long-running-development-safety.md)：限制在
本 workspace 与显式 `/var/tmp/jobslayer-*` 验证目录；默认拒绝缺失身份、隔离、预算、
lease 或完整证据的操作；不做系统级安装、远端 push/部署或未授权外部写入；临时
服务在取得证据后停止。任何删除或进程回收都先核对绝对目标/精确命令行。

## 当前基线

- 本地单任务路径已经覆盖固定基线 worktree、Codex/scripted executor、补丁、确定性验证、独立审查、决定应用、本地 fast-forward、完成证据与 cleanup。
- Windows/POSIX 已有统一公共 CLI 和进程监督协议。
- Phase 0 JSONL 继续服务旧语料和故障恢复；Phase 1 已有 SQLite/PostgreSQL 事务真相、认证身份、强隔离端口、预算/上下文治理和只读项目 Dashboard。
- Phase 1 入口条件已由 21 个跨 Windows/WSL 可重建 run、20 个不同已审查 task 和一次真实人工决定体验满足。

## 实施顺序

| ID | 状态 | 基础设施切片 | 可验证退出条件 |
|---|---|---|---|
| ST-00 | 已完成（本地 Windows/WSL 与真实人工决定） | Phase 0 证据门禁与样例语料 | `inspect-readiness` 校验全部 run 的双哈希链、制品、审查、成功闭环和负路径；至少 20 个不同 task 已审查；人工复盘另行确认 |
| ST-01 | 已完成（本地单控制器） | 持久化端口、恢复协议与故障注入 | 每个阶段中断后可只读判定一致/可恢复/不可恢复；恢复操作幂等；不重复执行 Agent 或 Git 集成 |
| ST-02 | 已完成 | PostgreSQL 元数据、迁移与事务 outbox | 重启后状态和证据不丢失；并发写入有序；workflow/run/artifact 元数据在一个明确事务边界内提交 |
| ST-03 | 已完成（本地签名 adapter + provider-neutral 生产端口） | 身份、授权与凭据端口 | UI/CLI 写操作绑定认证主体；越权不改变状态；authority 可验证；Agent 只得到短期、最小权限凭据 |
| ST-04 | 已完成（Linux 强隔离；Windows fail-closed 通用接口） | Sandbox/worker 与资源政策 | 网络、挂载、CPU、内存、超时和进程树限制可验证；worker 有 lease、心跳、取消和孤儿回收 |
| ST-05 | 已完成 | 预算、上下文包和有界修复 | 执行前预算预留、执行中扣减、超限取消；上下文有版本/哈希/大小；修复次数由确定性政策限制 |
| ST-06 | 已完成 | 查询 API、OpenTelemetry 与管理 Dashboard | 可查看任务/run/事件/验证/制品/成本/审批；实时视图来自持久事件；所有写操作仍经过 kernel 和授权服务 |
| ST-07 | 已完成（确定性 adapter 回归基线） | 双执行器对比与回归评测 | 两种 executor 使用相同任务/验证契约；结果、成本和人工干预率可重复比较 |

## ST-00 当前执行项

已实现或正在实现：

- [x] 用只读 `RunInspector` 端口复用现有 `LocalRunCoordinator.inspect`，不创建第二套运行真相。
- [x] `jobslayer inspect-readiness` 汇总有效、已审查、已完成、决定已应用完成和负路径 run。
- [x] 任一 run 的 ledger、journal、制品或摘要绑定无效时自动门禁失败。
- [x] 自动门禁与人工确认分离；命令不会替代真实人工复盘或改变阶段状态。
- [x] 用源码定义和真实服务建立 21 个可保留的内部样例 run；20 个不同 task 已审查，并覆盖成功、验证失败、要求修改和取消。
- [x] 完成一次真实人工计划/决定体验并把复盘追加到开发日志。
- [x] 原生 Windows 与 Ubuntu/WSL2 已对同一语料定义运行完整门禁；GitHub Actions 的 Windows/Ubuntu matrix 已固化，待提交后取得远端状态。

运行方式：

```text
jobslayer inspect-readiness --state-root .jobslayer --required-reviewed-tasks 20
jobslayer build-phase0-corpus
jobslayer inspect-readiness --state-root .jobslayer/phase0-corpus/state --required-reviewed-tasks 20
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
- [x] 闭合 Git 已快进、集成制品与 `Completed` 已提交但 `source_integration` run record 缺失的窗口；恢复只读复核 Git/制品/转换并复用原 artifact id，不重新集成。
- [x] 用独立 Python 子进程在 `Completed` 已 fsync、`source_integration` append 尚未调用的精确边界执行 `os._exit`，验证新进程可从真实落盘状态恢复。
- [x] workflow journal 与 run ledger 均使用前缀保持的原子 generation 发布；partial write、replace 前退出只保留旧链，replace 后退出只暴露完整新链。
- [x] decision transition 显式引用原 decision/authority 制品；崩溃后只读验证已应用转换并补写 ledger，不重新转换。
- [x] cleanup 通过 provider-neutral removal inspection 证明路径/注册消失和 source branch/commit 保留；崩溃后不重复 remove。
- [x] execution 在 Agent 前持久化 provider-neutral intent；严格 outcome 已落盘时补写首记录，只有 intent 时明确人工处理且绝不重跑 Agent。
- [x] execution 进程级 crash harness 分别覆盖 outcome 已落盘的自动恢复和 intent-only 的拒绝重跑。

## ST-02 当前执行项

- [x] `WorkflowKernel` 只依赖 provider-neutral `AuditJournal`；JSONL 不再进入 kernel 类型边界。
- [x] 定义 `ControlPlaneStore`/`StateTransaction`，明确 task/run expected sequence、显式 commit/rollback 和同事务 outbox。
- [x] 标准库 SQLite contract adapter 已覆盖迁移 checksum、重启持久化、并发陈旧写拒绝、整批回滚、append-only trigger 和 outbox 幂等标记。
- [x] 实现 PostgreSQL adapter 并复用同一 contract suite；Windows 本地 SQLite 与 WSL PostgreSQL 16 均通过。
- [x] 新事务协调器在 Agent 前提交 intent，长动作期间缓冲 Kernel transition，终态后将 workflow/run/artifact/outbox 原子提交；Phase 0 JSONL 仅作旧路径兼容，不异步镜像。
- [x] review、签名 decision application、local integration 和 cleanup 使用相同阶段事务协议，拒绝授权时 Git 与数据库均不改变。

## ST-03 至 ST-07 完成证据

- [x] 本地签名 session、RBAC、execution/approval authority 覆盖有效、篡改、过期、错角色和错 task/run；公共 UI/CLI 写操作不再接受自由文本身份。
- [x] `AgentCredentialGrant`/broker 端口不返回 secret；治理执行器要求 delegate 绑定同一短期 grant，终态后 revoke。没有真实短期凭据 adapter 时外部模型执行 fail closed。
- [x] Linux namespace sandbox 从空 root 启动，只暴露运行时和单 workspace；真实 WSL 测试证明无网络、host secret 不可见、root 不可写及 CPU/内存/process/time 限制。Windows 通过同一能力接口明确拒绝缺失的强隔离。
- [x] worker lease、heartbeat、持久化 cancel、release 和 orphan expiry 已接入治理执行器；超预算先写 cancel-requested 再终止执行器。
- [x] task/run contract 明确 token、cost、context、attempt 与 repair 上限；上下文组件和 package 按内容寻址，拒绝越界、symlink、超大小和篡改。
- [x] Dashboard 支持旧 Phase 0 完整性语料和事务 SQLite 真相；详情展示 workflow、run records、outbox events、制品、usage、review 和 decision，HTTP 保持认证、loopback、read-only。
- [x] OpenTelemetry 使用可选 adapter/no-op 默认；executor comparison 在 task/profile hash 一致后比较两种 adapter。当前第二执行器证据是 fake Codex CLI 合同回归，不冒充真实付费模型质量评测。

## 短期明确后置

Temporal、Ray、Kubernetes、远程 GPU、多租户平台、A2A、生产部署和复杂前端框架不属于本计划前置项。Langfuse/Phoenix、Promptfoo 和第二个 Agent adapter 也要等统一事件、样例语料和持久查询边界稳定后再评估。

## 完成定义

每个切片完成时必须追加 `docs/DEVELOPMENT_LOG.md`，记录变更文件、正反测试、完整 `./jobslayer check` 结果、限制和下一步。涉及持久化真相、权限或基础设施选型的决定必须新增 ADR；不能通过修改旧记录掩盖方向变化。
