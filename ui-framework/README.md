# JobSlayer Workbench Interaction Prototype

这是 JobSlayer Web-first 工作台。大部分页面仍是 Stage 0 固定样例；Task Orchestration
页面已经形成一个受限的本地纵向切片：

- 总览索引与工作台导航；
- 多计划搜索/切换/归档、任务讨论、Agent 候选差异与应用/拒绝、结构化节点、语义边 CRUD、
  完整度评估、历史比较/派生与定稿记录；
- Workflow Studio、React Flow 图和 canonical mock IR；
- Run Inspector、结构化事件、trace 与只读 xterm 输出；
- Markdown/JSON/Monaco Diff 制品审查和人工门；
- ECharts 可观测性、worker 与可行动提醒。

## 运行

推荐从仓库根运行统一初始化；没有全局 Node/npm 时会自动准备经校验的项目 Node：

```powershell
.\init.cmd
.\init.cmd -- npm --prefix ui-framework run dev
```

POSIX 使用 `sh ./init.sh` 和 `sh ./init.sh -- npm --prefix ui-framework run dev`。
已有 Node.js `>=22.12` 时也可在本目录直接运行 `npm ci`、`npm run dev`。

Task Orchestration 还需要先启动默认 `127.0.0.1:8780` 的认证 API：

```bash
./jobslayer orchestration-api \
  --identity-session .jobslayer/identity/planner-session.json \
  --identity-key .jobslayer/identity/planner-key.json
```

完整身份准备和运行方法见[协作式任务编排](../docs/TASK_ORCHESTRATION.md)。

打开 Vite 打印的 loopback URL。生产构建检查：

```powershell
.\init.cmd -- npm --prefix ui-framework run check
```

初始化完成后，根级 `jobslayer check` 也会离线执行同一 TypeScript + production build，
因此外部 UI 依赖和 bundle 已属于仓库统一完成门禁，而不是旁路检查。

## 页面边界

- Workflow Studio、Run Inspector、Artifact Review 和 Observability 仍从 `src/mockData.ts`
  读取固定样例；
- Task Orchestration 通过 Vite same-origin proxy 调用认证 loopback API，计划 revision 由
  Python 应用服务和追加式 store 拥有，浏览器不持久化权威计划；
- 默认 PlanningAgent 是确定性本地 fixture；后端可显式启用 Codex planning adapter，但 UI
  仍只显示并提交待应用提案，不调用 shell、Git 或执行工作流；
- 讨论中的 Agent 图是待应用 proposal，只有用户显式应用/CRUD/定稿才产生新 revision；
- React Flow JSON 不是 Workflow IR；
- React Flow 拖动坐标只按 plan 保存为浏览器 presentation metadata，不写入权威 revision；
- finalized plan 只是用户确认的设计制品，不等于 `TaskState.PLANNED` 或 Completed；
- 本目录不替代现有 `supervision/ui` 和 `management/ui`。

项目设计规则见 [`docs/INTERACTION_DESIGN_GUIDE.md`](../docs/INTERACTION_DESIGN_GUIDE.md)，
产品边界见 [`ADR-0028`](../docs/adr/0028-isolated-web-workbench-interaction-prototype.md)，
统一依赖门禁见 [`ADR-0030`](../docs/adr/0030-unified-gate-for-locked-ui-dependencies.md)。
任务编排边界见 [`ADR-0031`](../docs/adr/0031-versioned-collaborative-task-orchestration.md)。
交互式规划完善决策见 [`ADR-0032`](../docs/adr/0032-governed-interactive-planning-workbench.md)。

## 依赖口径

当前只安装被示例实际使用的库。PDF.js、Tauri、服务器端协作布局和真实 transport 在对应需求与契约成立前不引入。`package-lock.json` 固定本原型已验证的完整依赖树。
