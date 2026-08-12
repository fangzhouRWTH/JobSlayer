# ADR-0024：PostgreSQL 事务真相与 Kernel 缓冲协调器

- 状态：Accepted
- 日期：2026-08-12

## 背景

Phase 0 的 JSONL 双链已能恢复单控制器崩溃，但 workflow、run record、artifact metadata
和通知事件仍跨文件提交。长时间 Agent 执行又不应占用数据库事务。SQLite contract
adapter 已固定 expected sequence、rollback 和 outbox 语义，ST-00/ST-01 的退出证据也已
满足，因此可以选择生产数据库 adapter 并切入新的 Phase 1 协调路径。

## 决定

1. `ControlPlaneStore`/`StateTransaction` 保持 provider-neutral；SQLite 与 PostgreSQL
   复用同一 contract suite。PostgreSQL 使用事务级 advisory lock、乐观 sequence、
   append-only trigger、迁移 checksum 和同事务 outbox。
2. 执行前先事务提交不可变 intent 及输入制品 metadata。此时没有 workflow/run outcome，
   因而崩溃后只能人工处理，绝不自动重跑 Agent。
3. `WorkflowKernel` 在长时间动作期间写入内存中的哈希链缓冲；动作终态后，adapter 再
   校验并原样 stage 这些 Kernel 产生的 transition。数据库事务不跨越 Agent 或 Git。
4. execution outcome、review、签名 decision application、local integration 和 cleanup
   均把本阶段新增 transition、run record、artifact metadata 和 outbox 一次提交。
5. Phase 0 JSONL 继续作为旧语料与恢复兼容路径，不异步镜像到数据库；新的
   `TransactionalExecutionCoordinator` 与事务查询 adapter 以数据库为唯一元数据真相。

## 后果

- 重启、并发陈旧写、批量 rollback、append-only 和 outbox 均可由 SQLite/PostgreSQL
  相同测试证明。
- 外部 Git side effect 不能加入数据库事务，但本地 integrator 幂等复核已存在；数据库
  只在获得可验证 integration evidence 后进入 `Completed`。
- 备份、HA、outbox dispatcher 和托管 PostgreSQL 运维不属于本地基础架构闭环。

