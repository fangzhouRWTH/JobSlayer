# ADR-0053：以 Calm Ops 收敛 TaskManager 的视觉密度与阅读负担

- 状态：Accepted
- 日期：2026-09-03
- 依据：[ADR-0050](0050-semantic-elastic-ui-design-contract.md)、
  [ADR-0051](0051-pinned-read-only-ui-advice-adapter.md)、
  [ADR-0052](0052-left-view-rail-and-focused-task-surfaces.md)

## 背景

五版面 TaskManager 已形成清楚的职责导航，但初版沿用了大量 7–10px 微型文字、接近纯黑的表面和较多
首屏摘要。产品负责人实际阅读后指出界面费力，并选择尝试 Calm Ops：保持技术控制平面的严肃性，
同时减少信息、放大文字并让长时工作更平静。

本轮通过固定 UI/UX Pro Max adapter 收集四组绑定活动 SUID v2 的证据。style 结果命中
Data-Dense Dashboard、Accessible & Ethical、Dark Mode (OLED) 和 Fluent 2；UX 结果要求可读字号、
4.5:1 普通文字对比、非颜色单一状态、受控行长和长 token 换行；design-system 结果支持 Minimalism &
Swiss Style、16/24/32px 间距层级和克制暗色。针对“progressive disclosure”的附加查询没有命中相关
条目，只返回自动轮播、标题换行和 focus，因此不把它作为删减依据。

## 决定

1. 将视觉方向命名为 **JobSlayer Calm Ops**。继续使用暗色，但从接近纯黑转为深炭灰，并建立背景、
   普通面板和抬升面板三级表面；品牌绿降低饱和度，只承担活动、连接和确认反馈。禁用扫描线、故障
   动画、装饰渐变和大面积霓虹发光。
2. TaskManager 持续阅读正文调整为 13–17px，任务/节点标题不低于 13px，ID、时间、revision 和状态等
   机器元数据不低于 10px；窄屏普通说明为 14–16px。等宽字体只用于机器数据，不再承载大段正文。
3. 左侧栏在桌面从 72px 增至 84px，图标、短标签和点击区域同步增大；窄屏仍保持 54px 图标栏，避免
   侵占主要内容。活动状态继续使用位置条、边框、背景、图标和 `aria-current`，不只依赖颜色。
4. 首屏摘要按当前决策职责收敛：
   - 首页保留三个任务指标，删除与 rail 重复的本地连接指标；
   - Agent 状态保留 API、规划 Agent、任务执行器三个卡片，把 CLI-only UI advice 合入边界说明；
   - 总控只显示最多六条最近事件，完整 append-only log 仍由后端保留；
   - 有 run 的执行页把 run/stage 合并，摘要从四块减到三块。
5. 任务编排的 DAG、节点详情、Agent 对话和 2:1 关系不变，只放大节点、详情、消息、输入区和状态文字，
   并降低面板阴影。候选图、权限门禁、无 run 和验证事实不得因“简化”被隐藏。
6. 不引入新 UI runtime 依赖。Skill 推荐的远程 IBM Plex Sans/JetBrains Mono font import 不采用；继续
   使用本机/系统 sans-serif fallback，并仅为机器数据保留系统 monospace。Fluent 2 只作为 calm
   hierarchy 参考，不导入其组件库。
7. 发布并人工激活 `focused-task-graph@3`。v2 的 13 个 stable 单元逐项保持完全相同；Calm Ops、可读
   字号和信息克制作为三个 planned requirement，待产品负责人实际接受后才可在后续 revision 晋升
   stable。

## 后果

- 同一屏显示的任务事实略少，但任务记录、完整日志和执行证据没有被删除；只是首屏投影更聚焦。
- 更大的文字会降低极端节点数量下的同时可见量，因此 DAG 继续依靠缩放/平移，列表和上下文列继续
  独立滚动。
- 84px 桌面 rail 会减少少量 DAG 宽度，但剩余工作区内部仍维持约 2:1 的图/上下文比例。
- 当前仅有深色 Calm Ops。浅色主题、用户字号设置和密度切换需要独立 token/偏好契约，不能由浏览器
  自行改变活动 SUID。

## 未采用方案

- 直接全局放大所有旧 Workbench 选择器：会改变当前 TaskManager 范围外的遗留原型，扩大验证面。
- 把正文统一提升到 16px 以上：对 DAG 和审计表格会造成过度截断；本轮采用“正文 13–17px、移动说明
  14–16px、机器元数据 10px”的分层尺度，并通过真实截图继续校准。
- 采用完整 Fluent 2 或 Adobe Spectrum 组件库：当前退出条件不需要新的运行时依赖，也会混淆本地
  SUID 与外部组件系统的职责。
- 自动采用 advisor 的 FAQ landing pattern、固定色值或远程字体：它们与任务控制面语境、供应链边界
  或现有品牌方向不一致。
