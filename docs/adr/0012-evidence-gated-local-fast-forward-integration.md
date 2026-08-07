# ADR-0012：批准与完成之间必须经过证据门禁的本地快进集成

- 状态：Accepted
- 日期：2026-08-07

## 背景

原状态机把 merge review 的 `approve` 直接映射到 `Completed`，但 Phase 0 当时没有提交或合并实现。任务会在补丁仍只存在于脏 worktree 时宣称完成，工作流真相与 Git 真相不一致。现在需要闭合最小成功路径，同时继续禁止模型、自报结果或 UI 直接拥有完成权。

## 决策

1. 新增 `Integrating` 状态。有效人工决定的 `approve` 只执行 `MergeReview → Integrating`；`request_changes` 和 `reject` 语义不变。
2. `Integrating → Completed` 同时要求：human/policy 行为者、原通过的 `VerificationReport`、以及与该报告同一 patch SHA-256 的 `SourceIntegrationResult`。缺少任一项时 kernel 拒绝转换且不写日志。
3. `LocalGitIntegrator` 位于 adapter 边界。它只操作已登记本地 checkout：复核目标 checkout 当前分支、干净状态和固定 base，复核 workspace patch 与已审查 patch 完全一致，创建一个带 task/patch/approver trailer 的提交，再以 `--ff-only` 更新登记的默认分支。
4. 为恢复提交后、记录前的中断，重复集成可以识别已创建提交；恢复时不仅检查父提交、提交说明和路径，还用临时 Git index 将审核 patch 应用到 base，并要求提交 tree 完全相同。
5. 适配器禁用仓库 hooks，不 fetch、不 push、不 rebase、不强制更新、不创建 merge commit，也不部署。
6. 集成结果作为内容寻址制品登记，运行账本追加 `source_integration`；完成后可显式执行 `workspace_cleanup`，只移除干净 worktree并保留任务分支。
7. `apply-run-decision`、`integrate-run` 和 `cleanup-run` 保持三个独立命令。决定记录、权限应用和 Git 变更不会被一个 UI 点击隐式串联。

## 后果

- `Completed` 首次与本地目标源码事实一致，不再等同于“人类接受了一个尚未集成的补丁”。
- 目标分支或工作区在审核后发生漂移时，集成会停在 `Integrating` 供人工处置，不会自动 rebase 或覆盖变化。
- 本地成功路径已闭合，但远端 push、PR、部署、认证 identity provider、事务数据库和自动 repair 仍未实现。
- 旧的持久运行和决定卡保持不可变；它们仍可被检查。实际批准后会采用新的 `Integrating` 语义，并仍需操作员显式调用本地集成命令。

## 被否决方案

- 批准即 `Completed`，Git 留给人手：拒绝，状态会先于源码事实。
- 批准后自动 push：拒绝，本轮没有远端权限、保护分支和恢复契约。
- 目标漂移时自动 rebase/merge：拒绝，会改变人类审核过的 patch 与基线。
- 用提交说明或 changed paths 代替内容复核：拒绝，同路径的不同内容不能成为审核补丁。
