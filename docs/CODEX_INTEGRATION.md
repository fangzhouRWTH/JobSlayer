# Codex CLI 集成说明

## 当前状态

`CodexCliExecutor` 已通过假 CLI 合规测试，并已由 `LocalRunCoordinator` 在真实 BraveNewWorld worktree 中完成首个模型任务。它位于 `src/jobslayer/adapters/codex_cli.py`，消费已登记的 `WorkspaceManifest`；源控运行契约位于 `runbooks/bnw-filter-demo-001-codex.json`。

本机在 2026-08-07 检测到：

```text
codex-cli 0.142.4
```

版本号只是开发环境记录，不是领域契约。每次升级都应重新运行 adapter 测试，并用受控实验任务核对事件类型变化。

首次真实运行 `bnw-filter-demo-001-run-01` 已产生 19 文件范围内补丁，通过场景和完整 BNW 门禁、独立实现审查，并停在 `MergeReview`。完整证据和复核方式见 [Phase 1 实施说明](PHASE1_FILTER_CODEX_RUN.md)。

## 生命周期

```text
AgentInvocation + WorkspaceManifest
    -> validate named model/permission/schema profiles
    -> codex exec --json in registered worktree
    -> raw stdout JSONL + raw stderr
    -> normalized RunEvent sequence
    -> cancel / timeout / process exit
    -> AgentRunResult + log hashes
```

`start` 非阻塞返回 `AgentRunHandle`；`events(after_sequence=N)` 支持增量轮询；`cancel` 终止进程组；`collect` 只在终态返回，否则明确报运行中。

## 事件映射

| Codex JSONL | JobSlayer 事件 |
|---|---|
| `thread.started` | `agent.thread.started` |
| `turn.started/completed/failed` | `agent.turn.*` |
| command item | `command.started/completed` |
| file-change item | `file.change.started/file.changed` |
| agent-message item | `agent.message.started/completed` |
| plan item | `plan.started/proposed` |
| CLI error | `agent.error` |
| 进程终态 | `run.completed/failed/cancelled/timed_out` |

完整 Codex JSON 对象仍放在 normalized event 的 `raw` payload 中，同时写入原始 JSONL 文件。这样既能提供统一 UI，又不会妨碍未来重新解释旧事件。

## 允许的权限

默认只提供：

- `read_only -> read-only`
- `workspace_write -> workspace-write`

构造 adapter 时若任何权限映射到 `danger-full-access`，会立即失败。模型 profile 和 output schema 同样必须在 adapter 初始化时由可信控制器登记。

真实 `codex_cli` runbook 目前进一步固定为 `workspace_write`、`model_profile=default`、`output_schema=none` 和单次尝试。运行时必须另传 `--authorized-by`；源控 runbook 不能自我授权，也不能选择本机 Codex binary。

## 凭据原则

- adapter 不继承 ambient `OPENAI_API_KEY` 或 `CODEX_API_KEY`；
- 当前本地模式依赖 HOME/CODEX_HOME 中已有的 CLI 登录；
- 不把认证文件复制进目标 worktree；
- 不把密钥写入 prompt、任务 JSON、事件、stderr 或制品；
- CI 模式需要单独设计短期 credential provider，且 Agent 作业不得同时拥有仓库写入或部署凭据。

## 真实模型运行门禁

1. 测试床已有人工建立且在本机核验的固定基线 commit/tag；发布状态必须单独如实记录；
2. 创建低风险、允许路径明确、验证命令确定的 `TaskSpec`；
3. 为 run 设置 timeout、`workspace_write`、一次尝试和无额外目录权限；
4. 确认没有生产密钥进入进程环境；
5. 运行前由外部人工通过 `--authorized-by` 明确批准本次模型调用；
6. 运行后先收集 patch 和 raw logs，再通过独立验证 runner；
7. 只生成合并提案，不自动 push、merge 或 deploy。

当前实现不会自行跨过这些门禁。`--authorized-by` 仍只是声明而不是认证；token usage 只在运行结束后记录，尚不能强制执行前成本预算。`workspace-write` 也不等于 OCI/VM 外层网络、系统调用、CPU 或内存隔离，运行摘要会把这些能力标为不可用。
