# ADR-0061：恢复部分写入的执行命令并统一可执行节点语义

- 状态：Accepted
- 日期：2026-09-04
- 依据：[ADR-0039](0039-durable-task-manager-codex-worker.md)、
  [ADR-0055](0055-persistent-single-step-task-manager-coordinator.md)、
  [ADR-0060](0060-default-governed-desktop-execution.md)

## 背景

用户在现有 Run R1 点击“推进一步”后，TaskManager 已经先追加 R2
`node.dispatch_authorized`，并把 `scope` 从 `planned` 转成 `implementing`、保存稳定 start key；provider
reference 尚未绑定。原因是 coordinator 把非人工、非 validation 的 `milestone` 当作可执行节点，
durable Codex adapter 却只接受 `task`。API 返回错误后页面仍持有 R1，再次提交便收到
`expected TaskManager run revision 1, current revision is 2`。

这段状态是 intentional write-ahead 的保守结果，不允许删除 R2、回写 R1 或直接伪造 provider 成功。
coordinator journal 同时保留了与 R1 绑定的 `start_node` pending intent，可以安全识别并恢复。

## 决定

1. durable Codex adapter 接受 `task` 与 `milestone` 两类可执行节点；`validation` 仍只进入确定性
   validator，`human_gate` 仍只接受授权人工决定。adapter 与 coordinator 的路由集合保持一致。
2. coordinator 看到 pending intent 且 Run revision 已前进时重新计算权威 next action：
   - next action/node 仍与 pending intent 相同，说明 application transition 已写入但副作用未完成，使用
     原 intent 和幂等 start key 继续 `_execute`；
   - next action 已改变，说明原副作用已形成更高状态，只追加 reconcile projection，不重复 provider。
3. UI 的任何 TaskManager 写命令失败后保留原始错误，同时立即 GET 当前 task 和 task list，使本地
   revision、节点状态和 coordinator cursor 回到后端真相。刷新失败不覆盖第一个错误，显式刷新仍可用。
4. 继续依赖 expected revision、lease、pending intent、provider start key、append-only Run 和
   `WorkflowKernel.transition`；不增加自动 retry、不删除部分记录、不在浏览器推断恢复动作。
5. 发布并激活 SUID `focused-task-graph@12`，保留 v11 的全部 stable 单元。

## 后果

- 当前 R2/implementing/pending-start 是可恢复状态；重启新版 backend 后一次“同步并推进”即可使用原
  intent 绑定 provider，无需重建任务或 Run。
- provider 已经绑定但 coordinator 未写完成记录的崩溃路径仍不会重复启动。
- 409/502 等写命令错误不再让浏览器继续持有旧 revision。
- milestone 可能调用外部 Agent，仍受 finalized DAG、target、预算、workspace policy 和人工点击约束。

## 未采用方案

- 删除 R2 或手工编辑 JSONL：会破坏哈希链和已经发生的授权事实。
- 把所有 milestone 自动标为完成：会绕过计划中的实际工作和验证。
- 让 validation/human gate 也进入 Codex adapter：会破坏确定性验证与人工授权边界。
- 捕获 stale 错误后盲目重放 POST：可能在未知副作用后重复执行；前端只刷新，恢复仍由 coordinator 决定。
