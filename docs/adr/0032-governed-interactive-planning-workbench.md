# ADR-0032：受治理的交互式规划工作台完善

- 状态：Accepted
- 日期：2026-08-17

## 背景

ADR-0031 已证明“描述 → 讨论 → Agent 候选图 → 用户应用 → 定稿”的本地纵向切片，但首版
界面只自动打开最近计划，缺少计划切换、候选差异、通用语义边编辑、结构化验收信息、历史
比较和计划完整度反馈。直接接入真实模型会让这些交互与契约缺口被外部输出放大，也会增加
不可重复的调试成本。

这些增强仍然属于执行前规划，不应让 React Flow、浏览器或 Planning Agent 获得工作流状态、
权限、完成判定或执行能力。

## 决定

1. `TaskPlanNode` 增加 provider-neutral 的验收标准、交付物、约束、风险、验证要求和人工决策
   标记。字段使用带默认值的有界字符串序列，旧 revision 仍可读取。
2. 新增确定性的 `TaskPlanAssessment`。它对待处理提案、空图、归档状态、验证要求、孤立节点、
   人工门和结构化信息给出 blocker/warning/info；finalization 拒绝 blocker，但 warning/info 只作为
   规划质量提示。该评估不判断执行结果或 `TaskState`。
3. 通用 edge create/update/delete、提案 reject、历史 revision 派生和 archive/restore 都由
   `TaskOrchestrationService` 执行，要求最新 `expected_revision`，并追加新的哈希链记录。历史派生
   是创建新 draft，不是回滚或覆盖旧记录。
4. archive 是计划管理元数据。归档会追加一个只读 draft revision，并保留
   `latest_finalized_revision`；恢复也追加新 revision。归档计划不能讨论、编辑或定稿。
5. Workbench 增加计划搜索/切换、候选图差异、完整度问题、关系连线与 Inspector、revision 比较
   和派生入口。候选图仍按完整图应用或拒绝；本轮不引入逐项合并语义。
6. React Flow 拖动坐标只保存到浏览器本地 presentation metadata，按 plan 隔离；坐标不进入
   `TaskPlanSnapshot`、JSONL、Workflow IR 或领域契约。
7. 本轮复用现有 React Flow/React/Vite 栈，不新增基础设施或 UI 依赖。真实 Codex adapter、
   多人事务 store 和 plan-to-Workflow IR 编译继续作为独立阶段。

## 后果

- 本地用户可以管理多个计划，先看候选差异再应用或拒绝，并用结构化验收信息与确定性问题
  面板收敛到可定稿计划。
- 节点、边、归档、派生和定稿均可由 revision/hash 追溯；浏览器只能保存非权威布局。
- finalization 的规划门禁比 ADR-0031 首版更严格，但仍不代表任务已执行、验证、批准或完成。
- 逐项提案合并、协作冲突解决、远程部署、真实模型调用和 Workflow IR 创建不在本 ADR 范围。
