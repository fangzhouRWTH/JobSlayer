# ADR-0005：Codex CLI 执行器适配器

- 状态：Accepted
- 日期：2026-08-07

## 背景

JobSlayer 已具备任务 worktree、本地验证 runner 和人工决定契约，需要第一个真实编码 Agent adapter。Codex CLI 提供适合脚本和 CI 的非交互 `codex exec`，并能以 JSONL 输出运行事件。

2026-08-07 本地检查版本为 `codex-cli 0.142.4`。实现前通过当前 Codex Manual 的 [Non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode) 核对了 `--json`、`--ephemeral`、sandbox、配置隔离、stdin prompt 和结构化输出约定，同时以本机 `codex exec --help` 核对实际参数。

## 决定

定义非阻塞、提供方无关的 `AgentExecutor`：`start`、增量 `events`、`cancel` 和终态 `collect`。Codex adapter 使用：

```text
codex exec
  --json
  --ephemeral
  --ignore-user-config
  --ignore-rules
  --strict-config
  --color never
  --sandbox read-only|workspace-write
  --cd <registered-worktree>
  -
```

prompt 通过 stdin 传入，不进入进程参数。模型、权限和输出 schema 只能从控制器配置的命名映射选择，任务输入不能注入额外 CLI flag。adapter 完全拒绝 `danger-full-access` 映射。

Codex stdout 原样保存为 JSONL，stderr 单独保存，两者在终态计算 SHA-256。JSONL 同时被归一化为项目自有 `RunEvent`；无效 JSON 即使进程退出码为 0，也判定为 adapter 失败。终态由进程退出、Codex 失败事件、协议错误、timeout 或显式取消共同确定。

## 凭据和安全边界

adapter 不从宿主环境继承 `OPENAI_API_KEY` 或 `CODEX_API_KEY`。当前只保留 PATH、HOME/CODEX_HOME、证书和必要代理设置以支持本地已保存认证；正式自动化需要独立凭据提供者、短期凭据和外层 OCI/VM 隔离。

`workspace-write` 是 Codex 自身 sandbox 选择，不替代 JobSlayer 对进程、网络、密钥和主机文件的外部隔离。BraveNewWorld 尚无固定 commit，因此本轮只用假 CLI 和官方事件样例测试 adapter，没有发起模型请求。

## 后果

- 外部控制器可统一轮询事件、取消运行并收集终态证据；
- Codex 原始事件不会因归一化而丢失；
- 本地 Codex 版本升级需要用 adapter 合规测试回归；
- 当前事件保存在内存，进程重启后不能恢复；
- raw log 只有路径和哈希，尚未注册为 `ArtifactManifest`；
- 尚未实现 API key/ChatGPT auth 的生产凭据注入。

## 替代方案

- 解析默认人类可读 stdout/stderr：拒绝，事件语义不稳定。
- 直接把 Codex SDK 对象放入领域层：拒绝，破坏 adapter 边界。
- 使用 `danger-full-access` 简化权限：拒绝，Phase 0 没有足够外层隔离。
- 本轮执行真实模型烟雾测试：暂缓，缺少 BraveNewWorld 固定基线、成本登记和凭据隔离。

## 退出策略

Codex SDK、app server、OpenHands 或远程 Agent 都可以实现相同 `AgentExecutor`。领域工作流只消费 `AgentRunHandle`、`RunEvent`、取消结果和 `AgentRunResult`，不依赖 Codex JSON 对象。

