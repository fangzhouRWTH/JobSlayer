# 长任务执行与恢复

## 解决的问题

长任务的控制变量不能压缩成一个“等价 token”数字：模型可能长时间推理却很少调用工具，也可能
大部分时间在编译、下载、等待或人工门禁。JobSlayer 因而分别记录墙钟、模型、工具、等待、token、
费用和调用次数，并让每个维度明确选择 hard、soft、observe-only 或 unavailable。

这套能力只拥有运行控制面真相。它不会把长任务状态写成 `TaskState`，也不会让 provider 的
`completed` 跳过确定性验证、授权批准或 `WorkflowKernel` 完成规则。

## 持久生命周期

```text
admit(policy + attempt 1 + persisted start key)
  -> adapter.start_or_locate(the exact key)
  -> persist raw start evidence
  -> bind(provider run + worker lease)
  -> observe(raw event artifact + monotonic cursor/usage) ...
       -> heartbeat / soft warning / hard cancel-requested
       -> checkpoint(content-addressed progress evidence) ...
  -> completed | failed | cancelled | lost
       -> optional explicitly authorized retry with a new attempt and start key
```

`start_or_locate` 是关键恢复边界。如果 adapter 在外部启动成功后、本地 bind 前崩溃，它必须能用
已持久化的 `provider_start_key` 找回同一个 run，而不是再启动一次。不能提供该能力的本地子进程
adapter 必须在中断后保守标记 lost，并等待显式 retry。

运行中接管也不是 retry。只有旧 lease 已过期、provider adapter/run ID 完全匹配、raw observation
制品完整、cursor/usage 单调且预算未越界时，`recover` 才给同一 attempt 分配 replacement lease。

## 建议的本地订阅基线

| 维度 | 初始策略 | 理由 |
|---|---:|---|
| task elapsed | 8h hard | 包含重试和等待，给小时级工程任务明确总截止线 |
| attempt elapsed | 3h hard | 由 JobSlayer 本地时间派生，不信任 provider 自报 active time |
| attempts | 2 hard | 不自动重试；第二次必须有明确原因和授权 |
| worker lease | 90s | observation 建议不慢于每 30s，控制器死亡后可有界接管 |
| checkpoint | 每 15min | 绑定阶段、摘要、cursor、usage、证据和可选 workspace hash |
| no-progress warning | 20min soft | 允许长推理，但把停滞暴露给操作者 |
| output tokens | 100k soft | 只预警异常增长，不把 token 当作时间或订阅费用 |
| tool calls | 400 soft | 发现循环式工具行为，仍允许操作者判断 |
| input/model/tool/wait | observe-only | 保留分解数据，不假设各 provider 的计量等价 |
| cost | unavailable | 订阅模式不推断单次运行美元余额 |

这是长工程任务的宽松初始值，不是所有 prompt 的固定配额。短规划任务应收紧 task/attempt elapsed、
输出字节和调用次数；真实 provider 若能提供可信 metered cost，可在另一 policy 中配置 cost gate，
但不能由订阅月费反推。

## 代码入口

- `jobslayer.long_running`：policy、usage、provider identity、observation、checkpoint、event/store 和
  adapter protocol；
- `LongRunningExecutionService`：admit/bind/observe/checkpoint/cancel/finish/recover/retry 规则；
- `SqliteLongRunStore`：projection、append-only hash-chained events 与 checkpoint metadata；
- `SqliteWorkerLeaseStore`：唯一 live owner、heartbeat、cancel、release 与 orphan expiry；
- `LocalArtifactRegistry`：provider raw evidence 和 checkpoint 内容寻址对象。

## 当前边界与下一次验证

本轮还没有把这一 service 接到 `CodexPlanningAgent` 或 Web API，也没有后台 scheduler/daemon。
SQLite 组合用于单机可恢复控制面验证，不宣称跨主机 exactly-once。现有 Codex planning CLI 调用
仍由显式 model/effort/timeout/I/O 上限治理，且默认离线 fixture。

下一步闭环应使用一个明确授权的方案 prompt：创建 plan，触发 Codex 只生成 pending proposal，
核验 prompt/raw JSONL/stderr/final JSON 制品、DAG/assessment、权威图仍为空，然后由用户决定是否
apply/finalize。长任务 service 是否包裹这次调用，要取决于 adapter 能否满足 start-or-locate；
不能满足时不伪造可恢复性。
