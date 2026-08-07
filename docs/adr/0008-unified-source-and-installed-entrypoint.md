# ADR-0008：源码、验证、UI 与安装后 CLI 使用统一入口

- 状态：Accepted
- 日期：2026-08-07

## 背景

随着 CLI、应用控制器和可视化界面增加，仓库已经出现 `.venv/bin/jobslayer`、`python -m jobslayer`、直接 unittest 命令和 UI 子命令等多种调用方式。如果开发验证和正式入口分别维护，容易发生解释器、依赖、命令参数和完成标准漂移。

用户要求把 JobSlayer 交互界面主入口固化为统一脚本，并让后续开发验证与正式程序都经过该基础入口。

## 决定

仓库根目录新增可执行 `./jobslayer`，作为源码 checkout 中唯一推荐入口。它只负责：

1. 选择 `JOBSLAYER_PYTHON`、仓库 `.venv` 或当前 Python；
2. bootstrap 仓库 `src`；
3. 调用 `jobslayer.launcher:main`。

`python -m jobslayer`、setuptools 生成的 `jobslayer` console script 和根脚本全部调用同一个 public launcher。CLI 注册 `ui` 作为 `serve-review` 的稳定别名，并注册 `check` 作为完整开发门禁。

`check` 由独立 `DevelopmentCheckRunner` 编排完整 unittest、compileall、pip check、测试床登记和 Git diff 检查。仓库 `AGENTS.md` 将 `./jobslayer check` 设为报告完成前的唯一标准命令。

根脚本不得拥有业务或工作流逻辑；UI、验证和正式命令仍位于各自模块。环境变量只允许显式选择解释器，不改变验证步骤或权限。

## 后果

- 开发者和自动化只需记住 `./jobslayer ui` 与 `./jobslayer check`；
- 安装后 CLI、模块运行和源码运行不会使用不同分发逻辑；
- 完成验证步骤有一个可测试、可版本化的来源；
- 根脚本会自动使用仓库 `.venv`，避免系统 Python 缺少 Pydantic；
- `check` 依赖源码 checkout、Git 和测试文件，不是生产运行命令；
- Windows checkout 仍应使用安装后 console script 或 `python -m jobslayer`，根可执行脚本主要面向当前 POSIX 开发环境。

## 替代方案

- 只依赖 `.venv/bin/jobslayer`：拒绝，路径绑定具体环境且不能固化验证标准。
- 为 UI、测试和正式程序分别创建 shell 脚本：拒绝，会复制环境解析和逐渐产生行为分叉。
- 在根脚本中实现所有命令：拒绝，脚本会成为不可测试的第二套应用层。
- 使用 Makefile/Taskfile：当前不采用，会增加另一个入口；未来可调用 `./jobslayer`，但不能取代它。
- `check` 只运行 unittest：拒绝，依赖、打包、测试床登记和 diff 仍可能在报告完成后失败。

## 退出策略

未来可将 launcher 扩展为服务模式或远程控制平面入口，但 `jobslayer.launcher:main` 保持稳定。若引入构建系统，它必须调用相同 launcher/check 契约，而不是重新定义成功标准。
