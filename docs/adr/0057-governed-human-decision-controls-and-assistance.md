# ADR-0057：在执行页提供受治理人工决定、追加式反馈与只读 Agent 辅助

- 状态：Accepted
- 日期：2026-09-03
- 依据：[ADR-0041](0041-verified-artifact-deliverable-acceptance.md)、
  [ADR-0042](0042-independent-source-review-and-isolated-run-checkpoint.md)、
  [ADR-0044](0044-evidence-bound-final-completion-gate.md)、
  [ADR-0054](0054-task-independent-codex-quick-agent.md)、
  [ADR-0056](0056-revision-bound-human-action-guidance.md)

## 背景

revision-bound 指导已经能说明人工停顿需要谁处理、检查哪些证据和执行什么命令，但执行页没有对应
输入控件。操作者只能转向 CLI/API，核对后的理由、暂不批准意见和 Agent 辅助讨论也没有在同一节点
语境中连续显示。尤其在小时级任务中，这会让交接信息散落，并使“已经阅读指导”与“已经作出正式
决定”之间缺少清楚边界。

按钮不能把指导升级成授权；自然语言反馈或 Agent 回答也不能成为隐式批准。

## 决定

1. 执行页的完整人工指导卡新增决定选择、逐项证据核对、理由输入和动作边界确认。只有当前 session
   具备对应 capability，且证据和边界均被明确确认时，正式按钮才可提交。
2. 正式按钮不新增状态写路径。它按 guidance command 映射到现有 `confirm-scope`、
   `accept-review`、`review-source`、`approve-checkpoint` 或 `approve-completion` application API；
   服务端继续执行 RBAC、精确 revision、证据、Reviewer/Approver 独立性与
   `WorkflowKernel.transition` 检查。客户端 checkbox 只是防误操作界面，不是后端证据。
3. 没有正式转换 command 的决定使用独立 feedback API。反馈绑定 task、run、node、guidance、
   decision 和 plan/run revision，先登记 immutable artifact，再追加到 run hash chain；它不改变节点
   状态或 Kernel transition history。旧 guidance、错误 run 或未提供的 decision 均失败关闭。
4. 同一卡片提供任务绑定 Agent 辅助。请求先作为 human interaction 和 artifact 追加，再调用
   provider-neutral adapter；回答或错误随后作为 Agent interaction 和 artifact 追加。Codex adapter
   使用一次性、`read-only`、有时限、结构化输出的本机登录会话，并保存 prompt、JSONL、stderr 与
   final output。
5. Agent 提示和输出 schema 只允许解释要求、风险或起草反馈。Agent 不得批准、拒绝、重试、执行、
   写仓库，不能声称仅凭 artifact ID 已阅读证据，也不能返回 `decision_id`。正式决定始终由授权人类
   另行点击结构化控件。
6. assistance 使用独立 `ASSIST_HUMAN_DECISION` action，由 `quick-agent` role 授权；feedback 使用
   `RECORD_DECISION`。默认桌面临时身份包含 planner、quick-agent、reviewer 和 approver，便于本机
   显示对应入口；源码 Reviewer/Approver 同主体禁止仍由后端执行，checkpoint integration adapter
   默认仍未启用。
7. 发布并激活 SUID `focused-task-graph@8`。新增正式决定、反馈、Agent region/relations/journeys 和
   三项约束，同时逐字保留 v7 全部 stable 单元。

## 后果

- 用户可以在执行页完成核对、正式确认或明确要求修改，并立即看到新的 run revision 和审计反馈。
- 反馈与辅助对话是可追踪的任务事实，但不污染任务 DAG，不建立第二状态机，也不成为验证或完成证据。
- Agent 调用会消耗本机 Codex 容量；只有用户显式询问时才发生。失败被记录并显示，节点继续等待。
- 页面目前不直接打开 artifact 内容；证据核对框要求操作者通过已有制品入口或外部路径完成真实阅读。
- plan-level proposal/finalize/run assembly 仍使用既有编排控件；本次正式确认区只放在已有 run 的执行页。

## 未采用方案

- 让 Agent 根据对话自动选择或提交正式决定：破坏人类授权和完成门禁。
- 把自然语言中的“同意”解析为 Kernel transition：不可审计且容易误判。
- 只把对话写入浏览器状态：刷新即丢失，也无法与 task/run/revision 对账。
- 为每种人工状态复制一套新的后端状态机：会与现有 application command 和 Kernel 产生双重真相。
