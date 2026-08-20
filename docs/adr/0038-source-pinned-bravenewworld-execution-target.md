# ADR-0038：TaskManager 以源包哈希固定 BraveNewWorld 执行目标

- 状态：Accepted
- 日期：2026-08-19

## 背景

ADR-0037 已能把固化 DAG 装配为受治理 run，但刻意不从通用节点文字推断 repository、base commit、
写路径、验证命令或预算。现有悬架候选图也暴露了这种推断的风险：它把 JobSlayer 的
`./jobslayer check` 和领域约束写入了本应面向 BraveNewWorld 的工作内容。

只保存一个逻辑 target ID 仍不足够。如果 runbook、任务规格或验证 profile 在计划固化后、run
装配前发生变化，同一个 ID 会解析成不同执行输入。因此需要把可审查目标和精确源版本同时放进
计划与运行真相。

## 决定

1. TaskManager 只列出 host 显式注册的 execution target，不扫描目录或根据任务文本自动选择项目。
   首个生产目标 `brave-new-world-suspension-v1` 指向源码控制的 Codex runbook。
2. target resolver 同时加载并验证 runbook、`TestbedSpec`、`TaskSpec`、`ValidationProfile` 和
   `AgentInvocation`，记录四个源文件的 SHA-256，再由规范 JSON 计算 `source_bundle_sha256`。
3. resolver 必须只读检查本地 Git root、HEAD、`bnw-0` tag、origin 和工作树。基线不干净、HEAD/tag
   不匹配或 origin 未登记时，目标不能通过预检。
4. 计划 revision 同时保存 `execution_target_id` 和 `execution_target_source_sha256`。正常选择一次
   写入两者；对已经存在的旧选择只允许追加一次 source pin，不能静默重写历史。
5. 固化和 run 装配前都执行确定性 target assessment。首个 BNW compiler gate 阻止
   `./jobslayer`、`jobslayer.domain`、`WorkflowKernel` 等跨项目节点指令，并要求图中逐字包含 profile
   的两个必需命令：悬架场景命令和 `./bnw check`。
6. run snapshot 和每个 executor request 嵌入完整 `TaskManagerExecutionBinding`；run journal 把该
   binding 视为不可变字段。后续 source drift 不能改变已经装配的运行。
7. BNW 悬架 runbook 显式使用本机登录的 Codex、`gpt-5.6-sol`、`xhigh`、单次三小时、一次 attempt、
   300k input、50k output 和 4 MiB context。`max_cost_usd=10` 是 JobSlayer 任务预算元数据，不声称
   能读取或精确扣减 ChatGPT 月订阅余额。
8. API/UI 暴露目标、基线、允许/禁止路径、验证命令、模型 profile、时间和源包 hash；目标未选择、
   源包漂移或图预检失败时禁用固化/装配并显示具体 blocker。
9. 本决定不启用真实 TaskManager executor。Codex planning 可以只读生成 pending proposal；用户
   仍需 apply 和 finalize，之后还要实现可恢复执行 adapter、verifier/reviewer 和 human-gate 路径。

## 后果

- 当前悬架任务图不能再把 JobSlayer 自身命令带入 BraveNewWorld 执行；错配会在用户固化前暴露。
- target 源文件可以继续演进，但每次变更都会产生新 bundle hash，旧计划不会无声消费新配置。
- run 保存的绑定较大，但换来完整的 repository/base/path/profile/invocation 审计证据，且不向领域层
  暴露 Codex SDK 对象。
- 目前的 gate 是首个显式 BNW target compiler，不是通用自然语言安全证明。新增目标必须提供自己的
  确定性绑定和预检规则，不能复用关键词检查冒充完整权限系统。
