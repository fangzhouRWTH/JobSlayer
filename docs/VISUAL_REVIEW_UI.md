# 极简可视化审查界面

## 目标与边界

当前界面是 Phase 0 的本地“薄控制面”，不是项目管理后台。它只呈现并调用已经存在的真实能力：

- 严格校验后的 `DecisionCard`；
- 可选的、经过哈希链验证且仅属于当前 task 的状态历史；
- 决策证据、风险、选项、后果和受影响制品 ID；
- 一次 create-only 的 `HumanDecision` JSON 记录。

界面不会声称身份已认证，不会应用决定，不会执行 Git 集成、push 或部署。对 merge review，页面固定显示当前运行时边界：批准应用后只进入 `Integrating`，只有操作员另行显式执行且证据复核通过的 `integrate-run` 才能本地快进并进入 `Completed`。这也让框架升级前已经持久化的旧卡片在展示时不会掩盖当前真实状态语义。提供审计日志时，如果卡片要求的 `PlanReview`/`MergeReview` 与任务当前状态不一致，前后端都会禁止提交。

## 启动

```bash
./jobslayer ui \
  examples/decision-card.example.json \
  --actor-id local-reviewer \
  --output .jobslayer/decisions/task-example-001.json \
  --port 8765 \
  --open-browser
```

如有控制器审计日志，可追加：

```bash
--journal .jobslayer/audit.jsonl
```

服务只允许绑定 `127.0.0.1`/`localhost`。不使用 `--open-browser` 时，命令会打印本地 URL；按 `Ctrl+C` 停止。输出文件已存在时界面进入只读状态，不覆盖原决定。

`actor_id` 当前只是本地身份声明。决定文件仍需由 `DecisionApplicationService` 结合真实授权、当前状态和原验证报告重新核验后才能推进工作流。

真实 run 到达 `MergeReview` 后，可以直接接入它生成的 card 和 journal：

```bash
./jobslayer run-ui .jobslayer/runs/RUN_ID \
  --actor-id local-supervisor \
  --open-browser
```

该命令先检查 run record、workflow journal 和制品，再使用 run 目录中的 `decision-card.json`、`workflow.jsonl` 和默认 create-only `decision.json`，不需要人工拼接内部路径。

## 页面信息结构

```text
主栏                          侧栏
├─ 决策摘要与真实状态          ├─ 审计转换时间线
├─ 证据及 SHA-256             ├─ 当前能力边界
└─ 选项、后果与理由输入        └─ 受影响制品 ID
```

视觉采用单一强调色、系统字体和响应式双栏；不使用图标库、动画、外部字体或前端框架。所有动态数据通过 DOM `textContent` 写入，不把卡片内容作为 HTML 插入。

## 模块边界

| 模块 | 责任 | 可独立替换/调试 |
|---|---|---|
| `supervision/session.py` | 卡片、审计状态、既有决定和提交规则 | 可脱离 HTTP 做单元测试 |
| `supervision/records.py` | 提供方无关的决定存储协议 | 可换数据库实现 |
| `adapters/local_decisions.py` | 0600、create-only、本地 JSON 持久化 | 可直接用 CLI 测试 |
| `supervision/web.py` | loopback HTTP、JSON API、会话令牌和安全响应头 | 可用 HTTP 集成测试 |
| `supervision/ui/index.html` | 语义结构 | 无业务数据 |
| `supervision/ui/styles.css` | 极简布局与响应式样式 | 不影响 API |
| `supervision/ui/app.js` | API 读取、DOM 渲染和表单提交 | 不拥有工作流规则 |

## 本地 API

- `GET /api/session`：返回卡片、卡片哈希、身份声明、状态历史、现有决定和能力边界；同时返回本进程随机提交令牌。
- `POST /api/decisions`：只接受 `selected_option_id` 和 `rationale`，并要求 `X-JobSlayer-Session`。成功返回 `201 recorded_not_applied`。
- `409`：卡片状态过期、已经存在决定或并发创建冲突。
- `403`：缺少或错误的本地会话令牌。

服务不设置 CORS，POST 使用自定义会话头，并设置 `default-src 'self'` CSP、`frame-ancestors 'none'`、`no-store`、`nosniff` 和 `DENY` frame header。这些措施只保护本地 Phase 0 操作，不等同于用户认证或远程部署安全。

## 调试顺序

1. 用 `./jobslayer review-decision` 验证同一卡片和输出路径；
2. 调用 `GET /api/session` 检查卡片/审计绑定；
3. 查看浏览器网络面板中的 `/api/decisions` 状态码和 JSON；
4. 用 `HumanDecision.model_validate_json` 校验输出；
5. 最后才检查 CSS/DOM 展示问题。

这样可以区分领域规则、文件持久化、HTTP 适配和纯展示问题，避免把业务错误埋在前端状态中。
