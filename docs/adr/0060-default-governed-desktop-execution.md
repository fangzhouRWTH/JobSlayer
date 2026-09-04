# ADR-0060：普通桌面入口连接受治理执行闭环

- 状态：Accepted
- 日期：2026-09-04
- 取代：ADR-0059 决定 5 中“默认桌面入口不连接三类执行 adapter”的阶段性限制
- 依据：[ADR-0039](0039-durable-task-manager-codex-worker.md)、
  [ADR-0042](0042-independent-source-review-and-isolated-run-checkpoint.md)、
  [ADR-0043](0043-source-bound-deterministic-validation-nodes.md)、
  [ADR-0059](0059-actionable-finalization-and-profile-owned-validation.md)

## 背景

普通 `start.py` 已能讨论、固化任务并装配 Run，但它刻意没有连接 executor、validator 或 source
integrator。用户把新 3D Life Game 任务装配为 Run 后，执行页因此只能显示“没有绑定执行能力”。这不是
任务状态或 target 错误，而是推荐入口与产品承诺不一致。重启时即使连接 coordinator，尚未产生持久
cursor 的合法初始状态也会被 UI 误写成 `NOT CONNECTED`，进一步让可恢复 Run 看起来卡死。

## 决定

1. 统一桌面临时身份增加 `executor` role；仍使用受保护本机 key、24 小时 session 和显式 RBAC。
2. `start.py` 的 backend 参数默认显式启用 durable Codex executor、source-controlled local validation
   和隔离 run-branch checkpoint integrator。三者复用既有 adapter protocol、内容绑定 target、持久
   start key、原始 artifact、命令 policy 和 append-only Run，不建立桌面专用捷径。
3. 能力连接不等于开始执行。用户仍需在执行页点击“推进一步”；每个 tick 最多提交一个持久 intent 并
   调用一个既有 application command。Agent 不拥有 workflow、retry、validation、approval 或 completion。
4. 默认身份虽然具备 reviewer 与 approver role，同一 `subject_id` 仍不能审查并批准同一源码 patch，
   也不能批准自己直接依赖的最终完成门。独立 actor 规则不因便捷入口而放宽。
5. 执行页根据认证 session 的 `serial_coordinator` capability 区分两种状态：能力未连接显示
   `NOT CONNECTED` 和精确重启步骤；能力已连接但 cursor 尚未创建显示 `READY`，说明首次单步点击会
   从 Run 真相初始化 cursor 并执行唯一下一动作。
6. 发布并激活 SUID `focused-task-graph@11`，保留 v10 的全部 stable 单元。

## 后果

- 已装配的现有 Run 在普通入口重启后可直接继续，不需要重建、重新固化或手工拼接高级 CLI 参数。
- 执行、构建验证和 checkpoint 集成能力真实存在，但不会自动消费订阅额度或修改目标项目。
- 源码节点到达 review 后仍会停下等待明确审查；同一桌面主体完成 review 后不能再冒充独立 approver。
- `start.py --smoke-test` 现在验证完整闭环 adapter 能成功装配，但不会触发 Run tick 或 Codex 任务。

## 未采用方案

- 让 UI 在缺少 adapter 时继续发送无效 tick：只会把配置错误变成运行错误。
- 启动后自动推进首节点：会在没有当次用户动作时产生模型调用和工作区副作用。
- 移除 reviewer/approver 独立性：会削弱已有源码检查点和最终完成门。
- 删除持久 coordinator、直接从按钮调用 Codex：会形成第二条不可恢复且不可审计的执行路径。
