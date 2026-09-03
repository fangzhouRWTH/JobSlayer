# ADR-0052：以左侧垂直栏拆分 TaskManager 五个聚焦版面

- 状态：Accepted
- 日期：2026-09-03
- 取代：[ADR-0047](0047-single-screen-task-graph-preview.md) 的“仅一个可达版面”和“不展示
  Backlog/执行投影”限制
- 保留：ADR-0047 的任务编排页 2:1 DAG/上下文关系及右栏节点详情/Agent 对话结构
- 依据：[ADR-0050](0050-semantic-elastic-ui-design-contract.md)、
  [ADR-0051](0051-pinned-read-only-ui-advice-adapter.md)

## 背景

单屏任务图已证明“图 → 节点 → 对话”的核心规划结构，但 TaskManager 继续形成串行执行闭环后，首页
说明、Agent 能力、跨任务态势和具体 run 反馈若都叠回同一页面，会再次造成信息密度失控。产品负责人
明确要求在窗口左缘增加垂直版面栏，并建立首页、Codex/Agent 状态、任务 Backlog/总控、任务编排和
具体任务执行五个版面。

本次先通过固定 UI/UX Pro Max adapter 收集绑定 SUID v1 的证据。UX 结果要求活动位置有可见反馈、
compact control 使用原生语义与可访问名称、键盘顺序匹配视觉顺序、focus 可见，并建议 URL 表达当前
版面。design-system 结果倾向高密度、克制的专业工具风格并反对仅靠颜色表达状态。React 专项查询返回
零项，因此不作为实现依据；通用营销 pattern 和建议色值也因不符合当前产品语境而未直接采用。

## 决定

1. TaskManager 保持一个 App 和一份认证会话/任务 read model；左侧 72px 垂直栏只切换展示职责，
   不创建独立 store 或工作流。窄屏栏缩为 54px，并隐藏短标签但保留 `aria-label`、`title` 和原生
   button。
2. 五个固定版面及顺序为：
   - 首页：Logo、产品说明、真实任务统计、四个工作入口和活动 SUID 摘要；
   - Codex/Agent 状态：会话、规划 adapter、execution capability、登记目标和持久 provider run
     引用；
   - 任务 Backlog/总控：所有任务、选中任务 backlog 和最近 append-only 事件；
   - 任务编排：完整保留 v1 的真实 DAG、节点详情、Agent 对话、候选应用/拒绝与任务新建；
   - 具体任务执行：run binding/stage/revision 和节点 Kernel 状态、最新 observation、evidence 与
     transition 数量；无 run 时显示明确空状态。
3. 一次只渲染一个主版面。稳定 hash 为 `#/home`、`#/agent`、`#/control`、
   `#/orchestration`、`#/execution`；浏览器前进/后退恢复版面，旧 `#/task-manager` 兼容映射到任务
   编排。默认无 hash 进入首页。
4. 活动按钮同时使用位置条、边框、背景、图标与 `aria-current="page"`，不只依赖颜色。所有入口遵循
   DOM/视觉一致顺序和全局 `:focus-visible`；关注数量是补充文本，不替代状态标签。
5. 新版面只消费既有 TaskManager session/task/detail/run read model。配置过 adapter 不等于正在
   运行；只有 journal 中的 provider reference/observation 才显示为执行引用。UI/UX Pro Max 保持
   CLI-only，不由浏览器调用。
6. SUID `focused-task-graph@2` 以 v1 hash 为 parent，逐项记录新增区域、关系、journey 和要求。
   产品负责人对原 `requirement.single-screen` 的 material 变化提供 human authorization；活动 binding
   引用本 ADR、用户方向和两份 normalized UI advice artifact。新增视觉单元先保持 planned，待人工
   接受后再通过后续 revision 晋升 stable。

## 后果

- 用户可在不挤压 DAG 的情况下快速切换产品说明、Agent 能力、任务态势、规划与执行反馈；当前任务
  选择在版面间持续复用。
- UI 重新成为多版面，但没有恢复旧通用 Workbench、mock 页面或全局功能矩阵；版面数量和职责由 SUID
  与本 ADR 固定。
- 当前 Agent/总控/执行页以可验证的只读投影为主。后续增加写操作必须单独定义权限、expected revision、
  幂等、审计和渐进披露，不能因为已有版面而默认获得授权。
- hash 是展示位置，不是领域工作流状态。前端路由丢失或被篡改最多影响当前版面，不能改变任务真相。

## 未采用方案

- 把五类内容做成任务编排页内 tab：会继续挤压 DAG/右侧上下文，并混淆全局与当前节点职责。
- 恢复旧 Workbench sidebar 和历史 mock 页面：扩大产品面且形成与当前 TaskManager 不一致的数据源。
- 只用无标签图标和颜色高亮：发现性、键盘使用和辅助技术语义不足。
- 在 Agent 状态页轮询或直接启停 Codex：当前没有对应的认证 command/lease 契约，会旁路控制面。
- 让 UI advice 自动选择样式或生成页面：第三方建议是 evidence，不是设计决定或代码执行器。
