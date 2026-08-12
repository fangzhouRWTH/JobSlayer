# ADR-0019：决定应用与工作区清理记录通过副作用证明恢复

- 状态：Accepted
- 日期：2026-08-12

## 背景

decision application 和 workspace cleanup 都先产生不可简单重复的事实，再追加
run ledger：前者先注册 decision/authority 制品并通过 kernel 改变状态；后者先让
Git 移除 worktree。进程可能在这些事实持久化后、对应 run record 发布前退出。

重跑决定应用会尝试第二次状态转换；看到路径不存在就声明清理成功，又无法证明
任务分支是否按约定保留。两者都需要从原始副作用和证据中恢复记录，而不是重复
业务命令或猜测结果。

## 决定

1. 本地决定应用注册的 `human-decision` 与 `approval-authority` manifest 必须携带
   task/run binding，其 artifact id 必须作为同一次 workflow transition 的证据。
2. `DecisionApplicationService.validate_applied_transition` 提供 provider-neutral
   只读验证：重新验证 card hash、decision evidence、authority actor/kind/有效窗口、
   option 到目标状态的映射、transition actor/from/to 和必需制品引用，但不调用
   `WorkflowKernel.transition`。
3. 当 ledger 停在 implementation review、journal 已包含合法决定转换时，恢复器
   只能读取转换引用的唯一 decision/authority 制品，验证全部先前制品和已应用转换，
   然后追加原 transition 的 decision-application record。不得再次应用决定，也不
   要求外部 authority 文件仍位于原路径。
4. 定义 provider-neutral `WorkspaceRemovalInspection`，同时表达 workspace path
   absence、Git worktree registration absence、保留分支及其 commit。Git adapter
   的 `inspect_removal` 是只读操作。
5. cleanup 正常路径在删除后必须证明 `safely_removed`，并把结构化 removal
   inspection 写入新 cleanup record。若路径已不存在但 ledger 缺记录，恢复器只有
   在分支仍精确指向已集成 commit 时才补写；不会再次调用 remove。
6. decision/authority 制品缺失或 producer/run/task 绑定改变、决定转换不匹配、
   workspace 被 symlink 替代、Git registration 未清除、保留分支丢失或漂移，均
   归类为 `invalid_evidence` 并拒绝自动恢复。

## 理由

- transition 引用内容寻址制品后，授权事实不依赖易失的外部输入路径，且能够证明
  ledger 补写使用的是原决定和原 authority。
- 把“验证已发生的转换”与“执行新转换”分开，可复用同一领域规则而不让恢复器
  获得工作流所有权。
- cleanup 的安全后置条件不只是路径消失；保留 source branch/commit 才能让源码
  结果继续可追溯。

## 后果

- decision application 和 cleanup 的 journal/Git 已提交、ledger 未提交窗口可在
  真实进程退出后幂等恢复。
- 新 cleanup record 包含 `removal_inspection`；读取器继续接受此前没有该字段的旧
  Phase 0 cleanup record，避免破坏已有语料。
- 旧决定转换若未引用 decision/authority artifact，不会被新恢复器猜测补齐，仍需
  人工处理。
- execution 首记录窗口仍未闭合；它需要在 Agent 副作用前建立可重构 intent，或
  持久化足以重建 outcome/context 的提交协议。

## 未选择的方案

- 恢复时再次调用 decision application：拒绝，状态转换不是可重复副作用。
- 从当前 `decision.json` 和外部 authority 路径重新注册制品：拒绝，文件可能改变或
  已消失，且会产生新的 artifact identity。
- worktree 路径不存在即补写 cleanup：拒绝，无法证明 Git registration 和 source
  branch 保留承诺。
