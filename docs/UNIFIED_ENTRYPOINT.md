# JobSlayer 统一入口

## 单命令桌面应用

普通 Windows/Linux 用户从源码 checkout 启动 TaskManager 时只需：

```powershell
py -3 start.py
```

```bash
python3 start.py
```

`start.py` 先复用 manifest 初始化器检测并准备 `.venv`、Node/npm、UI 和 desktop extra，再以
`planner + quick-agent + reviewer + approver` 角色的临时本地身份启动 TaskManager API 与 Vite UI。
这些角色让用户在执行页提交受治理的人工 review/approval、追加反馈并调用只读辅助；源码
Reviewer/Approver 同主体禁止仍由后端执行，durable execution/validation/integration adapter 也不会
因角色存在而自动启用。Quick Agent adapter
随桌面入口连接本机 Codex，并从 `model/list` 获取当前账户可用的模型、effort 与速度层，但只有用户发送
消息才产生模型 turn。只有 API 本身和 Vite same-origin
proxy 都健康后，才打开独立 WebView2/Qt 窗口。关闭窗口会回收本次拥有的两个进程树和临时 session；
它不会自动启用任务 planning、durable execution、验证、审批或集成能力；Quick Agent 的仓库写入仍需
用户在 Agent 页显式选择“快速执行”，且不具备任务链完成语义。

只读环境检查、服务烟测和显式无窗口模式分别为 `python start.py --check`、
`python start.py --smoke-test` 和 `python start.py --headless`。高级治理操作仍使用下述 `jobslayer`
CLI，不把便利启动器当成权限旁路。

Linux 只有真正创建 Qt 窗口时才要求 `DISPLAY` 或 `WAYLAND_DISPLAY`；检查、smoke 与 headless 可在
无图形会话主机运行。启动器对其固定的 `127.0.0.1` 健康检查禁用外部代理，并允许已关闭连接处于
`TIME_WAIT` 时立即安全重启；真实 listener 仍由端口前检拒绝。

## 环境初始化与正式入口分层

未准备的源码 checkout 先运行 `init.cmd`（Windows）或 `sh ./init.sh`（POSIX）。
该入口创建/检查 `.venv`，并为 `ui-framework` 解析兼容 Node/npm；完整规则见
[跨平台开发环境初始化](INITIALIZATION.md)。它不进入 CLI、状态机或运行数据。

```powershell
.\init.cmd
.\init.cmd --check --json
.\init.cmd -- npm --prefix ui-framework run dev
```

```bash
sh ./init.sh
sh ./init.sh --check --json
sh ./init.sh -- npm --prefix ui-framework run dev
```

环境就绪后，继续使用下述 `jobslayer` 公共应用入口。初始化和正式命令不互相复制职责。

## 入口约定

跨平台公共命令契约是：

```text
jobslayer <command> [arguments]
python -m jobslayer <command> [arguments]
```

在已安装/已激活的 Python 环境中，这两种形式在 Windows 和 POSIX 完全一致，
并统一进入 `jobslayer.launcher:main`。根目录脚本是未激活环境时的平台 bootstrap：

```bash
./jobslayer --help           # 源码仓库唯一推荐入口
python -m jobslayer --help   # Python 模块入口
jobslayer --help             # 安装后的 console script
```

```powershell
.\jobslayer.cmd --help       # Windows 源码仓库入口
python -m jobslayer --help   # Windows 模块入口
jobslayer --help             # 安装后的 console script
```

仓库内的文档、开发验证和手工操作优先表达为公共 `jobslayer` 命令；需要从
未激活的源码 checkout 启动时，POSIX 使用 `./jobslayer`，Windows 使用
`.\jobslayer.cmd`。两层根入口都只负责解释器选择和源码 bootstrap，不复制
CLI 业务逻辑。

## 主命令

### 本地认证准备

所有会改变决定、运行或 Git 状态的公共命令都要求短期签名 session，不再接受自由文本
`actor_id`/`authorized_by`。本地开发首次创建一个受保护的签名 key，再按最小角色签发
最长 24 小时的 session：

