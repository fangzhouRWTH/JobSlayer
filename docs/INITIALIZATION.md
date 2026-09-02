# JobSlayer 跨平台开发环境初始化

## 1. 目标

仓库根初始化入口负责检测并准备开发工具链：

```text
Python 3.11+（唯一系统前置条件）
        |
        +-- .venv + JobSlayer editable dependencies
        |
        +-- Node/npm detection
                |
                +-- reuse compatible system Node
                +-- or verified user-local Node LTS cache
        |
        +-- ui-framework npm ci from package-lock.json
```

它不启动服务、不创建身份、不迁移数据库、不调用 Agent、不改变工作流状态，也不安装系统软件或修改用户 `PATH`。

## 2. 快速开始

如果目标是直接打开 TaskManager，而不是单独操作开发工具链，Windows/Linux 推荐使用根目录单一
应用入口；它会调用本页初始化协议并继续启动 API、UI 与原生桌面窗口：

```powershell
py -3 start.py
```

```bash
python3 start.py
```

下列 `init` 入口保留给开发、CI、分组件初始化和高级手工服务编排。

Windows PowerShell 或 CMD：

```powershell
.\init.cmd
.\init.cmd --check
.\init.cmd -- npm --prefix ui-framework run dev
```

Linux、macOS 或 WSL：

```bash
sh ./init.sh
sh ./init.sh --check
sh ./init.sh -- npm --prefix ui-framework run dev
```

第三条命令通过初始化入口解析项目 Node/npm，不要求全局 `npm` 位于当前 shell 的 `PATH`。

## 3. 安装策略

### Python

- 要求宿主已有 Python 3.11 或更新版本；这是运行 JobSlayer 本身的最小前置条件；
- 自动创建仓库 `.venv`；
- 使用 `python -m pip install -e .` 安装项目；
- 根据 `pyproject.toml` 哈希和 `pip check` 判断是否需要重装；
- 不升级系统 Python、pip，也不安装到全局 site-packages。

可选依赖组：

```powershell
.\init.cmd --extra postgres --extra observability
```

只有显式列出的 `pyproject.toml` optional dependency group 可以安装。
`desktop` 组由 `start.py` 自动选择：Windows 安装 pywebview/WebView2 binding，Linux 安装
pywebview Qt backend；也可手工执行 `.\init.cmd --extra desktop` 或
`sh ./init.sh --extra desktop`。

### Node/npm

优先级：

1. 显式 `JOBSLAYER_NODE`，路径无效或版本不合格时失败，不静默回退；
2. `PATH` 中同时满足版本和 npm 探测的 Node；
3. JobSlayer 用户级工具缓存中的固定 Node LTS。

当 1/2 均不可用时，入口读取 `bootstrap/toolchains.json`，按操作系统与 CPU 架构下载固定 Node 发行包，先验证 SHA-256，再安全解压到用户缓存：

| 平台 | 默认缓存 |
|---|---|
| Windows | `%LOCALAPPDATA%\JobSlayer\toolchains` |
| macOS | `~/Library/Caches/JobSlayer/toolchains` |
| Linux/WSL | `${XDG_CACHE_HOME:-~/.cache}/jobslayer/toolchains` |

支持 Windows、Linux、macOS 的 x86_64/arm64。其他平台明确失败，不猜测兼容包。初始化不向用户/系统 `PATH` 写入持久配置。

`.venv` 与 `node_modules` 是宿主平台产物。同一个物理 checkout 不应同时由 Windows 和
WSL/Linux 初始化；入口检测到已记录的平台不一致或 `.venv` 只有另一平台的解释器时会
失败关闭。需要双平台开发时使用两个 clone/worktree，共享的用户级 Node cache 已按平台隔离。

### UI dependencies

- 只使用 `npm ci` 和源码控制的 `package-lock.json`；
- 安装到 `ui-framework/node_modules`；
- 通过 lockfile/package manifest 哈希、Node 版本和完整 `npm ls --all` 做幂等检测；
- manifest 未变且依赖完整时不重复执行 `npm ci`。

