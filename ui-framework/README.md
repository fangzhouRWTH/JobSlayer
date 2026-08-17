# JobSlayer Workbench Interaction Prototype

这是与 Python 控制平面隔离的 Stage 0 交互原型，用固定样例演示：

- 总览索引与工作台导航；
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

打开 Vite 打印的 loopback URL。生产构建检查：

```powershell
.\init.cmd -- npm --prefix ui-framework run check
```

## 原型边界

- 所有数据来自 `src/mockData.ts`；
- 不导入或修改 `src/jobslayer`；
- 不调用 Agent、shell、Git、数据库或控制面 API；
- 按钮只演示本地交互并显示未提交提示；
- React Flow JSON 不是 Workflow IR；
- 人工决定不写入审计链，刷新后消失；
- 本目录不替代现有 `supervision/ui` 和 `management/ui`。

项目设计规则见 [`docs/INTERACTION_DESIGN_GUIDE.md`](../docs/INTERACTION_DESIGN_GUIDE.md)，架构边界见 [`ADR-0028`](../docs/adr/0028-isolated-web-workbench-interaction-prototype.md)。

## 依赖口径

当前只安装被示例实际使用的库。PDF.js、Tauri、持久布局和真实 transport 在对应需求与契约成立前不引入。`package-lock.json` 固定本原型已验证的完整依赖树。
