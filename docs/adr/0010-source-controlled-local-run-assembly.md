# ADR-0010：用版本化 runbook 装配可恢复的本地 Phase 0 运行

- 状态：Accepted
- 日期：2026-08-07

## 背景

JobSlayer 已有状态机、worktree、命令门禁、执行器协议、验证引擎、制品注册表和人工决定 UI，但只能由测试代码手工组装。BraveNewWorld 也已经有固定 BNW-0 基线和 `./bnw check`，仍缺少从仓库内版本化输入到真实隔离运行的公共路径。

## 决策

1. 用 `LocalTaskRunbook` 引用独立的测试床登记、`TaskSpec`、`ValidationProfile` 和固定哈希执行输入；`AgentInvocation` 保持提供方无关。
2. runbook 的所有文件引用必须位于 JobSlayer checkout 内，补丁内容必须命中登记 SHA-256；task repository、project、base commit、profile、run id 和 executor 必须交叉绑定。
3. 初步框架只自动授权 low-risk `scripted_patch` 运行。该 adapter 仅用 `git apply --check` 和 `git apply` 重放已固定补丁，不调用模型、不访问网络，也不宣称具有智能。
4. `scripted_patch` 与 Codex 一样实现 `AgentExecutor`，因此走相同 worktree、路径政策、日志哈希、制品、验证和状态转换路径；不建立测试专用控制器。
5. 每个 run 使用独立目录保存工作流 JSONL、内容寻址制品、executor raw logs 和另一条 append-only hash-chain run ledger。工作流状态仍只由 `WorkflowKernel` 和审计日志拥有；run ledger 只保存可恢复装配快照。
6. 实现审查必须显式声明 `agent` 或 `human`，不能根据 CLI 调用方式伪造身份类型。只有接受的实现审查才能生成真实 merge decision card。
7. `run-ui` 复用现有 loopback 决策界面并读取该 run 的真实卡片和审计日志。它仍只记录决定，不应用、不合并、不部署。
8. `apply-run-decision` 只接受外部提供的当前有效 `ApprovalAuthority`，调用确定性 `DecisionApplicationService`。JobSlayer 本地 CLI 不签发或伪造权限；应用决定也不等于 Git 合并。
9. `validate-runbook` 加入统一 `./jobslayer check`，防止 task/profile/patch/testbed 引用在开发中静默漂移。

## 后果

- 初步框架可以零模型费用在真实外部仓库的隔离 worktree 中运行并恢复到 `Reviewing`、`MergeReview` 或最终决定状态。
- 同一 run id、workspace id 和 run 目录均为不可覆盖资源；重跑必须创建新版本/标识，而不是删除证据。
- 当前本地 runner 只约束命令、环境、路径、超时和输出，不提供网络、系统调用、CPU 或内存强隔离。真实 Codex 前仍需要 OCI/VM 外层沙箱。
- 本地 JSONL 在单进程下可发现篡改，但状态、run ledger 和制品写入还不是跨文件事务；生产持久化需要数据库事务和恢复协议。
- `scripted_patch` 是框架验收工具，不计入 Agent 能力，也不能用来证明模型质量。
