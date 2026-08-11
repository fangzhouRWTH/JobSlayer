# JobSlayer 统一入口

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

### 可视化交互

```bash
./jobslayer ui \
  examples/decision-card.example.json \
  --actor-id local-reviewer \
  --output .jobslayer/decisions/example.json \
  --open-browser
```

`ui` 是 `serve-review` 的稳定短别名，两者进入同一实现。界面仍遵守 [可视化审查边界](VISUAL_REVIEW_UI.md)：只记录决定，不应用工作流、合并或部署。

### 完整开发验证

```bash
./jobslayer check
```

该命令按固定顺序运行且汇总所有结果：

1. `python -m unittest discover -s tests -v`；
2. `python -m compileall -q src tests`；
3. `python -m pip check`；
4. 通过统一模块入口校验 BraveNewWorld 测试床登记；
5. 校验 BraveNewWorld scripted task/profile/runbook/patch 的交叉绑定；
6. 校验真实 Codex task/profile/runbook 的交叉绑定；
7. `git diff --check`。

`check` 只能在 JobSlayer 源码 checkout 中使用。通常会自动查找根目录；从其他工作目录运行时可以传 `--root /path/to/JobSlayer`。任何一步失败都会使最终退出码非零，但其他步骤仍继续执行，便于一次看到完整问题列表。

### 正式功能

原有命令全部保留并通过同一入口调用，例如：

```bash
./jobslayer validate-task examples/task.example.json
./jobslayer validate-testbed testbeds/brave-new-world.json
./jobslayer inspect-testbed testbeds/brave-new-world.json
./jobslayer validate-runbook runbooks/bnw-scenario-slow-001.json
./jobslayer run-task runbooks/bnw-scenario-slow-001.json
./jobslayer validate-runbook runbooks/bnw-filter-demo-001-codex.json
./jobslayer run-task runbooks/bnw-filter-demo-001-codex.json \
  --authorized-by local-human-operator
./jobslayer inspect-run .jobslayer/runs/bnw-scenario-slow-001-run-01
./jobslayer inspect-readiness --state-root .jobslayer --required-reviewed-tasks 20
./jobslayer inspect-recovery RUN_DIR
./jobslayer recover-run RUN_DIR
./jobslayer review-run RUN_DIR --actor-type agent --actor-id reviewer \
  --status accepted --summary "审查结论"
./jobslayer run-ui RUN_DIR --actor-id local-supervisor --open-browser
./jobslayer apply-run-decision RUN_DIR --authority AUTHORITY.json
./jobslayer integrate-run RUN_DIR
./jobslayer cleanup-run RUN_DIR
./jobslayer verify-journal .jobslayer/audit.jsonl
./jobslayer review-decision card.json --actor-id reviewer --output decision.json
./jobslayer demo --journal .jobslayer/demo.jsonl
```

`inspect-testbed` 从登记的本地 checkout 提示读取 Git 事实，检查工作树、HEAD、标签和 origin，并明确输出基线是否已发布。它不会执行、提交、fetch 或 push；其他目录可用 `--checkout` 覆盖本地提示。

`run-task` 到 `cleanup-run` 构成可恢复的本地最小成功路径，详见[最小开发闭环与体验测试手册](MINIMUM_DEVELOPMENT_LOOP.md)。`inspect-recovery` 只读分类 run；当前 `recover-run` 只会从权威 review ledger create-only 重建缺失的决策卡投影，不补写工作流、不覆盖可疑文件，也不重跑 Agent。scripted replay 使用登记 policy；真实 `codex_cli` 必须额外提供声明式 `--authorized-by`，且当前只允许 low-risk、`workspace_write` 和一次尝试。`apply-run-decision` 只进入 `Integrating`；`integrate-run` 必须由操作员另行显式调用，并且只在审核补丁、提交 tree、固定基线、目标分支和干净状态都匹配时执行本地 fast-forward。没有子命令会 push 或部署。

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
| `/jobslayer` | 仓库可执行入口、解释器选择和源码 bootstrap |
| `/jobslayer.cmd` | Windows 仓库入口、解释器发现和退出码透传 |
| `jobslayer.launcher` | 源码、模块和安装后脚本共用的稳定公共入口 |
| `jobslayer.cli` | 命令 schema 与功能分发 |
| `jobslayer.development.checks` | 版本化开发验证步骤与退出汇总 |
| `jobslayer.recovery` | 提供方无关的恢复分类与安全恢复协议 |
| `jobslayer.supervision.*` | UI/session/决定能力，不进入 launcher 环境逻辑 |
| `jobslayer.integration.*` | 提供方无关的源码集成协议 |
| `jobslayer.adapters.local_git_integration` | 受控本地 commit/fast-forward 实现；不操作远端 |

后续新增正式命令必须注册到统一 CLI；新增开发完成门禁必须加入 `DevelopmentCheckRunner`。禁止再创建绕开本入口的平行启动脚本。

## 平台边界

本地命令 runner 和 Codex adapter 只依赖公共 `ProcessSupervisor` 协议。默认
native factory 在 POSIX 返回 session/process-group 实现，在 Windows 返回
process-group/`taskkill` 实现；测试、容器或未来 worker 可注入其他实现而无需
修改领域模型或应用控制器。这只提供超时与取消所需的进程树清理，不升级为
网络、资源或系统调用安全隔离。固定 patch 通过二进制 stdin 进入 Git，避免
Windows 文本模式改写证据字节；源控 patch 文件也禁止行尾转换。

外部测试床的验证 argv 是受治理输入，JobSlayer 不按操作系统偷偷改写。当前
BraveNewWorld profile 明确登记 `./bnw`，所以 JobSlayer 自身的 Windows 开发
门禁已经可用，并不代表该外部 POSIX 命令已自动获得 Windows 实现。
