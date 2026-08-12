# 受治理执行闭环

## 当前能力

`TaskExecutionController` 保留 Phase 0 JSONL 兼容路径；Phase 1 的 `TransactionalExecutionCoordinator` 和 `GovernedAgentExecutor` 已把事务真相、签名身份、短期凭据、强隔离、worker lease、上下文和预算组合为提供方无关基础架构。它仍不会自动操作远端 Git。

```mermaid
sequenceDiagram
    participant U as Human/Policy
    participant C as Transactional Coordinator
    participant P as Governance Ports
    participant K as WorkflowKernel
    participant A as AgentExecutor
    participant V as VerificationEngine
    participant R as Reviewer
    participant G as LocalGitIntegrator
    U->>C: TaskSpec + Authorization
    C->>C: transaction: intent + artifact + outbox
    C->>P: verify identity/context/grant/sandbox; reserve budget/lease
    C->>K: buffer Draft -> Planned -> Implementing
    C->>A: isolated workspace invocation
    A-->>P: normalized events + usage
    P-->>C: terminal result + governed evidence
    C->>C: enforce path policy and register patch
    C->>K: Implementing -> Verifying
    C->>V: trusted ValidationProfile
    alt required checks pass
        C->>K: buffer Verifying -> Reviewing
        C->>C: transaction: exact transitions + outcome + artifacts + outbox
        R->>C: ReviewReport
        C->>K: Reviewing -> MergeReview
        C-->>U: evidence-bound DecisionCard
        U->>K: approved decision -> Integrating
        C->>G: reviewed patch + fixed base + target ref
        G-->>C: fast-forward + SourceIntegrationResult
        C->>K: Integrating -> Completed
    else check fails/errors
        C->>K: Verifying -> Repairing
    end
```

## 输入契约

- `TaskSpec`：固定仓库基线、允许/禁止路径、验收条件、验证配置 ID、风险和费用上限。
- `AgentInvocation`：固定执行器、模型配置名、上下文包、工作区、权限、时限、token/context/attempt/repair 上限和输出 schema。
- `TaskExecutionAuthorization`：签名绑定 task/run、认证主体、最大风险和有效窗口。
- `ValidationProfile`：绑定可信命令政策及一项或多项 required/optional 检查。

Phase 0 控制器继续只接受 `Draft` 状态和 `max_attempts=1`。Phase 1 协调器在长时间 Agent 之前单独提交 intent，期间只让 `WorkflowKernel` 写入可验证转换缓冲，终态后将转换、run record、artifact metadata 与 outbox 原子提交；不让数据库事务跨越 Agent/Git。授权、治理绑定或证据不一致时，会在相应副作用前拒绝请求。

## 输出与人工监督

成功验证返回 `TaskExecutionOutcome(status=awaiting_review)`，同时保留工作区供 diff 和复核。实现审查需要提交绑定 task 与 patch SHA-256 的 `ReviewReport`：

- `changes_requested`：记录审查证据并进入 `Repairing`；
- `accepted`：还必须引用本次 `VerificationReport.report_id`，然后进入 `MergeReview` 并产生决策卡。

决策卡的 `approve` 只授权进入 `Integrating`，不会由 UI 自动执行 Git。人工工具仍使用：

```bash
./jobslayer review-decision card.json \
  --identity-session .jobslayer/identity/approver.json \
  --identity-key .jobslayer/identity/key.json \
  --output decision.json
```

`DecisionApplicationService` 随后复核卡片哈希、原始证据列表、当前状态和授权窗口。任一项不匹配都不会改变工作流。

随后由操作员显式调用 `integrate-run`。本地 adapter 重新核对 reviewed patch、由该 patch 产生的 Git tree、固定 base、干净目标 checkout 和登记默认分支，只创建一个提交并执行 fast-forward；结果登记为制品后，kernel 才允许 `Completed`。`cleanup-run` 只移除已完成且干净的 worktree，保留分支。

`LocalRunCoordinator` 支持确定性 `scripted_patch` 和签名 execution authority 的 `codex_cli`，以双 append-only hash chain 保存 Phase 0 快照。`TransactionalExecutionCoordinator` 则以 SQLite/PostgreSQL 为唯一元数据真相，并支持 execution/review/signed decision/integration/cleanup 的阶段事务。两条路径都不取代 `WorkflowKernel` 的状态真相，也不互相异步镜像。完整入口见 [MINIMUM_DEVELOPMENT_LOOP.md](MINIMUM_DEVELOPMENT_LOOP.md)。

## 制品与失败语义

本地注册表保存以下证据：任务、执行授权、验证配置、Agent 终态、raw JSONL、stderr、补丁、每项命令结果、验证报告、审查报告、决策卡、人工决定/authority、源码集成结果和控制器失败记录。读取制品时复核清单与内容哈希。

| 条件 | 结果状态 | 是否产生证据 |
|---|---|---|
| Agent 失败/取消/超时 | `Failed` | 是，含日志和失败记录 |
| 日志哈希不一致 | `Failed` | 是，保留实际字节和不一致记录 |
| 空补丁或路径政策拒绝 | `Failed` | 是 |
| required 验证失败/错误 | `Repairing` | 是，含命令结果或拒绝原因 |
| 验证通过 | `Reviewing` | 是，不自动批准 |
| 审查要求修改 | `Repairing` | 是 |
| 审查接受 | `MergeReview` | 是，等待授权人工决定 |
| 有效人工批准 | `Integrating` | 是，尚未宣称完成 |
| 本地 fast-forward 成功且证据匹配 | `Completed` | 是，含集成结果与提交 |

## BraveNewWorld 接线状态与下一步

1. [完成] BNW-0 本地基线 commit/tag 与只读 inspection；
2. [完成] `./bnw check`、场景检查和精确命令政策组成的 `ValidationProfile`；
3. [完成] 低风险、路径范围窄的 scripted replay 真实 worktree 运行到 `MergeReview`，用于证明框架接线；
4. [完成] 显式人工授权一次真实 Codex，在 BNW-0 worktree 实现滤波主题并经独立 Agent 复核到达 `MergeReview`；
5. [完成] 用临时仓库验证人工批准、内容复核、本地 commit/fast-forward、完成门禁和 worktree 清理；
6. [完成] 真实人工决定体验、签名身份/authority 及越权拒绝测试；仍不自动 push 或部署；
7. [完成] 短期凭据端口、执行前/运行中预算、上下文包、worker lease 和默认无网络 Linux 强隔离；
8. [待部署能力] 接入生产 OIDC/secret broker、远程 worker 和第二个真实模型 executor；这些需要独立部署配置和外部调用授权。
