# 最小开发闭环与体验测试手册

## 结论

JobSlayer 当前已经支持本地单任务成功路径的全部基础操作：版本化任务绑定、独立 worktree、scripted 或显式授权 Codex 执行、路径门禁、确定性验证、独立实现审查、可视化人工决定、外部 authority 应用、受控本地 commit/fast-forward、完成证据和安全 worktree 清理。

```text
Draft → Planned → Implementing → Verifying → Reviewing → MergeReview
  → Integrating → Completed → workspace cleanup
```

失败、拒绝和要求修改会留下证据并停在 `Failed`、`Cancelled` 或 `Repairing`。当前还没有自动发起下一轮 repair；因此“已闭合”指单任务本地成功路径，不包含自动重试、远端 push/PR 或部署。

## 统一入口

所有操作都通过根脚本：

```bash
./jobslayer check
./jobslayer inspect-run RUN_DIR
./jobslayer inspect-recovery RUN_DIR
./jobslayer review-run RUN_DIR --actor-type agent --actor-id REVIEWER \
  --status accepted --summary "实现与验证证据一致"
./jobslayer run-ui RUN_DIR --actor-id HUMAN --open-browser
./jobslayer apply-run-decision RUN_DIR --authority AUTHORITY.json
./jobslayer integrate-run RUN_DIR
./jobslayer cleanup-run RUN_DIR
```

若 `inspect-recovery` 报告 `recoverable` 且动作为
`restore_decision_card`，可执行 `./jobslayer recover-run RUN_DIR`。当前恢复器
只重建由权威 review ledger 可证明的缺失决策卡投影；其他状态会拒绝并要求人工
处理，不会覆盖文件或重复执行 Agent、验证、审查、状态转换和 Git 集成。

Windows PowerShell 使用 `.\jobslayer.cmd` 替换上述 `./jobslayer`。控制平面、
临时 Git 仓库闭环和完整开发检查可原生运行；本节现成 BraveNewWorld runbook
仍调用测试床的 `./bnw`，实际执行它时需要 POSIX 兼容环境。

每一步后都可再次运行 `inspect-run`。摘要中的 capability 只反映当时真实可执行的下一步：`decision_recording`、`decision_application`、`source_integration` 或 `workspace_cleanup`。

## 外部 authority

UI 只创建 `HumanDecision`，不签发权限。体验测试需要由人工侧另行提供一个当前有效的 `ApprovalAuthority` JSON：

```json
{
  "schema_version": "1.0",
  "authorization_id": "local-experience-approval-001",
  "actor_id": "与 run-ui --actor-id 完全相同",
  "allowed_decision_kinds": ["merge_review"],
  "issued_at": "带时区且早于应用时刻的 RFC 3339 时间",
  "valid_until": "带时区且晚于应用时刻的 RFC 3339 时间"
}
```

本地 CLI 尚未认证 `actor_id`，也不会自行签发 authority；这正是当前体验需要重点验证的人工边界。

## BraveNewWorld 现成候选

- 本地仓库：`/home/fangzhou/projects/JobSlayer/TestProjects/BraveNewWorld`
- Git 地址：`https://github.com/fangzhouRWTH/BraveNewWorld.git`
- 当前固定基线：`fb43878c9f0164deef272e55969c0fc134a6d6a3` / `bnw-0`
- 真实 Codex run：`.jobslayer/runs/bnw-filter-demo-001-run-01`

该 run 仍停在 `MergeReview`，没有 `decision.json`，主 checkout 仍干净且位于 BNW-0。可先只运行 `inspect-run` 和 `run-ui` 体验证据与决定记录；只有你明确选择 `approve`、提供有效 authority，并随后显式执行 `integrate-run`，本地 `main` 才会快进。`cleanup-run` 只移除完成后的干净 worktree，保留任务分支。任何命令都不会 push 或部署。

## 建议体验检查表

1. 决策页是否以足够少的信息准确解释任务、风险、补丁、验证和审查结论；
2. `request_changes`、`reject` 与 `approve` 的后果是否清楚，是否有误导性的“自动完成”感；
3. authority 缺失、过期、actor 不匹配时是否明确拒绝且状态不变；
4. 审核后修改 worktree 或移动 `main` 时，`integrate-run` 是否拒绝且不覆盖现场；
5. 成功集成后 commit trailer、目标 HEAD、运行账本、审计链与制品是否一致；
6. 清理后能否继续检查完整 run，且任务分支仍可用于追溯；
7. 哪些信息下一轮应进入 UI，哪些继续保留为显式 CLI 操作。

体验期间请保留 run 目录，不要手工改写 `records.jsonl`、`workflow.jsonl` 或制品对象；发现问题时记录命令、run id、预期和实际结果即可。
