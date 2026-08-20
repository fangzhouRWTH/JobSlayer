# ADR-0042：源码节点使用独立审查、审批与隔离运行分支检查点

- 状态：Accepted
- 日期：2026-08-19
- 关联：ADR-0001、ADR-0002、ADR-0012、ADR-0037、ADR-0039、ADR-0041

## 背景

TaskManager 已能把 provider 成功编译为确定性的 `VerificationReport`，并能接受无源码差异的阶段
交付物。源码节点仍停在 `Reviewing`：若复用 artifact-only 接受会丢失源码集成真相；若直接合并目标
主干，又会把节点级推进扩大成项目发布权限。长任务还要求 Git 副作用可在 API/进程中断后安全重试。

## 决定

1. 源码节点沿用 `WorkflowKernel` 的既有路径：`Reviewing -> MergeReview -> Integrating -> Completed`。
   不新增旁路状态，也不允许 adapter、Agent 或 provider success 决定完成。
2. 具备 reviewer 权限的人类只能审查 passing report 所绑定的精确 patch，形成 `ReviewReport` 和
   task/run-bound immutable artifact；patch hash、report、changed paths 或执行绑定不一致时失败关闭。
3. 离开 `MergeReview` 要求独立 approver。审批人与 reviewer 不得相同；批准制品绑定 finalized plan
   id/revision/hash、review/report、patch、路径、现有 run branch 和稳定 integration key。
4. integration key 和审批 intent 必须先追加到 run 哈希链，之后才允许 Git 副作用。具体实现位于
   provider-neutral `TaskManagerSourceIntegrator` 后；当前本地 adapter 只提交精确 reviewed patch 到
   已存在的 run worktree branch，不 checkout/merge 主干，不 push，不 deploy。
5. adapter 把 canonical request 写入独立状态目录；同一 key 的输入漂移被拒绝。提交后若控制面尚未
   记录结果，重试只能接受“HEAD 的唯一父提交是 reviewed base、工作树 clean、diff hash/path 完全
   相同”的同一检查点，并返回同一 durable result。
6. `Completed` 仍要求 passing verification report 与 `SourceIntegrationResult` 的 task、base、patch、
   changed paths、target ref 和 approved actor 完全一致。集成 artifact 再经注册表校验后才由 Kernel
   追加终态。
7. CLI 默认不连接 source integrator。只有显式
   `--allow-task-manager-checkpoint-integration` 且短期签名 session 具备 `INTEGRATE_SOURCE` 才启用；
   planning、review、approval 和 integration API 分别按命令授权，允许使用真正的 approver-only
   session，而不要求附带 planner/executor 权限。

## 后果

- 每个源码节点都留下“验证事实、Reviewer 语义接受、独立 Approver 决定、精确 Git 提交”四层证据。
- 节点完成只推进隔离 run branch；后续节点可基于该 HEAD 继续形成 node-local patch，主干集成仍需
  另行授权与既有项目级流程。
- 当前 adapter 支持本机已有 worktree 的检查点恢复，不宣称跨机器调度、远端推送或发布恢复；这些
  能力若需要必须另立权限和 ADR。
