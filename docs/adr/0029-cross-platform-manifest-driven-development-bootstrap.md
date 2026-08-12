# ADR-0029：跨平台、manifest 驱动的开发环境初始化

- 状态：Accepted
- 日期：2026-08-13

## 背景

根 `jobslayer` / `jobslayer.cmd` 已统一应用入口和 Python 解释器选择，但不会创建 `.venv`、安装项目依赖或管理前端工具链。引入 React Stage 0 原型后，Windows 开发机可能有 Python 而没有全局 Node/npm；要求每位开发者手工修改 `PATH` 会产生不可重复环境，也不利于 CI、workspace init 或其他自动化入口组合。

跨平台初始化同时存在安全边界：不能默认调用 winget、Homebrew、apt、dnf 或 sudo 修改系统；不能下载未固定版本或未验证内容；检测模式不能产生副作用；初始化也不能进入 JobSlayer workflow、identity、run 或 artifact 状态。

## 决定

1. 提供根 `init.cmd`（Windows）和 `init.sh`（POSIX）作为未准备 checkout 的开发环境入口。平台 wrapper 只发现 Python，并调用同一个标准库实现 `scripts/bootstrap.py`；不复制安装策略。
2. Python 3.11+ 是唯一系统前置条件。初始化在仓库 `.venv` 中执行 editable install 和 `pip check`，不修改全局 Python/pip。可选依赖只能从 `pyproject.toml` 已声明的 `postgres`/`observability` 组显式选择。
3. Node 先复用满足最低版本且带可用 npm 的显式/系统 runtime；否则从 nodejs.org 下载 `bootstrap/toolchains.json` 固定的 Node LTS 发行包。URL、平台/架构、根目录和 SHA-256 均受源码控制，安装到用户级 JobSlayer tool cache，不修改持久 `PATH`，不要求管理员权限。
4. UI 依赖只通过源码控制的 `package-lock.json` 和 `npm ci` 安装。Python 与 UI 使用 manifest hash state stamp 减少重复安装，但 readiness 仍必须通过真实 `pip check`/import 与 `npm ls`，不能仅信任 stamp。
5. `--check` 严格只读；`--check --json` 提供稳定 schema 和退出码供其他 init/CI 组合。`--offline` 禁止网络，`--skip-ui` 允许 Python-only worker，`--force` 只重装 manifest 管理依赖或替换专用 cache 中的无效版本目录。
6. 初始化入口可在 `--` 后运行解析出的 `python`、`jobslayer`、`node` 或 `npm`，从而使用项目 Node 而不依赖 shell `PATH`。命令使用参数数组直接启动；不提供任意 shell 拼接。
7. Node 解压在临时目录完成，发布前拒绝路径穿越和越界链接。显式环境变量路径无效时失败关闭，不静默回退。
8. `.venv` 与 `node_modules` 绑定宿主平台；同一物理 checkout 不跨 Windows/WSL 复用。state stamp 记录平台，检测到另一平台产物时失败并要求独立 checkout，避免互相覆盖原生依赖。

## 后果

- Windows、Linux/WSL 和 macOS 的 x86_64/arm64 checkout 共享一套安装/检测语义；Node 缺失不再要求手工全局安装。
- 系统仍需先有 Python 3.11+。初始化会给出明确错误，但不会自动安装系统 Python或调用特权 package manager。
- 用户级 Node cache 占用额外磁盘，但可跨 checkout 复用并按版本/平台隔离；删除后可从固定清单重建。
- `init` 只准备开发环境；`jobslayer` 继续作为正式公共入口，`jobslayer check` 继续拥有开发完成门禁。
- 更新 Node 版本/checksum 或新增平台是 durable toolchain decision，必须评审配置、测试、ADR/开发日志和真实平台验证。
