# ADR-0014：统一 CLI 与 ProcessSupervisor 接口

- 状态：Accepted
- 日期：2026-08-10

## 背景

ADR-0013 使 Windows 与 POSIX 都能运行 JobSlayer，但仍保留了两个容易混淆的
层次：使用者看到 `./jobslayer` 与 `jobslayer.cmd` 两种 bootstrap 名称，adapter
则直接调用模块级平台函数。平台行为虽然集中，却还不是一个可注入、可替换的
明确协议。

跨平台不应意味着上层代码到处判断操作系统。公共命令语义和进程生命周期
语义都需要稳定接口，平台脚本和具体系统调用只应是实现细节。

## 决定

1. 安装后的跨平台公共 CLI 是 `jobslayer <command> [arguments]`；源码环境的
   等价公共入口是 `python -m jobslayer <command> [arguments]`。二者都调用
   `jobslayer.launcher:main`。
2. 根目录 `jobslayer` 与 `jobslayer.cmd` 只作为未激活源码环境的 POSIX/Windows
   bootstrap，不定义新的命令或业务行为。
3. `jobslayer.execution` 公开提供方无关的 `ProcessSupervisor` Protocol，只有
   `popen_kwargs()` 与 `terminate()` 两项生命周期能力。
4. `PosixProcessSupervisor` 和 `WindowsProcessSupervisor` 实现同一协议；
   `native_process_supervisor()` 是默认选择工厂。
5. `GovernedLocalCommandRunner` 与 `CodexCliExecutor` 通过构造参数接收该协议，
   默认使用 native 实现。应用控制器、领域模型、权限、重试和完成判定不感知
   操作系统。
6. 保留原模块级进程函数作为兼容 facade，但新 adapter 代码不得绕过协议直接
   分支平台。

## 理由

- 同一个 CLI grammar 可用于 Windows、Linux、macOS、源码环境和安装包。
- 协议注入让取消/超时行为可用确定性 recording fake 验证，而不需要伪造领域
  证据或修改工作流状态。
- 未来引入 Windows Job Object、OCI worker 或远程 executor 时，可以替换
  supervisor/adapter，不改控制平面契约。
- 只用标准库和现有 packaging 能力，不增加基础设施依赖。

## 后果

- 环境创建与激活仍是平台相关操作，但激活后的 JobSlayer 命令和参数完全一致。
- `ProcessSupervisor` 只拥有一个已授权进程树的生命周期，不拥有命令政策、
  权限、验证或重试决定。
- 外部测试床必须自己提供平台通用 console command，或在版本化 profile 中
  明确保留平台限制；JobSlayer 不会把 `./bnw` 猜测替换为其他命令。

## 备选方案

- **只保留模块级 `os.name` 分支**：代码量少，但 adapter 无法注入或独立验证
  生命周期实现。
- **把平台写入 `TaskSpec`/`ValidationProfile`**：会让执行细节污染稳定领域
  契约，且不能解决 CLI 命名问题。
- **统一要求 Docker/WSL**：把宿主准备问题转移给基础设施，不符合当前本地
  MVP 范围。

## 复审条件

当出现真实远程 worker 或 Windows Job Object 需求时，新实现必须继续满足
`ProcessSupervisor`，并增加超时、取消、父进程提前退出和子进程继承句柄测试。
如果未来确需同一测试床在不同平台使用不同 argv，应另行设计版本化命令选择
契约，不扩张本协议的职责。
