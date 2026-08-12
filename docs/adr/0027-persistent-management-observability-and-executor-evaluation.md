# ADR-0027：持久事件驱动管理面、OpenTelemetry 端口与同契约执行器评测

- 状态：Accepted
- 日期：2026-08-12

## 背景

单 run 审查 UI 不能回答跨任务状态、成本、审批和异常问题。管理面若重新推断状态或直接
写数据库，会产生第二控制平面；执行器比较若任务/验证契约漂移，也不能形成有效证据。

## 决定

1. Dashboard 保持 loopback、认证、read-only。默认可读旧 Phase 0 完整性语料；传入现有
   SQLite control-plane DB 与 artifact root 时，只从事务 history/run/artifact/outbox 真相
   构建快照，intent-only 运行也可见。
2. 查询 API 返回任务状态、run stage、workflow transitions、完整 run records、持久事件、
   制品 metadata、usage/cost、review 与 decision 状态。制品字节必须再次通过 registry
   校验；无效 run 不进入正常聚合。
3. 可选 `TelemetrySink` 默认 no-op；OpenTelemetry adapter 仅使用官方 API 创建 span，
   事务执行和管理查询记录稳定的标量属性，不把原始 prompt、凭据或日志写入 telemetry。
4. executor comparison 先校验 task 与 validation contract hash 完全一致，再比较状态、
   usage/cost、耗时和人工干预。当前确定性回归使用 scripted adapter 与 fake Codex CLI，
   不以测试替身冒充付费模型质量评测。

## 后果

- 管理页面没有写 API；任何后续审批动作仍必须经过身份服务、应用服务与 Kernel。
- 项目无需前端框架即可获得可验证的 Agent 开发管理视图；远程、多租户和生产部署后置。
- 真实第二模型/付费评测需要独立预算和用户授权，本阶段不会自动发起外部调用。