```bash
./jobslayer create-local-identity-key .jobslayer/identity/key.json
./jobslayer issue-local-identity-session \
  --key .jobslayer/identity/key.json \
  --subject-id local-approver --display-name "Local approver" \
  --role approver --output .jobslayer/identity/approver.json
```

`observer` 只读 Dashboard，`planner` 管理任务计划，`quick-agent` 使用独立 Codex 讨论/仓库写入，
`executor` 启动任务，`reviewer` 提交实现审查，`approver` 记录/应用决定并执行集成和清理，
`worker-admin` 管理 worker；`operator-admin` 仅用于确需
全部本地权限的受控运维。key/session 不得提交、交给 Agent 或写入日志/制品。

### 可视化交互

```bash
./jobslayer ui \
  examples/decision-card.example.json \
  --identity-session .jobslayer/identity/approver.json \
  --identity-key .jobslayer/identity/key.json \
  --output .jobslayer/decisions/example.json \
  --open-browser
```

`ui` 是 `serve-review` 的稳定短别名，两者进入同一实现。界面仍遵守 [可视化审查边界](VISUAL_REVIEW_UI.md)：只记录决定，不应用工作流、合并或部署。

Agent 开发管理 Dashboard 的脚本入口是：

```bash
./jobslayer serve-dashboard \
  --state-root .jobslayer/phase0-corpus/state \
  --identity-session .jobslayer/identity/observer.json \
  --identity-key .jobslayer/identity/key.json \
  --open-browser
```

`dashboard` 是短别名。读取 Phase 1 事务真相时，改用一对已有且经过迁移的路径：

```bash
./jobslayer dashboard \
  --control-plane-db .jobslayer/control-plane.sqlite3 \
  --artifact-root .jobslayer/artifacts \
  --identity-session .jobslayer/identity/observer.json \
  --identity-key .jobslayer/identity/key.json \
  --open-browser
```

Dashboard 仅绑定 loopback、只读且要求 `view_control_plane` 权限；不会自动创建或迁移
传入数据库，也没有写状态、审批、合并或部署 API。

### 协作式任务编排

任务编排 API 使用独立的 `planner` 身份和 `manage_task_plan` 权限：

```bash
./jobslayer orchestration-api \
  --identity-session .jobslayer/identity/planner-session.json \
  --identity-key .jobslayer/identity/planner-key.json
```

该历史 API 仍支持讨论、待应用 Agent proposal、节点 CRUD/支线/子任务和用户定稿 revision；
不会把计划变成 `TaskState`、启动执行、调用 Git 或标记完成。当前 Web App 的
`#/orchestration` 是 TaskManager 内部任务编排版面，不是旧独立 Workbench；它与首页、Agent、总控、
执行版面共享同一认证 read model。也可直接使用受认证 API，契约见
[协作式任务编排](TASK_ORCHESTRATION.md)。

### 完整开发验证

```bash
./jobslayer check
```

该命令按固定顺序运行且汇总所有结果：

1. `python -m unittest discover -s tests -v`；
2. `python -m compileall -q src tests`；
3. `python -m pip check`；
4. 校验 source-controlled 语义 UI revision、内容 hash、stable 保护和唯一活动 binding；
5. 验证固定 UI/UX Pro Max core 快照的来源、整树 hash、路径白名单和上游数据一致性；
6. 通过初始化所解析的项目 npm，离线执行 `ui-framework` 的 TypeScript 与 Vite production build；
7. 通过统一模块入口校验 BraveNewWorld 测试床登记；
8. 校验 BraveNewWorld Anygine 小 App task/profile/Codex runbook 的交叉绑定与预算；
9. `git -c core.autocrlf=true diff --check`，先按跨平台 checkout 规则规范化文本再检查空白与冲突标记。

`check` 只能在已经运行 `init.cmd`/`init.sh` 的 JobSlayer 源码 checkout 中使用；UI 步骤
不会联网补装或升级依赖。通常会自动查找根目录；从其他工作目录运行时可以传
`--root /path/to/JobSlayer`。任何一步失败都会使最终退出码非零，但其他步骤仍继续执行，
便于一次看到完整问题列表。

### 正式功能

原有命令全部保留并通过同一入口调用，例如：

