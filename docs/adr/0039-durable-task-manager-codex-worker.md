# ADR-0039：TaskManager Codex 使用持久启动身份、独立 worker 与运行级 worktree

- 状态：Accepted
- 日期：2026-08-19
- 关联：ADR-0002、ADR-0005、ADR-0023、ADR-0035、ADR-0037、ADR-0038

## 背景

TaskManager 已经在调用 executor 前追加稳定 `provider_start_key`，但旧 `CodexCliExecutor` 只把
`Popen` 和事件缓冲保存在 API 进程内存中。API 重启后无法重新定位进程；直接在 BraveNewWorld 主
checkout 上运行多个 DAG 节点也会污染固定基线。另一个边界问题是通用 DAG 中的 `validation` 与
`human_gate` 不能被当作普通 Agent 实现步骤。

## 决定

1. 新增 `DurableTaskManagerCodexExecutor`。它以 `provider_start_key` 派生稳定 provider 目录和
   provider run id，在任何外部启动前持久化完整请求、prompt、launch envelope、执行目标绑定、
   worktree manifest 与 start evidence。
2. API 不直接持有 Codex 子进程。一个受信任的独立 Python worker 先用 create-exclusive claim 文件
   赢得启动权，再以 argv（无 shell）调用本机已登录的 `codex exec --json`。API 进程重启后，相同
   start key 返回同一 reference；并发重复 worker 只有 claim 获胜者能启动 Codex。
3. 一个 TaskManager run 从 source-pinned base commit 建立一个稳定 Git worktree；顺序 task nodes
   共享该 worktree，使早期节点的未提交实现可以被后续节点继续处理，同时原 BraveNewWorld checkout
   保持不变。start key 若绑定到不同请求、prompt 或 launch envelope，必须失败关闭。
4. worker 把原始 JSONL、stderr 和 terminal result 留在 provider 目录。adapter 将其注册为
   task/run-bound 内容寻址制品，再返回稳定 cursor 的 normalized observation。相同 cursor 必须返回
   字节意义上相同的持久 observation。
5. 只有 `task` node 可进入 Codex adapter。`validation` 必须进入确定性 verifier，`human_gate` 必须
   进入授权人类决定路径；当前未实现的路径保持阻塞。里程碑仍沿用现有显式执行语义，后续由独立的
   plan compiler ADR 决定自动汇聚规则。
6. TaskManager API 只有同时提供 `--allow-external-task-execution` 和带 `executor` 权限的签名 session
   才连接 adapter。目标中的实际 model 与 reasoning effort 随 source bundle 固化；当前 BNW 值是
   `gpt-5.6-sol/xhigh`。

## 边界与后果

- 该机制解决 API 进程重启后的 start-or-locate，不宣称跨主机调度或任意机器重启后的透明续跑。
  worker 无 terminal evidence 而消失时观察为失败，只能由授权主体用新 attempt key 显式重试。
- wall-clock timeout 由 worker 硬执行；Codex/订阅不提供可验证的预扣 token 或美元余额，因此 token、
  context 与成本仍是输入门禁和事后证据，不表述为精确订阅扣费控制。
- adapter success 只能把 Kernel 节点推进到 `Verifying`。确定性 verifier、review、human gate、source
  integration 和 authorized completion 仍需后续实现，executor 不能宣布完成。
- worker 使用 Codex 自身 `workspace-write` sandbox；这不扩大任务 allowed/forbidden paths，也不把
  Codex 当作权限、验证或工作流状态的所有者。
