# ADR-0054：以 Codex App Server 建立与任务链隔离的 Quick Agent

- 状态：Accepted
- 日期：2026-09-03
- 依据：[ADR-0023](0023-fail-closed-long-running-development-safety.md)、
  [ADR-0025](0025-authenticated-control-plane-and-agent-credential-grants.md)、
  [ADR-0035](0035-provider-neutral-resumable-long-running-control-plane.md)、
  [ADR-0050](0050-semantic-elastic-ui-design-contract.md)、
  [ADR-0053](0053-calm-ops-readable-task-manager.md)

## 背景

产品负责人需要在 Agent 版面实时查看本机已登录 Codex 的剩余容量和下次刷新时间，并像直接使用
Codex CLI 一样讨论或快速处理仓库问题。该便利入口明确不属于任务 DAG：它不应要求先创建任务，也不
应成为绕过 JobSlayer 工作流、验证或完成门禁的第二控制面。

Codex CLI 的 `/status` 可以在交互式 CLI 中显示剩余限额；官方 App Server 则是富客户端嵌入接口，
提供 `account/rateLimits/read`、thread/turn 生命周期、增量 Agent 消息、命令输出、token usage 和中断
事件。因此页面不抓取终端文本，也不根据订阅价格或 token 数估算额度，而通过结构化协议读取原始窗口。

本轮还用固定 UI/UX Pro Max 快照查询“streaming agent chat quota remaining reset time execution mode
safety”。采纳其持续反馈和关键标签不隐藏的原则；与本页无关的 stacking context 结果不作为依据。

## 决定

1. 新增 provider-neutral `jobslayer.quick_agent` 端口和 Codex App Server adapter。UI 与 HTTP API
   只依赖规范化 capacity/session/event 契约，不暴露 Codex SDK 对象。adapter 通过本机 `codex
   app-server --stdio` 使用官方 core JSON-RPC 方法，不启用实验能力，也不调用无沙箱 `process/*`。
2. Quick Agent 必须显式 opt-in。API 只有带 `--allow-quick-agent` 才装配 adapter；身份必须含独立
   `quick-agent` 角色。`use_quick_agent` 允许额度、会话、讨论、中断和新会话，
   `execute_quick_agent` 单独保护可写 turn。默认桌面入口签发 `planner + quick-agent` 临时身份并连接
   adapter；只有用户发送消息才产生模型 turn。
3. 每轮发送前必须指定权限模式：
   - `discuss`：turn policy 为 `readOnly`、`networkAccess=false`；
   - `execute`：turn policy 为 `workspaceWrite`、`writableRoots` 仅当前 JobSlayer 仓库、
     `networkAccess=false`。

   thread start/resume 的 `sandbox` 使用 App Server 声明的 `read-only` / `workspace-write` 枚举；两者均
   设置 `approvalPolicy=never`。不发送需要 `experimentalApi` 的 `runtimeWorkspaceRoots`；adapter 对任何
   server-initiated approval/request 一律拒绝并生成可见事件，不自动升级权限。
4. 默认模型按产品方向为 `gpt-5.6-sol`，reasoning effort 为 `xhigh`。可选模型、effort、输入模态、
   service tier 与多 Agent runtime 版本必须从本机 App Server `model/list` 动态读取，正常缓存 300 秒；
   后端拒绝 provider 未公布的组合。UI 可在每轮前选择模型/版本、effort 和响应速度；runtime 版本是能力
   元数据而不是伪造的可选参数。单轮本地 watchdog 默认 1800 秒，可配置为 30–7200 秒；同一 adapter
   最多一个活动 turn，用户可显式中断。输入上限 16000 字符，单条 UI 事件上限 24000 字符。
5. 容量只采用 `account/rateLimits/read` 返回的 `usedPercent`、`windowDurationMins` 与 `resetsAt`。
   `remaining_percent = 100 - usedPercent` 是唯一确定性换算；保留主/次窗口、plan/bucket 和观测时间。
   正常缓存 30 秒，provider 更新通知会失效缓存，人工刷新可强制读取。字段缺失、非法、CLI 未登录或
   协议失败时返回明确 unavailable/error，不推断美元余额或可执行 token。
6. App Server 原始 stdout JSONL 与 stderr 按进程实例写入 `.jobslayer/orchestration/quick-agent/` 的
   私有诊断目录；UI 只得到规范化、长度受限的 user/agent/tool/system 事件，不显示隐藏 reasoning，
   只接受 App Server 提供的 reasoning summary。新 UI 会话只清空当前内存投影并解除 thread 指针，
   不删除 Codex 自身历史。
7. Quick Agent 和任务链严格隔离：端点没有 task ID、plan revision 或 run revision；conversation、
   tool output 和 terminal state 不写入 task plan/run journal，不调用 `WorkflowKernel.transition`，不产生
   verification report，也不宣称任务完成。需要追踪、恢复、验证或批准的工作必须回到任务编排闭环。
8. 发布并人工激活 SUID `focused-task-graph@4`。保留 v3 的全部 13 个 stable 单元；Agent 版面、额度、
   控制台、权限模式、流式旅程与四条安全/真实性要求保持 planned，等待实际使用与视觉复核。
9. 会话兼容修正和动态模型选单发布为 `focused-task-graph@5`。它保留 v4 的全部 stable 单元，新增
   provider model catalog、模型与性能选单及其真实性要求；真实只读会话是发布前验证的一部分。

## 后果

- 用户可在一个页面检查真实限额窗口并进行短程 Codex 工作，无需先制造任务记录。
- 这是进程内会话投影，不是 durable TaskManager run。API 重启后当前 UI transcript/pointer 不恢复；
  Codex 自己可能保留 thread 历史，但 JobSlayer 不把它冒充为工作流证据。
- `account/rateLimits/read` 反映 provider 当前公开的百分比窗口，不等价于美元、精确 token 数或承诺的
  剩余工作时长。多模型/额外 bucket 可能分别计量，UI 优先显示 `codex` bucket 并保留次级窗口。
- App Server 是随本机 Codex CLI 演化的本地协议边界。adapter 对未知通知宽容、对请求/非法响应失败
  关闭；升级 CLI 后必须运行 fake protocol 回归、真实只读 capacity/model probe 和最小会话 smoke。
- Fast 速度层采用 `model/list.serviceTiers[].id`，页面明确提示更快响应会增加用量；不存在可用速度层的
  模型只显示 Standard。effort 也严格按所选模型过滤，不假设所有版本具有相同能力。
- 快速执行可直接改变当前工作树，因此它适合明确的小修改，不具备任务 run 的隔离 worktree、持久
  retry、验证门禁或审批语义。

## 未采用方案

- 抓取 `codex` TUI 或 `/status` 屏幕文字：格式脆弱，难以可靠获得多窗口及时间戳。
- 每轮调用 `codex exec` 并自行拼接 transcript：可用于非交互执行，但会重复实现 App Server 已提供的
  thread、stream、interrupt 与 approval 协议。
- 根据 $200 月订阅、token 计数或历史速度推算“剩余可运行小时”：不同模型、工作类型和 provider
  窗口不可等价换算，显示精确数字会制造错误信心。
- 复用 `planner` 或 `executor` 角色：会混淆任务计划权限、durable run 权限与快捷仓库写入权限。
- 把 Quick Agent 消息追加到任务 audit chain：没有 task/revision binding，反而会污染工程真相。