```bash
./jobslayer validate-task examples/task.example.json
./jobslayer validate-testbed testbeds/brave-new-world.json
./jobslayer inspect-testbed testbeds/brave-new-world.json
./jobslayer validate-runbook runbooks/bnw-anygine-small-app-001-codex.json
./jobslayer validate-ui-design ui-designs/catalog.json
./jobslayer inspect-ui-design ui-designs/catalog.json --page-id task-manager
./jobslayer validate-ui-advisor
./jobslayer collect-ui-advice \
  --page-id task-manager --task-id ui-task-manager \
  --request-id task-manager-react-a11y-001 \
  --query "live updates accessibility" --mode stack --stack react
./jobslayer inspect-task-manager-target \
  runbooks/bnw-anygine-small-app-001-codex.json \
  --target-id brave-new-world-anygine-app-v1 \
  --dependency-attachment anygine-source=/absolute/path/to/Anygine \
  --dependency-attachment anygine-conan-toolchain=/absolute/path/to/conan \
  --validation-environment "DISPLAY=${DISPLAY}" \
  --validation-environment "XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR}"
./jobslayer run-task runbooks/bnw-anygine-small-app-001-codex.json \
  --identity-session .jobslayer/identity/executor.json \
  --identity-key .jobslayer/identity/key.json
./jobslayer inspect-readiness --state-root .jobslayer --required-reviewed-tasks 20
./jobslayer build-phase0-corpus
./jobslayer inspect-readiness --state-root .jobslayer/phase0-corpus/state --required-reviewed-tasks 20
./jobslayer inspect-recovery RUN_DIR
./jobslayer recover-run RUN_DIR \
  --identity-session .jobslayer/identity/operator-admin.json \
  --identity-key .jobslayer/identity/key.json
./jobslayer review-run RUN_DIR \
  --identity-session .jobslayer/identity/reviewer.json \
  --identity-key .jobslayer/identity/key.json \
  --status accepted --summary "审查结论"
./jobslayer run-ui RUN_DIR \
  --identity-session .jobslayer/identity/approver.json \
  --identity-key .jobslayer/identity/key.json --open-browser
./jobslayer issue-approval-authority \
  --key .jobslayer/identity/key.json \
  --identity-session .jobslayer/identity/approver.json \
  --decision-kind merge_review --output .jobslayer/identity/authority.json
./jobslayer apply-run-decision RUN_DIR \
  --authority .jobslayer/identity/authority.json \
  --identity-session .jobslayer/identity/approver.json \
  --identity-key .jobslayer/identity/key.json
./jobslayer integrate-run RUN_DIR \
  --identity-session .jobslayer/identity/approver.json \
  --identity-key .jobslayer/identity/key.json
./jobslayer cleanup-run RUN_DIR \
  --identity-session .jobslayer/identity/approver.json \
  --identity-key .jobslayer/identity/key.json
./jobslayer verify-journal .jobslayer/audit.jsonl
./jobslayer review-decision card.json \
  --identity-session .jobslayer/identity/approver.json \
  --identity-key .jobslayer/identity/key.json --output decision.json
./jobslayer demo --journal .jobslayer/demo.jsonl
```

`inspect-testbed` 从登记的本地 checkout 提示读取 Git 事实，检查工作树、HEAD、标签和 origin，并明确输出基线是否已发布。它不会执行、提交、fetch 或 push；其他目录可用 `--checkout` 覆盖本地提示。

`run-task` 到 `cleanup-run` 构成可恢复的本地最小成功路径，详见[最小开发闭环与体验测试手册](MINIMUM_DEVELOPMENT_LOOP.md)。`inspect-recovery` 只读分类 run；`recover-run` 可在严格 outcome 已持久化时补写 execution 首记录、重建缺失决策卡，也可在原转换、制品及 Git 后置事实一致时只补写 decision application、source integration 或 cleanup 记录。只有 execution intent 而没有 outcome 时会明确要求人工处理，绝不重跑 Agent；其他恢复也不补写工作流、不覆盖可疑文件或重放决定转换、Git 集成和 worktree remove。scripted replay 使用登记 policy；真实 `codex_cli` 需要绑定 task/run 的签名 execution authority，且 runbook 必须显式给出 token、费用、上下文、attempt/repair 上限。`apply-run-decision` 只进入 `Integrating`；`integrate-run` 必须由有权限的操作员另行显式调用，并且只在审核补丁、提交 tree、固定基线、目标分支和干净状态都匹配时执行本地 fast-forward。没有子命令会 push 或部署。

