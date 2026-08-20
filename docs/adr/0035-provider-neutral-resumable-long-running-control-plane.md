# ADR-0035：提供方无关的可恢复长任务控制面

- 状态：Accepted
- 日期：2026-08-19

## 背景

真实工程任务经常超过一小时。纯模型推理、工具执行、排队、人工等待和重试消耗不同，token、
墙钟时间与费用不能互相换算；ChatGPT 订阅也不能被本地控制面推断成某次运行可用的精确美元
余额。现有 `GovernedAgentExecutor` 已具备启动前预算预留、worker lease、heartbeat、增量用量和
超限取消，但没有持久化的 provider run 身份、checkpoint 序列或重启后的同一 attempt 接管协议。

直接提高同步超时会让崩溃窗口、重复启动、无界重试和“provider 自称完成即完成”等风险扩大。
同时，当前真实 Codex planning adapter 是一次性本地 CLI 子进程；它没有因此自动获得跨控制器
重启续跑能力。

## 决定

1. 新增 provider-neutral 长任务契约和 application service。长任务状态是运行控制面状态，不是
   `TaskState`；它不能绕过 `WorkflowKernel`、验证报告、授权批准或完成门禁。
2. 每个 run 先持久化 admission、完整 policy 和唯一 `provider_start_key`，再允许 adapter 执行
   `start_or_locate`。同一 attempt 始终使用同一 key；只有显式授权 retry 才增加 attempt 并生成
   新 key，从而关闭“外部已启动但 provider ID 尚未写回”时只能猜测的恢复窗口。
3. provider start 和每次 observation 都必须先保存绑定 task/run 的原始不可变制品。归一化状态、
   event cursor 和累计用量只有在制品存在且哈希验证通过后才能改变控制面投影。
4. 预算按独立维度表达为 `hard`、`soft`、`observe_only` 或 `unavailable`。JobSlayer 本地派生
   `task_elapsed_ms` 与 `attempt_elapsed_ms`，两者必须有 hard limit；model/tool/wait、token、
   tool calls 和 cost 按 adapter/账户可观测性单独配置。非 metered billing 禁止用 provider cost
   作为 hard gate。
5. SQLite adapter 原子追加 event、更新 projection，并可在同一事务写 checkpoint metadata。
   event/checkpoint 表由 trigger 拒绝 update/delete，event 使用连续 sequence 与 SHA-256 hash
   chain；读取 projection 时重新核对完整 event truth。
6. live run 必须绑定唯一 worker lease。正常 observation/checkpoint heartbeat；hard limit 先把
   lease 持久化为 cancel-requested，再由调用方通知 adapter。lease 未过期时拒绝 takeover；过期后
   只有 exact provider identity、单调 cursor/usage 和 raw evidence 齐全时才能在同一 attempt
   换 lease。找不到 provider run 时记为 lost，不自动重启。
7. checkpoint 保存 stage、summary、attempt、cursor、累计 usage、引用制品、可选 workspace hash
   与 continuation artifact，并另存内容寻址 checkpoint artifact。它表示可核验进度，不是验证通过
   或任务完成。
8. 初始本地订阅型建议基线为：task wall 8 小时 hard、单 attempt wall 3 小时 hard、最多 2 次
   attempt、90 秒 lease、最长 30 秒一次 observation、15 分钟 checkpoint、20 分钟无进展警告；
   output 100k tokens 与 400 tool calls 只作 soft warning，input/model/tool/wait 只观测，cost
   unavailable。具体任务可以收紧，但不能删除两个本地 elapsed hard gate 或自动扩大 attempt。

## 后果

- 长任务不再用一个 token 上限或单次同步 timeout 冒充完整预算；订阅不可计费时也不会生成虚假
  美元精度。
- 重启可以区分 live owner、可接管 orphan、lost provider 和需要人工授权的新 attempt；恢复同一
  provider run 不消费 retry。
- 本轮只增加 Python 契约、application service、SQLite/现有 artifact 与 lease 组合，没有引入
  Temporal、队列、远程数据库或新的 Python/npm 依赖。
- SQLite long-run event 和 worker lease 位于两个本地事务边界。service 对 bind 失败做补偿，但
  进程在两个提交之间崩溃仍需 reconciliation；生产多控制器场景应把两者装配到共享事务 adapter，
  不能把当前本地组合宣称成分布式 exactly-once。
- 具体 executor 只有实现持久 `start_or_locate` 和 raw evidence 契约后才可宣称可恢复。当前
  `CodexPlanningAgent` 仍是同步一次性 adapter；下一步 prompt→任务图验证首先证明受治理的
  pending proposal 闭环，再决定 CLI 中断后是标记 lost/显式 retry，还是接入真正可 attach 的
  provider run。
