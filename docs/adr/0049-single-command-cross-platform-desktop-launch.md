# ADR-0049：单命令跨平台桌面启动与受控服务生命周期

- 状态：Accepted
- 日期：2026-09-03
- 推进：[ADR-0029](0029-cross-platform-manifest-driven-development-bootstrap.md)、
  [ADR-0036](0036-focused-task-manager-product-surface.md)、
  [ADR-0047](0047-single-screen-task-graph-preview.md)

## 背景

TaskManager 的真实页面此前要求操作者依次初始化环境、创建 key、签发 planner session、在两个终端
启动 API/Vite，再手工打开浏览器。每一步都有独立参数和生命周期，首次使用容易遗漏，关闭页面也不
会自动回收后台进程。直接打开普通浏览器还会把产品面与用户现有浏览会话混在一起。

## 决定

1. 仓库根 `start.py` 是 Windows/Linux 源码 checkout 的单一应用入口。它以 Python 3.11+ 为唯一
   宿主前置条件，复用 `BootstrapManager` 检测并按需准备仓库 venv、固定 Node/npm、lockfile UI
   依赖和 `desktop` Python extra；不复制第二套初始化逻辑。
2. 桌面 extra 固定 pywebview 6.2.1。Windows 强制使用系统 WebView2 的 `edgechromium` renderer；
   Linux 安装 Qt extra 并强制 `qt` renderer。没有现代 renderer 或 Linux 图形会话时失败关闭，不
   静默退回旧浏览器内核。`--headless` 只用于显式的服务运行场景。
3. 启动器只在 loopback 上托管既有 TaskManager API 和 Vite UI，先检查端口，再逐个启动并通过
   `/api/task-manager/session` 和 Vite same-origin proxy 做健康验证。Vite proxy 端口来自启动器显式
   环境，不使用 shell 字符串拼接。
4. 首次运行创建受保护的本地签名 key；每次运行签发最长 24 小时、仅含 `planner` 角色的唯一临时
   session。关闭窗口后删除该 session。入口不自动授予 executor/reviewer/approver/operator-admin，
   也不启用外部 planning、任务执行、validation、checkpoint、merge、push 或 deployment。
5. API/Vite 是桌面入口拥有的独立进程组。窗口关闭、启动失败或 Ctrl+C 时按逆序回收完整进程树；
   stdout/stderr 写入 `.jobslayer/desktop/logs`。端口已占用或子进程提前退出时输出结构化上下文并
   失败，不接管不属于本次运行的服务。
6. `start.py --check` 保持只读，`--smoke-test` 执行服务和代理健康闭环后立即清理。原
   `init.cmd`/`init.sh`、`jobslayer` CLI 和手工高级 API 参数继续保留给开发、CI 与显式治理操作，
   但不再是普通打开 TaskManager 的必经步骤。

## 后果

- Windows 和 Linux 的普通启动都收敛为 `python start.py`，首次运行与后续运行使用同一个入口。
- Web UI 仍是既有 React/Vite 客户端，桌面壳不拥有任务状态、权限、重试、验证或完成判定。
- Windows 主机必须具有 WebView2 Runtime；Linux 必须有可用 X11/Wayland 会话。Qt Python 依赖由
  desktop extra 安装，但宿主图形/驱动问题仍会明确暴露。
- 默认桌面身份只能规划和讨论。需要真实 Codex、执行或审批时，操作者仍必须走既有显式 CLI 身份
  与 capability 开关，不能通过便利启动器扩大授权。
