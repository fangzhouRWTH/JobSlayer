# ADR-0046：以内容绑定的本地依赖 attachment 部署 Anygine 验证

- 状态：Accepted
- 日期：2026-09-01
- 推进：ADR-0045 中 Anygine source/toolchain attachment blocker

## 背景

BraveNewWorld 基线已证明能够消费固定 Anygine public targets，但 TaskManager 的隔离
run worktree 原先只能执行 `./bnw contract`。将本机绝对路径写入 task/profile 会破坏
跨机器契约，从父进程继承完整环境又会泄露凭据并丢失证据绑定。

## 决定

1. runbook 只声明 provider-neutral attachment requirement：稳定 ID、resource kind、期望
   SHA-256、Git revision/origin（如适用）、暴露的相对路径和唯一环境变量名。本机
   绝对路径始终由 operator 在启动时提供，不进入源控契约。
2. local target adapter 对 Git checkout 核对 clean status、HEAD、origin 和确定性 `git archive`
   SHA-256；对 file/directory 使用版本化目录摘要算法，并拒绝 symlink 或 special file。
3. observed dependency identity 和经源控 allowlist 允许的非敏感运行时环境摘要进入
   target source bundle；组装后的 run 保存完整不可变 binding。缺失或漂移的 attachment
   作为 target blocker，不隐藏目标，不启动外部过程。
4. validation runner 仍使用最小环境，只追加 binding 中的 attachment 变量与 allowlisted
   runtime values；`HOME`/`PATH`/temp/locale 等 runner-owned 变量不得覆盖。实际环境、
   source ID 和 source SHA-256 保存在每个 `CommandResult`。
5. adapter 在命令前、terminal 落盘前和证据采集时重新检查所有 attachment；任一内容、
   revision、origin 或 clean fact 变化都拒绝验证。TaskManager 再精确核对 command environment
   和 dependency evidence，然后才编译 verification report。
6. BraveNewWorld profile 当前将 `./bnw contract`、`./bnw test --jobs 4` 和
   `./bnw run --jobs 4` 作为三项 required checks。`DISPLAY`/`WAYLAND_DISPLAY`/
   `XDG_RUNTIME_DIR` 只能由 operator 显式提供且仅用于图形 runtime validation。

## 信任边界

当前 `GovernedLocalCommandRunner` 执行的是源控、精确 argv、非发布型验证命令。
`read_only` 在该 adapter 中表示“不提供修改 API，并以命令前后哈希/仓库事实拒绝任何
观察到的写入”，尚不是 namespace/ACL 强制的主机挂载只读。因此 attachment 不会传给
Codex 实现进程，也不得用于不受信任的任意命令。若未来需要对敌执行验证脚本，
必须将同一 binding 映射为 bubblewrap/container 的强制 read-only mount。

Conan generated toolchain 目录已内容绑定，但它引用的本机 Conan package cache 仍是受信任的
build-host prerequisite，尚未形成独立 package-closure manifest。命令证据能证明本次实际构建，
但不等价于可跨机器逐字节重现的 hermetic build。

## 后果

- TaskManager 现在能在隔离 BNW worktree 中产生与固定 Anygine source/toolchain 精确绑定的
  C++ build、CTest 和 GPU smoke 证据，ADR-0045 中的当前 blocker 已解除。
- 本地绝对路径不进入 task/profile，但装配后的 run 仍能完整追踪本次使用的实体。
- 下一个产品 blocker 是持久串行 coordinator 和 UI 收束，而不再是 Anygine 构建可达性。
