# ADR-0026：执行隔离、worker lease、预算与上下文采用统一治理装饰器

- 状态：Accepted
- 日期：2026-08-12

## 背景

Codex 自身的 `workspace-write` 不是宿主机安全边界；长任务还需要可恢复 worker 所有权、
执行前预算、增量 usage、上下文内容绑定以及超限取消。把这些策略交给 Agent 或分别散落
在 adapter 中会违反 JobSlayer 拥有工程真相的原则。

## 决定

1. 定义跨平台 `SandboxLauncher`/`SandboxExecutor`、`WorkerLeaseStore`、`BudgetStore` 和
   `ContextPackage` 端口。Windows 与 Linux 使用同一接口；无法实现强控制的平台报告能力
   缺口并 fail closed。
2. Linux adapter 使用 bubblewrap user/pid/uts/ipc/network namespace，从空 root 开始，只
   读挂载运行时目录，只写挂载单个 `/workspace`，并由 `prlimit` 和进程监督器限制 CPU、
   内存、进程数、wall timeout 与整个进程树。
3. SQLite worker lease 持久化 acquire、heartbeat、cancel-requested、release 和 orphan
   expiry；同一 run 只允许一个 live lease。取消信号必须在状态持久化之后发送。
4. 预算由 source-controlled task/run contracts 确定，执行前 reserve 并消耗 attempt；
   normalized usage 增量扣减，token/cost/time 超限持久化为 exhausted 后取消。repair 次数
   小于 attempt 上限且由确定性计数器约束。
5. 上下文只从 admitted root 的普通文件构建，拒绝 symlink/escape，在注册前检查总字节；
   组件和 package 都带内容哈希。`GovernedAgentExecutor` 在 launch 前同时验证上下文、短期
   grant、沙箱能力、预算和 lease，缺一不启动。

## 后果

- WSL 真实测试覆盖无网络、工作区外 host 文件不可见、root 不可写、CPU/内存/进程数和
  timeout 后无孤儿进程。
- 原生 Windows 控制平面继续可用；尚无等价 Windows 强沙箱 adapter 时，强治理的未知
  Agent 任务不会退化为非隔离执行。
- 当前不引入 OCI/Kubernetes/Dagger；当真实部署需要时可在相同端口后增加 adapter。

