# Codex CLI 集成说明

## 当前状态

`CodexCliExecutor` 已通过假 CLI 合规测试，并已由 `LocalRunCoordinator` 在旧 BraveNewWorld
worktree 中完成首个模型任务。它位于 `src/jobslayer/adapters/codex_cli.py`，消费已登记的
`WorkspaceManifest`。当前源控运行契约已切换为
`runbooks/bnw-anygine-small-app-001-codex.json`；旧滤波 runbook 只保留在 Git 历史和离线归档中。
当前 runbook 同时声明 Anygine source/toolchain attachment，但这两个本机路径只注入独立
validation node，不传给 Codex 实现进程。Codex 仍只能修改固定 BraveNewWorld worktree 的允许路径。

本机在 2026-08-07 检测到：

```text
codex-cli 0.142.4
```

版本号只是开发环境记录，不是领域契约。每次升级都应重新运行 adapter 测试，并用受控实验任务核对事件类型变化。

历史运行 `bnw-filter-demo-001-run-01` 曾产生 19 文件范围内补丁并到达 `MergeReview`；它是旧测试床
方向的审计证据，不再是当前 target。完整证据见 [Phase 1 实施说明](PHASE1_FILTER_CODEX_RUN.md)。

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

`start` 非阻塞返回 `AgentRunHandle`；`events(after_sequence=N)` 支持增量轮询；`cancel` 通过可注入的 `ProcessSupervisor` 协议终止 POSIX process group 或 Windows process tree；`collect` 只在终态返回，否则明确报运行中。进程清理本身不是安全隔离；配置 `SandboxLauncher` 后，命令才会通过可验证的外层沙箱启动。

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

真实 `codex_cli` runbook 目前进一步固定为 `workspace_write`、`model_profile=default`、`output_schema=none`，并显式登记 input/output token、context bytes、费用、attempt 与 repair 上限。公共 CLI 运行时必须传入具备 `execute_task` 权限的签名 session；CLI 生成绑定 task/run/风险/有效期的 `ExecutionCredentialProof`。源控 runbook 不能自我授权，也不能选择本机 Codex binary。

## 凭据原则

- adapter 不继承 ambient `OPENAI_API_KEY` 或 `CODEX_API_KEY`；
- 历史 Phase 0 本地实验依赖操作员已有的 CLI 登录，但不构成安全部署方案；
- 不把认证文件复制进目标 worktree；
- 不把密钥写入 prompt、任务 JSON、事件、stderr 或制品；
- provider-neutral `AgentCredentialBroker` 只返回不含 secret 的短期 grant 证据；
- 严格治理路径要求 executor 证明绑定同一 grant，并在终态撤销；没有真实短期凭据 adapter 时 fail closed；
- CI 模式不得让 Agent 作业同时拥有仓库写入或部署凭据。

## 真实模型运行门禁

1. 测试床已有人工建立且在本机核验的固定基线 commit/tag；发布状态必须单独如实记录；
2. 创建低风险、允许路径明确、验证命令确定的 `TaskSpec`；
3. 为 run 显式设置 token、费用、上下文、timeout、attempt/repair 和目录权限上限；
4. 由 context builder 校验版本、内容哈希、普通文件和最大字节；
5. 运行前验证签名 execution authority、短期凭据 grant、worker lease 和沙箱能力；
6. 预算 reserve 与 attempt 授权在启动前持久化，运行中按 normalized usage 增量扣减；
7. 运行后先收集 patch 和 raw logs，再通过独立验证 runner；需要引擎的检查必须核对
   run-bound dependency identity 与每条命令的显式环境证据；
8. 只生成合并提案，不自动 push、merge 或 deploy。

`GovernedAgentExecutor` 统一执行 credential/context/sandbox/budget/lease 门禁：缺少任一能力都不启动；超限时先持久化预算耗尽和 lease cancel-requested，再通知 delegate。Linux bubblewrap adapter 已验证默认无网络、宿主文件不可见、仅 workspace 可写以及 CPU/内存/进程/时间限制。原生 Windows 没有等价强沙箱时明确失败关闭，可把同一请求路由到 WSL/Linux worker。

`LocalRunCoordinator` 仍保留 Phase 0 JSONL 兼容路径，其签名 execution authority 已替代自由文本授权，但它不是生产 worker/secret broker 的替代品。面向外部模型的安全部署必须组合治理装饰器、真实短期凭据 adapter 和 enforcement-backed sandbox；当前仓库不会用已有 CLI 登录假装完成这条生产链路。
