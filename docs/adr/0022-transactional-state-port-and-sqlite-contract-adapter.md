# ADR-0022：先以事务状态端口和 SQLite contract adapter 固化 ST-02 语义

- 状态：Accepted
- 日期：2026-08-12

## 背景

Phase 0 的 workflow journal、run ledger 和 artifact manifest 各自具备完整性与崩溃
恢复边界，但它们仍是多个独立文件提交。ST-02 需要一个明确事务同时保存 workflow、
run、artifact metadata 和可靠事件，并在多 writer 下拒绝陈旧版本。

直接把 `WorkflowKernel` 绑到 PostgreSQL SDK 会违反 provider-neutral 领域边界；在
Phase 0 人工退出条件尚未确认时引入数据库服务依赖，也违反“由退出证据证明后再加
基础设施”的路线图约束。本地跨平台开发还需要一个不依赖守护进程的 contract
adapter，以便 Windows 和 POSIX 使用同一事务测试。

## 决定

1. `WorkflowKernel` 依赖 provider-neutral `AuditJournal`，不再依赖
   `JsonlAuditJournal` 具体类；transition 构造和哈希链验证成为可复用确定性函数。
2. run record 契约改名为 provider-neutral `RunRecord`，保留 `LocalRunRecord` 别名
   兼容已有调用；抽出统一 record 构造与阶段序列校验。
3. 新增 `ControlPlaneStore`/`StateTransaction`。每个事务显式绑定 task/run 和两个
   expected sequence，向 kernel 暴露 journal，并在同一 commit 中暂存 run record、
   artifact metadata 与 outbox event。
4. 任何 workflow/run/artifact truth mutation 没有同事务 outbox event 时拒绝提交。
   事务退出但未显式 `commit` 时回滚；重复 identity 或陈旧 sequence 返回结构化
   conflict，而不提交部分数据。
5. 使用 Python 标准库 SQLite 实现第一个 contract adapter：WAL、FULL synchronous、
   `BEGIN IMMEDIATE`、有序迁移及 checksum、append-only/immutable 数据库 trigger、
   稳定 outbox commit order 和幂等 publication mark。
6. SQLite adapter 是本地开发与契约证明，不冒充 PostgreSQL 或生产级多控制器。
   PostgreSQL adapter 必须在 ST-00 人工退出条件完成后实现同一端口，并通过相同
   restart/concurrency/rollback/outbox contract tests；应用协调器切换到该事务边界前，
   ST-02 仍不得标记完成。

## 理由

- kernel 只需要有序历史和原子 append 语义，不应知道 JSONL、SQLite 或 PostgreSQL。
- 标准库 adapter 能立即在两种宿主验证事务规则，而不提前要求数据库服务或驱动。
- 先固定 expected-version、rollback 和 outbox contract，可减少后续 PostgreSQL
  schema 反向塑造领域模型的风险。

## 后果

- 已能证明进程重开后数据保留、陈旧并发 writer 只有一个成功、唯一冲突整批回滚、
  truth table 拒绝 UPDATE/DELETE、迁移 checksum 漂移失败和 outbox 标记幂等。
- SQLite 事务当前是新 adapter，还没有替代 `LocalRunCoordinator` 的 Phase 0 文件
  backend；文件恢复协议仍是现有默认真相。
- 长事务、跨主机并发、数据库备份和 PostgreSQL outbox dispatcher 仍待后续切片。

## 未选择的方案

- 立刻加入 psycopg/PostgreSQL 服务：暂缓，Phase 0 人工退出证据未完成，且当前环境
  没有已登记的 PostgreSQL testbed。
- 只把文件状态异步镜像进数据库：拒绝，会产生两个无法明确裁决的工程真相。
- 允许无 outbox 的状态提交后再扫描补事件：拒绝，崩溃窗口会让查询/实时视图永久
  漏掉已提交变化。