## 4. 检测与其他 init 集成

只读检测：

```powershell
.\init.cmd --check
.\init.cmd --check --json
```

```bash
sh ./init.sh --check
sh ./init.sh --check --json
```

退出码：

- `0`：所选组件已经就绪；
- `1`：依赖缺失、版本不满足、完整性失败或安装失败；
- `2`：命令参数组合无效；
- `127`：平台 wrapper 找不到可用 Python。

`--check --json` 只写 stdout JSON，适合被更上层的 workspace/bootstrap/CI init 脚本调用；它不创建目录、下载或安装。上层脚本应按退出码决定是否调用无 `--check` 的安装入口，不应解析自然语言输出。

可覆盖变量：

| 变量 | 作用 | 失败语义 |
|---|---|---|
| `JOBSLAYER_BOOTSTRAP_PYTHON` | 平台 wrapper 使用的 Python | 路径不存在则失败 |
| `JOBSLAYER_NODE` | 指定 Node executable | Node/npm 契约不通过则失败 |
| `JOBSLAYER_TOOL_CACHE` | 用户级工具缓存根目录 | 无权限/无效时失败 |

对应命令参数 `--tool-cache PATH` 优先于默认缓存。不要把 cache 或 `node_modules` 提交到仓库。

## 5. 离线、强制与分组件初始化

```powershell
.\init.cmd --offline
.\init.cmd --force
.\init.cmd --skip-ui
```

- `--offline` 禁止 Node 下载和 Python/npm registry 访问；要求所需包已安装，或由
  pip/npm 本地缓存与 `PIP_FIND_LINKS` 等显式本地源完整提供；
- `--force` 重新应用 manifest 管理的 Python/UI 项目依赖，但不会覆盖一个有效的 Node cache；
- `--skip-ui` 只准备 Python 环境，适合不参与前端开发的 worker/CI job。

离线模式不会把缺失依赖冒充为成功。检测模式与安装模式严格分离。

## 6. 在初始化环境中运行工具

在参数分隔符 `--` 后，只允许以下工具：

```text
python
jobslayer
node
npm
```

示例：

```powershell
.\init.cmd -- node --version
.\init.cmd -- npm --version
.\init.cmd -- jobslayer check
```

工具以参数数组直接执行，不拼接 shell 命令。入口不会扩大 JobSlayer runner、sandbox 或 Agent 权限；这里只是开发者主动运行的环境工具。

## 7. 安全与故障恢复

- Node URL、平台包和 SHA-256 固定在源码控制的 `bootstrap/toolchains.json`；
- 下载使用 `.part` 临时文件，哈希通过后原子发布；
- 解压前拒绝路径穿越、绝对链接和越界链接；
- 安装目录按版本/平台隔离；只有显式 `--force` 才会替换专用 cache 中的无效目录；
- Python/UI 状态 stamp 只是幂等提示，仍需 `pip check`、import probe 和 `npm ls` 通过；
- 删除 `.venv`、`ui-framework/node_modules` 或用户级 JobSlayer tool cache 后重新运行即可重建；
- 初始化失败不会修改工作流 journal、事务状态、run、artifact 或 identity 数据。

## 8. 与正式入口的关系

`start.py` 是普通 TaskManager 桌面应用入口；`init.cmd` / `init.sh` 是未准备环境时的开发初始化
入口；`jobslayer.cmd` / `jobslayer` 是环境已准备后的治理 CLI：

```text
start.py
        -> init protocol + planner session + API/Vite + native WebView

init.cmd / init.sh
        -> tools + .venv + project dependencies

jobslayer.cmd / jobslayer
        -> public launcher -> CLI/application services
```

初始化不复制 CLI 业务逻辑，正式命令也不承担系统软件安装。完整开发完成门禁仍是
`jobslayer check`；它会通过 bootstrap 的离线路径复用已校验的项目 Node/npm 与 lockfile
依赖，执行 UI TypeScript 和 production build。依赖尚未准备时应先运行初始化，不由完成
门禁临时联网改变依赖树。