## 开发环境选择

根脚本按以下优先级选择 Python：

1. 显式环境变量 `JOBSLAYER_PYTHON`；
2. 当前宿主对应的仓库 venv：Windows 使用 `.venv/Scripts/python.exe`，
   POSIX 使用 `.venv/bin/python`；
3. 执行脚本的当前 Python（仅在前两项都不存在时）。

根脚本随后只做源码路径 bootstrap，并调用公共 launcher；不复制 CLI 业务逻辑。安装后的 console script 和 `python -m jobslayer` 直接调用同一 launcher，因此不会形成“开发脚本一套、正式程序另一套”的分叉。

如需显式解释器：

```bash
JOBSLAYER_PYTHON=/opt/jobslayer/bin/python ./jobslayer check
```

```powershell
$env:JOBSLAYER_PYTHON = 'C:\Python312\python.exe'
.\jobslayer.cmd check
```

路径必须指向现有 Python 文件，否则入口会明确失败，不静默回退。

## 模块责任

| 文件/模块 | 责任 |
|---|---|
| `/start.py` | Windows/Linux 单命令初始化、服务健康编排与桌面 App 入口 |
| `/jobslayer` | 仓库可执行入口、解释器选择和源码 bootstrap |
| `/jobslayer.cmd` | Windows 仓库入口、解释器发现和退出码透传 |
| `/init.sh`、`/init.cmd` | 未准备 checkout 的平台 Python 发现与统一初始化转发 |
| `scripts/bootstrap.py` | manifest 驱动的 Python/Node/UI 检测、安装与受限工具运行 |
| `bootstrap/toolchains.json` | 固定 Node 版本、平台发行包和 SHA-256 |
| `jobslayer.launcher` | 源码、模块和安装后脚本共用的稳定公共入口 |
| `jobslayer.cli` | 命令 schema 与功能分发 |
| `jobslayer.development.checks` | 版本化开发验证步骤与退出汇总 |
| `jobslayer.desktop.app` | 最小权限临时身份、API/Vite 进程组、健康检查、WebView 与清理生命周期 |
| `jobslayer.recovery` | 提供方无关的恢复分类与安全恢复协议 |
| `jobslayer.supervision.*` | UI/session/决定能力，不进入 launcher 环境逻辑 |
| `jobslayer.integration.*` | 提供方无关的源码集成协议 |
| `jobslayer.adapters.local_git_integration` | 受控本地 commit/fast-forward 实现；不操作远端 |

后续新增正式命令必须注册到统一 CLI；新增开发完成门禁必须加入 `DevelopmentCheckRunner`。禁止再创建绕开本入口的平行启动脚本。

## 平台边界

本地命令 runner 和 Codex adapter 只依赖公共 `ProcessSupervisor` 协议。默认
native factory 在 POSIX 返回 session/process-group 实现，在 Windows 返回
process-group/`taskkill` 实现；测试、容器或未来 worker 可注入其他实现而无需
修改领域模型或应用控制器。Linux 强治理任务还通过 `SandboxLauncher` 进入从空 root
启动的 bubblewrap namespace，只暴露只读运行时和一个可写 workspace，并限制网络、
CPU、内存、进程数、wall timeout 与进程树。原生 Windows 目前没有等价强沙箱 adapter；
需要这些能力的任务会失败关闭，可由同一接口调度到 WSL/Linux worker，不会静默降级。
固定 patch 通过二进制 stdin 进入 Git，避免
Windows 文本模式改写证据字节；源控 patch 文件也禁止行尾转换。

外部测试床的验证 argv 是受治理输入，JobSlayer 不按操作系统偷偷改写。validation profile 可为
同一 check 显式登记平台 argv；当前 BraveNewWorld profile 在 POSIX 授权 `./bnw`，在 Windows
授权 `.\bnw.cmd`。领域校验会逐平台复核 check/policy 一致性，未登记的平台变体仍失败关闭。
