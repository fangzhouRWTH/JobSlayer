# ADR-0028：隔离的 Web Workbench 交互原型与控制面契约边界

- 状态：Accepted
- 日期：2026-08-13

## 背景

ADR-0007 和 ADR-0027 已分别提供零前端依赖的本地审查页与只读管理 Dashboard，证明真实证据、身份和决定边界。它们不是用于验证图编辑、IDE 式运行检查、复杂制品对比和可观测性信息架构的长期前端框架。

外部交互设计文章建议使用 React/TypeScript/Vite，以及 React Flow、Monaco、xterm.js、ECharts 和文档查看组件构建 Web-first 工程工作台。本仓库同时要求不提前引入无退出条件支撑的基础设施，并禁止 UI、模型或外部 Agent 拥有工作流状态、权限、重试、验证或完成判定。

## 决定

1. 在根目录 `ui-framework/` 建立 Stage 0 React/TypeScript/Vite 原型。该目录不导入 Python 包、不注册 JobSlayer CLI/HTTP route，不连接数据库、Agent、worker 或 Kernel。
2. 原型以总索引连接 Workflow Studio、Run Inspector、Artifact Review 和 Observability 示例，全部读取固定 mock data。页面持续标识 `PROTOTYPE · MOCK`；写操作只更新浏览器组件状态并明确提示“未提交”。
3. 使用成熟库承载通用交互：React Flow 负责图视图、Monaco 负责 IR/Diff、xterm.js 负责只读终端渲染、ECharts 负责图表、`react-markdown` 负责 Markdown。它们不进入 `jobslayer.domain`，也不定义 canonical Workflow IR、事件、验证或批准语义。
4. React Flow 的位置、连线选择和画布状态属于 presentation metadata。未来真实接线必须建立显式 adapter，在 provider-neutral Workflow IR 与 UI view model 间转换；图 JSON 不得直接驱动 Agent runtime。
5. 未来 UI command 只能提交到认证 application API。权限、expected state/version、幂等、预算、验证和 actor authority 由后端检查；所有状态变化仍经 `WorkflowKernel.transition` 并形成可验证审计/事务记录。
6. 现有 `supervision/ui` 与 `management/ui` 保持原职责和部署/安全边界。本 ADR 不把 React 原型宣称为生产替代品，也不批准远程部署、Tauri、PDF.js、对象存储或新的控制平面服务。

## 后果

- 团队可以在不触碰内核的情况下评审长期工作台的信息架构、交互层级和库适配性。
- 仓库增加独立 Node lockfile 和单独的前端构建；完整 Python 门禁仍通过根 `jobslayer check` 执行，原型另运行 `npm run check`。
- 真实 read model、事件传输、认证与 command API 仍是后续受治理任务，不能用 mock 界面冒充。
- 新前端依赖必须有实际示例；PDF.js、Tauri 和 dock layout 等能力在需求成立前继续后置。
