# ADR-0043：源码绑定的确定性 validation node 执行

- 状态：Accepted
- 日期：2026-08-19

## 背景

TaskManager 已明确禁止把 `validation` node 分派给 Codex，但此前没有对应的确定性执行路径。因此真实
BraveNewWorld DAG 在源码节点完成后会停在验证节点：既不能绕过验证，也无法把 finalized target 中的
`ValidationProfile` 转成受治理的命令事实。直接让 Agent 运行并解释测试会把命令选择、通过判定和工作流
状态交给 provider，违反 JobSlayer 拥有工程真相的产品不变量。

## 决定

1. 增加 provider-neutral `TaskManagerValidator` 端口及结构化 validation check evidence。adapter 只返回
   原始 `CommandResult`、workspace inspection 和不可变 artifact 引用；不返回工作流完成决定。
2. 本地实现必须通过显式 `--allow-task-manager-local-validation` 接入，并要求当前认证主体拥有
   `EXECUTE_TASK`。没有开关、权限或 adapter 时，validation node 失败关闭且不改变 run revision。
3. adapter 只执行 finalized execution target 中精确的 `ValidationProfile`，使用既有 run worktree 和
   `GovernedLocalCommandRunner`。开始前将 worktree 重新绑定到当前隔离分支 HEAD，并要求工作树 clean；
   完成后收集的 evidence 仍必须 clean、无 changed paths 和 source patch。
4. TaskManager 在任何命令副作用前把稳定 `tmvalidate-*` intent 写入 run 哈希链。adapter 以该键持久化
   canonical request、reference 和 terminal results；同键输入漂移拒绝，terminal 已存在时直接复用。
5. runner 的 `Succeeded` 只表示所有配置命令已终止并形成事实，不表示检查通过。TaskManager service
   精确比对 check id/order、argv、cwd、required、task/workspace/policy 绑定，映射命令状态并编译
   `VerificationReport`，再由 `WorkflowKernel` 推进到 `Reviewing` 或 `Repairing`。
6. passing report 仍不能自动完成节点。授权 Reviewer 必须复核并登记接受证据，validation node 才进入
   `DeliverableAccepted` 并满足后继依赖。该路径不修改、合并、推送或部署任何主干源码。

## 后果与限制

- validation 与普通 Agent task 共用 observe/verify/read projection，但 start path、adapter 身份和 UI
  capability 分离，避免把确定性检查伪装成模型执行。
- 每条命令的 stdout/stderr、完整字节 hash、截断标志、退出码与计时都作为 task/run-bound artifact
  保留，TaskManager 报告可追溯到原始事实。
- 当前本地 adapter 同步执行短时、源码控制、预期只读的验证命令。进程若在命令退出后、terminal
  原子落盘前崩溃，恢复可能重跑该组命令；因此此阶段只允许幂等、非发布型 validation profile。跨机器
  lease、远端队列和 exactly-once 外部副作用仍留给后续路线图退出条件。

