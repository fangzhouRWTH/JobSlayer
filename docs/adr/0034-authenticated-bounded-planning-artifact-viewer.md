# ADR-0034：认证、有界的规划制品查看器

- 状态：Accepted
- 日期：2026-08-19

## 背景

ADR-0033 已要求 Codex 规划调用保存精确 prompt、raw JSONL、stderr 和 final JSON 四类内容寻址
制品。成功提案只在 Workbench 显示 invocation ID 与制品数量，操作者仍需离开任务上下文并直接
查询本地 registry 才能复核模型输入、事件和输出；直接向浏览器暴露 `ArtifactManifest.uri` 或
任意 registry 内容又会泄漏宿主路径并扩大读取范围。

规划证据可能达到数 MiB，且包含不可信的模型/任务文本。查看功能必须保持只读、验证完整性、
限制响应体，同时不能让制品内容成为应用提案、验证结果或完成判定。

## 决定

1. 新增 provider-neutral `PlanningArtifactQuery` application service，只读取
   `ArtifactRegistry`，不注册、修改或删除制品。它只允许 ADR-0033 定义的四类
   `task_plan.agent.*` 证据。
2. 列表和内容读取均绑定 `plan_id`。查询先依赖 registry 完整读取/哈希验证，再投影公共描述；
   API 不返回 backing `uri`，只返回 artifact/invocation ID、类型、producer、大小、SHA-256、时间
   和有界 metadata。
3. 文本预览默认最多 1 MiB。完整对象仍先经过大小和 SHA-256 复核，响应明确标记 COMPLETE 或
   TRUNCATED；不提供未经验证的 range/raw file 路径，也不把内容解释为 HTML。
4. loopback API 新增 plan-scoped artifact list/preview GET。两者都要求未过期 planner principal
   和进程随机 `X-JobSlayer-Session`，复用 no-store、nosniff、DENY、无 CORS 与拒绝内容执行的
   CSP。未配置 query 时 capability 为 false，endpoint 返回不存在。
5. Task Orchestration 增加只读 Planning Artifact Viewer，显示四类证据、invocation、大小、哈希
   和纯文本内容。浏览器不接收存储 URI，不提供编辑/删除/下载或把证据转为 workflow command
   的操作。本轮复用 React/Lucide/CSS，不新增依赖。

## 后果

- 操作者可以在应用或拒绝候选图前复核真实 Codex 输入、原始事件、诊断和最终结构化输出，且
  篡改对象会失败关闭而不是被显示。
- 本能力是规划 registry 的受限只读切片，不是通用 run Artifact Review read model；顶级
  Artifact Review、远程对象存储、下载授权和 retention policy 仍需独立设计。
- provider 失败且没有生成计划 revision 时，制品仍保存在 registry，但当前 plan-scoped UI
  不负责发现未知/孤立 plan ID；后续可在有审计/保留策略后增加 operator-wide failure index。
- 规划证据不能改变 proposal、`TaskState`、WorkflowKernel、验证、审批或完成语义。
