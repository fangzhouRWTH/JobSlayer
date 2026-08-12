# JobSlayer 人工监督入口

## 目的

人工监督不是在 Agent 对话末尾回答一次“可以”。JobSlayer 使用结构化 `DecisionCard` 告诉评审者：需要决定什么、为什么是现在、有哪些证据、风险和可逆性如何、每个选项会导致什么后果。

当前提供两个消费同一契约的本地控制面：适合终端/SSH 的 CLI，以及只监听本机的极简可视化审查页。二者都要求签名 session，只产生同一 `HumanDecision`，不拥有工作流权限。

## 可视化入口

```bash
./jobslayer ui examples/decision-card.example.json \
  --identity-session .jobslayer/identity/approver.json \
  --identity-key .jobslayer/identity/key.json \
  --output .jobslayer/decisions/task-example-001.json \
  --journal .jobslayer/audit.jsonl \
  --open-browser
```

界面展示决策摘要、风险、证据哈希、选项后果、当前 task 的审计时间线、受影响制品以及真实能力边界。没有 journal 时状态明确显示为未知；有 journal 且卡片状态不匹配时拒绝提交。详细接口和模块说明见 [VISUAL_REVIEW_UI.md](VISUAL_REVIEW_UI.md)。

## 交互使用

```bash
./jobslayer review-decision examples/decision-card.example.json \
  --identity-session .jobslayer/identity/approver.json \
  --identity-key .jobslayer/identity/key.json \
  --output .jobslayer/decisions/task-example-001.json
```

CLI 会：

1. 严格校验决策卡 schema；
2. 展示任务、风险、可逆性、证据和受影响制品；
3. 标出控制器提供的推荐/默认项；
4. 展示每个选项的描述和后果；
5. 要求人工选择并填写理由；
6. 生成包含卡片 SHA-256、已认证人工身份、选择、理由和证据 ID 的 JSON；
7. 拒绝覆盖已经存在的决策记录。

输入 `q` 会取消，不产生决定。直接回车选择默认项，但仍必须填写理由。

## 非交互形式

测试、脚本或外部 UI adapter 可以显式提供选择和理由：

```bash
./jobslayer review-decision card.json \
  --identity-session .jobslayer/identity/approver.json \
  --identity-key .jobslayer/identity/key.json \
  --select request_changes \
  --rationale "控制器饱和场景缺少验证证据" \
  --output decision.json
```

非交互参数不会绕过身份门禁。session 必须有效且具备 `record_decision` 权限；选择和理由仍会绑定已认证主体及原卡片哈希。

## 决定不等于状态转换

`review-decision` 只产生 `HumanDecision`，不会直接：

- 把任务标记为完成或取消；
- 修改验收标准；
- 合并、推送或部署代码；
- 扩大 Agent 权限；
- 覆盖任何验证记录。

`DecisionApplicationService` 负责重新读取原 `DecisionCard`，核对卡片哈希、任务、选项、证据、签名 authority 和当前工作流状态，再调用 `WorkflowKernel.transition`。`TaskExecutionController` 已能在补丁验证通过且实现审查接受后生成这张卡。`issue-approval-authority` 可由当前有效的本地 `approver` session 签发短期 authority；`apply-run-decision` 仍会独立验证 session、proof 和 decision 绑定。若卡片生成后任务或证据已经变化，服务会拒绝旧决定。批准只把任务置为 `Integrating`。随后必须由有权限的操作员显式运行 `integrate-run`；只有本地目标成功快进并登记 `SourceIntegrationResult` 后，kernel 才允许 `Completed`。UI、决定应用和源码集成因此仍是三个独立权限边界，且都不会 push 或部署。

## 决策卡生成要求

决策卡必须由控制器根据领域状态和已登记制品生成，不能让 Agent 自行声称证据已通过。至少包含一项证据和两个选项；只能有一个推荐项，且它必须是默认项。

证据摘要不得包含密钥、完整敏感日志或不必要的源代码。大 diff、图片和日志应使用受控制品 URI，并在 UI 层按权限加载。

## 管理界面现状与远程平台引入条件

本地认证、事务查询和多运行只读 Dashboard 已完成。以下条件满足后才建设远程、多租户监督平台：

- OIDC/mTLS、撤销和团队/租户授权边界已稳定；
- 远程制品 URI、权限、脱敏和保留策略可用；
- outbox dispatcher 与断线恢复/重放语义完成；
- 生产 secret broker 不向 Agent 暴露长期凭据；
- 至少一个 BraveNewWorld 真实任务闭环证明 CLI 信息模型足够。

届时优先实现项目概览、工作流图、审批收件箱、diff/证据查看和运行控制台，而不是复制聊天界面。
