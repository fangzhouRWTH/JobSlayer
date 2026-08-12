# ADR-0017：补写源码集成记录前必须只读证明 Git 与完成证据

- 状态：Accepted
- 日期：2026-08-12

## 背景

本地源码集成跨越三个不可原子提交的事实域：Git worktree/目标分支、workflow
journal 中的 `Integrating -> Completed` 转换，以及 run ledger 中的
`source_integration` 记录。进程可能在 Git 已快进、集成制品已落盘且 kernel 已
追加 `Completed` 后退出，使 ledger 仍停在 `decision_application`。

此时重新执行完整集成动作虽然现有 Git adapter 通常可幂等返回，但恢复器仍需
区分“外部事实已完成、只缺内部记录”和“journal 声称完成但 Git/制品已经漂移”。
仅根据 workflow 状态补账会让 JobSlayer 接受未经当前证据证明的完成事实；再次
创建集成制品还会生成新的随机 artifact id，使补写记录与原 `Completed` 转换
引用的制品不一致。

## 决定

1. `LocalGitIntegrator` 提供只读 `verify_existing_integration` 证明：目标 checkout
   位于预期分支且干净；source worktree 是固定 base 之上的唯一提交；提交消息、
   changed paths 和 Git tree 与经审查 patch 完全一致；目标 HEAD 等于该提交；
   持久化 `SourceIntegrationResult` 的全部稳定字段与这些 Git 事实一致。
2. 已处于 `Completed` 的重试不得调用 mutating `integrate`，不得创建新 commit、
   fast-forward、工作流转换或新的集成制品。协调器必须从完成转换引用的唯一
   `source-integration-result` 制品恢复原始 result 和 `integrated_at`。
3. 自动补写 `source_integration` run record 只在以下条件全部满足时开放：前三条
   ledger 记录及其哈希链有效；journal 哈希链有效且倒数第二条转换与决定记录
   完全一致；完成转换由同一批准人执行并引用 decision、authority、verification、
   integration result 和 integration artifact；所有先前制品及集成制品通过内容
   校验；只读 Git 证明通过。
4. 恢复只追加缺失的第四条 run record。重复恢复健康 run 是只读幂等操作；目标
   分支漂移、工作区漂移、制品缺失/改写或转换绑定不一致均标记为
   `invalid_evidence` 并拒绝自动补账。
5. 该能力不改变 provider-neutral `RunRecoveryManager` 合约，不新增数据库或外部
   基础设施，也不改变任何 workflow 转换规则。

## 理由

- Git 是源码是否实际集成的外部事实源；只读证明把恢复与当前 commit/tree 绑定，
  避免依赖自然语言理由或单一状态枚举。
- 复用完成转换已引用的内容寻址制品，可保持 ledger、journal 和 artifact id 的
  原始因果关系，不制造第二份“同一集成”证据。
- 把重试路径拆成 mutating 首次集成与 read-only completed resume，可用测试明确
  证明恢复不会重复执行 Git 副作用。

## 后果

- “Git/Completed 已提交但 source-integration ledger 未追加”的崩溃窗口可以自动、
  幂等恢复。
- 如果目标分支在崩溃后继续前进，即使原集成提交仍是祖先，也不会自动补账；当前
  本地单任务模型要求目标 HEAD 与已批准提交精确相等，漂移需人工调查。
- run ledger 自身发生 partial append、以及 execution、decision application、cleanup
  的其他提交窗口仍未闭合；这些继续属于 ST-01 后续切片。

## 未选择的方案

- 看到 `Completed` 就补写 ledger：拒绝，缺少 Git 和制品事实证明。
- 再次调用完整 Git 集成后补写：拒绝，恢复不应依赖重复副作用，并可能制造新制品
  身份。
- 发现目标分支包含该提交即接受后续提交：暂不选择；这会扩大单任务本地集成的
  并发和顺序语义，应在事务存储与多控制器模型明确后另行决策。
