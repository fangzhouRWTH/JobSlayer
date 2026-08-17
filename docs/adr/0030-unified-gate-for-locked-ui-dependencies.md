# ADR-0030：锁定外部 UI 依赖进入统一开发门禁

- 状态：Accepted
- 日期：2026-08-17

## 背景

PR #1 把 `UI_Framework` 分支合入 `main`，依据 ADR-0028 增加隔离的 React/TypeScript/Vite
Workbench 原型，并通过 lockfile 接入 React Flow、Monaco、xterm.js、ECharts、
`react-markdown` 和 Lucide。ADR-0029 随后提供固定校验的 Node 与 `npm ci` 初始化，但根级
`jobslayer check` 和 GitHub `governed-corpus` job 仍只验证 Python 与 Phase 0 契约。

这会允许外部前端依赖、TypeScript 类型或 production bundle 在完整开发门禁之外损坏，
与“统一 launcher 拥有完整开发序列”的仓库约束不一致。另一方面，验证命令不应静默升级
依赖或依赖公网来改变 lockfile 解析结果。

## 决定

1. `DevelopmentCheckRunner` 增加 `ui` 步骤，以锁定的 `package-lock.json` 执行
   `npm --prefix ui-framework run check`，同时覆盖 TypeScript `--noEmit` 与 Vite production
   build。任何失败都使统一门禁失败。
2. 该步骤通过 manifest 驱动的 bootstrap 解析项目 Node/npm，并使用 `--offline`；完整门禁
   只消费已经初始化且经 `npm ls --all` 校验的依赖，不在验证期间从网络升级外部库。
3. GitHub `governed-corpus` matrix 先运行 `scripts/bootstrap.py` 安装 Python 与 lockfile UI
   依赖，再通过同一 bootstrap 环境调用 `jobslayer check`、语料构建和 readiness 检查。
   PostgreSQL contract job 继续只安装其专用 Python extra，不承担无关的 UI 验证。
4. UI build 进入工程完成门禁不改变 ADR-0028 的产品边界：外部库对象不得进入
   `jobslayer.domain`，Stage 0 仍使用 mock data，不能被描述为已接通 read model、事件流、
   身份或任何写命令。
5. 后续 UI 依赖新增或升级仍必须有实际使用点、更新 lockfile，并通过 production build；
   漏洞与版本检查作为依赖决策证据记录，但不在每次离线门禁中主动改写依赖树。

## 后果

- 新 checkout 必须先运行 `init.cmd` 或 `sh ./init.sh`；初始化负责联网准备，统一门禁负责
  离线复用和验证，职责清晰。
- Python、控制面契约和外部 UI 栈现在共享一个 8 步完成信号，CI 不再允许前端构建漂移。
- 前端 build 成功只证明依赖与产物可重复构建，不证明视觉验收或真实控制面接线完成；
  Stage 1 read-only vertical slice 仍需独立契约与实施任务。

