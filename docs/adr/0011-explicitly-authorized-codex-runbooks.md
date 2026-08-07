# ADR-0011：用外部显式授权启动真实 Codex runbook

- 状态：Accepted
- 日期：2026-08-07

## 背景

ADR-0005 已实现 `CodexCliExecutor`，ADR-0010 已用确定性 replay 验证本地运行装配，但源控 runbook 尚不能选择真实模型。直接把 Codex 当成另一个自动授权 adapter，会让仓库内文件同时定义任务和授予自身模型调用权限，也无法诚实表达凭据、费用与主机隔离边界。

## 决策

1. `LocalTaskRunbook.executor` 使用带 discriminator 的 `scripted_patch | codex_cli` 联合契约；执行器类型必须与 `AgentRunSpec.executor_type` 一致。
2. `codex_cli` 当前只接受 low-risk、`workspace_write`、`model_profile=default`、`output_schema=none` 和 `max_attempts=1`。尚未实现的重试策略不能通过填写大于一的数字伪装存在。
3. Codex binary、登录凭据和模型 profile 由可信运行端配置，不由目标仓库或 runbook 指定。runbook 不能替换可执行文件或注入 SDK 对象。
4. 每次真实模型执行都要求 CLI 外部提供非空 `--authorized-by`。JobSlayer 将它记录为声明式 human `TaskExecutionAuthorization`；缺失授权在创建 run 目录和 worktree 前拒绝。该本地声明不是身份认证。
5. 确定性 replay 继续由登记 policy 授权；若人为向 replay 提供 `--authorized-by`，则拒绝，避免混淆授权来源。
6. 实际 Codex 仍走同一 controller、worktree、路径政策、raw JSONL/stderr、内容寻址制品、验证引擎、双哈希链、实现评审和合并决定卡，不建立模型专用完成路径。
7. 当前只记录 Codex 返回的 token usage，不提供执行前 token/cost 强制预算。真实调用必须逐次由外部人类授权；在预算执行器完成前不得开放无人值守批量模型运行。
8. `workspace-write` 是 Codex 内部沙箱选择，不等于 JobSlayer 已提供外层网络或资源隔离。运行摘要继续明确返回 `network_isolation=false` 和 `resource_isolation=false`。

## 首次验证

源控任务 `bnw-filter-demo-001` 从本地 `bnw-0` 固定基线启动真实 `codex-cli 0.142.4`，在独立 worktree 实现确定性含噪正弦和低通滤波主题。受治理场景和完整 BNW 套件通过，独立 Agent 实现审查接受，运行停在 `MergeReview`；没有人类决定、Git 合并、提交、推送或部署。

## 后果

- JobSlayer 已证明真实模型可以被现有提供方中立控制面治理，而不是由模型拥有状态或完成权。
- 源控输入可复核，模型调用授权仍来自运行时外部；两者不会被混成一个自我批准文件。
- 凭据隔离、认证 identity provider、执行前预算、OCI/VM 外层隔离、自动修复重试和受控 Git 合并仍是独立退出条件。
- 真实运行使用量可能显著高于预期；在积累更多样例前必须把 token、缓存命中、时长和成功率作为评估数据，而不是默认扩大并发。

## 被否决方案

- runbook 内保存 `authorized: true`：拒绝，仓库输入不能授予自身执行权限。
- 为方便自动化允许 `danger-full-access`：拒绝，当前没有足够外层隔离。
- 把模型最终消息当作验收结果：拒绝，只有独立验证报告和授权状态转换能推进工作流。
- 首次成功后立即自动合并或推送：拒绝，它们需要独立权限、基线漂移检查和恢复设计。
