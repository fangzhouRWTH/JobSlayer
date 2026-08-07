# ADR-0006：证据驱动的应用控制器与本地制品注册表

- 状态：Accepted
- 日期：2026-08-07

## 背景

工作区、命令 runner、Agent adapter、验证报告和人工决定此前均可单独工作，但调用者仍需手工拼接步骤。原始日志只有路径与哈希，验证配置也没有版本化契约；如果由外部 Agent 自行决定何时验证或进入评审，JobSlayer 就没有真正拥有工程真相。

## 决定

新增提供方无关的 `ArtifactRegistry`、`ValidationProfile` 和 `TaskExecutionController`。控制器按照固定顺序协调一次 Phase 0 实现尝试：

```text
TaskSpec + execution authorization
  -> isolated workspace -> AgentRun -> scoped patch
  -> trusted validation profile -> implementation review
  -> merge decision card
```

所有工作流状态仍只通过 `WorkflowKernel.transition` 改变。控制器不得自动提交、推送、合并、部署或把任务标记为完成；它在 `MergeReview` 停止，最终决定仍由 `DecisionApplicationService` 核验授权后应用。

本地 `ArtifactRegistry` 使用 SHA-256 内容寻址保存对象，以独立清单记录 task/run/type/producer/URI/大小/哈希。对象不覆盖写入，读取时重新核对 URI、大小和哈希。相同字节可复用对象，但每次登记生成独立证据 ID。

`ValidationProfile` 把检查 argv、cwd、超时、required 标记与可信 `CommandPolicy` 绑定。验证引擎只调用 `CommandRunner`；通过、失败、超时和执行前拒绝都形成结构化检查证据。验证报告同时绑定固定基线 commit 和补丁 SHA-256。

进入实现需要任务绑定、风险上限和有效期均通过的 human/policy `TaskExecutionAuthorization`。成功验证只进入 `Reviewing`；实现审查接受后才生成包含补丁、验证和审查哈希的合并决策卡。审查要求修改则进入 `Repairing`。

## 后果

- Phase 0 已有可在假执行器和真实本地验证命令上运行的完整应用闭环；
- 原始 Agent 日志、任务、授权、验证配置、运行结果、补丁、检查结果、审查和决策卡均可登记为制品；
- Agent 失败、空补丁、越界补丁、日志哈希不一致和验证失败不能进入合并评审；
- 当前控制器只支持从 `Draft` 开始的一次尝试，不实现自动修复重试或进程重启恢复；
- 本地对象权限和哈希校验可以发现常见篡改，但不能抵抗拥有完整文件系统权限的攻击者；
- 制品清单尚无事务数据库、访问控制、保留策略或垃圾回收。

## 替代方案

- 由 CLI 脚本直接串接组件：拒绝，步骤遗漏和状态所有权难以测试。
- 仅在 `VerificationReport` 内嵌 stdout/stderr：拒绝，大日志会污染领域记录且不利于独立保留。
- 验证失败时抛异常并丢弃结果：拒绝，失败本身是工程证据。
- 当前阶段引入 S3/PostgreSQL/Temporal：暂缓，本地确定性闭环尚未达到引入基础设施的退出条件。
- 控制器自动批准低风险补丁：拒绝，现阶段必须保留明确人工合并决定。

## 退出策略

对象存储、数据库注册表、容器 runner 或其他 Agent 均可实现现有协议。未来持久工作流可逐步替换同步控制器，但必须保留相同的任务绑定、证据引用、验证门禁和人工完成规则。
