# ADR-0047：将当前 UI 收束为单屏任务图预览

- 状态：Accepted
- 日期：2026-09-01
- 调整：ADR-0036/ADR-0045 中 TaskManager 产品面的当前 UI 范围

## 背景

TaskManager 已逐步把任务切换、DAG、Backlog、总 Log、target、执行、验证、review、approval 和
Agent 对话放入同一页面。虽然这些控件映射真实后台能力，但在串行 coordinator 尚未完成时同时暴露
全部治理步骤，会掩盖当前最需要验证的交互：用户是否能看懂任务图、选择节点并通过对话调整计划。

## 决定

1. 当前 Web App 只有一个可达产品页面，入口固定为 `http://127.0.0.1:4173/`。移除全局
   workbench header/sidebar、命令面板和 legacy hash routes；旧实验组件源码暂不删除，但不从当前
   `App` 装配。
2. 主工作区使用固定双栏：左侧 `2fr` 显示不可直接拖改的 React Flow DAG，右侧 `1fr`；窄屏时
   两栏纵向排列。
3. 右侧不再使用 tab。上半区始终显示所选节点的状态、描述、依赖、验收标准、验证要求和最新反馈；
   下半区始终显示完整 Agent 对话与绑定当前节点的输入框。
4. 当前 UI 只保留任务切换、新建、刷新、候选图应用/拒绝和 Agent 讨论。Backlog、总 Log、target
   选择、finalize、run assembly、执行、验证、review、approval 与 integration 控件不在本轮 UI
   暴露；相应 JobSlayer application/API 能力和权限边界不删除、不旁路。
5. Vite 增加显式 `task-manager` script。标准启动顺序为先运行认证的 `task-manager-api`，再运行
   `sh ./init.sh -- npm --prefix ui-framework run task-manager`。

## 后果

- 产品视觉焦点缩小为“图 → 节点 → 对话”，可以先验证任务规划闭环而不被治理控件干扰。
- 用户暂时不能从当前页面完成固化和执行闭环；在串行 coordinator 与新的渐进披露交互确定前，
  这些能力仍只能通过受认证 API/既有 application command 使用。
- 恢复执行控件时必须围绕当前节点渐进披露，不能重新引入多页面 workbench 或常驻治理按钮矩阵。
