# 最小开发闭环与体验测试手册

## 结论

JobSlayer 当前已经支持本地单任务成功路径的全部基础操作：版本化任务绑定、独立 worktree、签名执行授权、预算/上下文门禁、scripted 或受治理 Agent 执行、路径门禁、确定性验证、独立实现审查、可视化人工决定、签名 authority 应用、受控本地 commit/fast-forward、完成证据和安全 worktree 清理。

```text
Draft → Planned → Implementing → Verifying → Reviewing → MergeReview
  → Integrating → Completed → workspace cleanup
```

失败、拒绝和要求修改会留下证据并停在 `Failed`、`Cancelled` 或 `Repairing`。当前还没有自动发起下一轮 repair；因此“已闭合”指单任务本地成功路径，不包含自动重试、远端 push/PR 或部署。

## 统一入口

所有操作都通过根脚本：

```bash
./jobslayer check
./jobslayer build-phase0-corpus
./jobslayer inspect-readiness --state-root .jobslayer/phase0-corpus/state \
  --required-reviewed-tasks 20
./jobslayer inspect-run RUN_DIR
./jobslayer inspect-recovery RUN_DIR
./jobslayer review-run RUN_DIR \
  --identity-session .jobslayer/identity/reviewer.json \
  --identity-key .jobslayer/identity/key.json \
  --status accepted --summary "实现与验证证据一致"
./jobslayer run-ui RUN_DIR \
  --identity-session .jobslayer/identity/approver.json \
  --identity-key .jobslayer/identity/key.json --open-browser
./jobslayer issue-approval-authority \
  --key .jobslayer/identity/key.json \
  --identity-session .jobslayer/identity/approver.json \
  --decision-kind merge_review --output .jobslayer/identity/authority.json
./jobslayer apply-run-decision RUN_DIR \
  --authority .jobslayer/identity/authority.json \
  --identity-session .jobslayer/identity/approver.json \
  --identity-key .jobslayer/identity/key.json
./jobslayer integrate-run RUN_DIR \
  --identity-session .jobslayer/identity/approver.json \
  --identity-key .jobslayer/identity/key.json
./jobslayer cleanup-run RUN_DIR \
  --identity-session .jobslayer/identity/approver.json \
  --identity-key .jobslayer/identity/key.json
```

若 `inspect-recovery` 报告 `recoverable`，可用带 `recover_run` 权限的 session 执行
`./jobslayer recover-run RUN_DIR --identity-session SESSION --identity-key KEY`。当前支持 `restore_decision_card`，以及在 Git、
原始集成制品和 `Completed` 转换全部只读证明一致后执行
`resume_decision_application_record`、`resume_source_integration_record` 和
`resume_workspace_cleanup_record`；execution outcome 已完整落盘时还支持
`resume_execution_record`。这些动作只追加缺失的 run record，不重复 Agent、决定
转换、commit、fast-forward 或 worktree remove，也不创建替代制品。其他状态会
拒绝并要求人工处理；恢复器不会覆盖可疑文件或重复执行 Agent、验证和审查。

Windows PowerShell 使用 `.\jobslayer.cmd` 替换上述 `./jobslayer`。控制平面、
临时 Git 仓库闭环和完整开发检查可原生运行；BraveNewWorld profile 已显式将 POSIX `./bnw`
映射为源控授权的 Windows `.\bnw.cmd`，不要求 WSL。真实 C++/GPU validation 仍要求操作者绑定
当前平台准备好的 Anygine source、Conan toolchain 和图形/Vulkan 主机前置条件。

`build-phase0-corpus` 从 `corpora/phase0-foundation-v1.json` 在新的忽略目录中
重建 21 个真实 run，并拒绝覆盖既有输出。它用于自动回归且明确标记 fixture；
其中的自动决定不满足本节的真实人工计划/决定体验要求。

每一步后都可再次运行 `inspect-run`。摘要中的 capability 只反映当时真实可执行的下一步：`decision_recording`、`decision_application`、`source_integration` 或 `workspace_cleanup`。

## 签名 identity 与 authority

UI 只创建 `HumanDecision`，不自动应用权限。首次本地体验先创建一个签名 key，再按
最小角色签发 session：

```bash
./jobslayer create-local-identity-key .jobslayer/identity/key.json
./jobslayer issue-local-identity-session \
  --key .jobslayer/identity/key.json \
  --subject-id local-approver --display-name "Local approver" \
  --role approver --output .jobslayer/identity/approver.json
```

`issue-approval-authority` 由签名 session 签发短期 proof；`apply-run-decision` 再验证
issuer、session、actor、decision kind、策略版本和有效期。key/session/authority 都不应
提交、写入制品或交给 Agent。生产部署仍应以 OIDC/mTLS 和 secret broker adapter 替换
本地 HMAC adapter。

## BraveNewWorld 当前基线

- 本地仓库：仓库根相对路径 `../TestProjects/BraveNewWorld`；当前 Windows 部署为
  `D:\projects\JobSlayer\TestProjects\BraveNewWorld`
- Git 地址：`https://github.com/fangzhouRWTH/BraveNewWorld.git`
- 当前固定基线：`d4947e7fdca4f70970c04fcf61221b55afddfb25` / `bnw-life-game-1`（本机待发布）
- 当前 target：`brave-new-world-anygine-app-v1`
- 当前 validation gates：POSIX 为 `./bnw contract/test/run`，Windows 为对应
  `.\bnw.cmd contract/test/run` 显式变体

旧滤波/悬架 run 仍可作为追加式历史证据读取，但其源码 worktree 已在完整归档后移除，不再是当前
可集成候选。下一次体验应从 TaskManager 新建具体 Anygine 小 App 任务；启动 API 时
必须绑定 `anygine-source`、`anygine-conan-toolchain` 和所需图形会话环境。attachment 缺失或
漂移会在固化/run 装配前失败关闭，真实 build/runtime 结果则进入 validation 证据。

## 建议体验检查表

1. 决策页是否以足够少的信息准确解释任务、风险、补丁、验证和审查结论；
2. `request_changes`、`reject` 与 `approve` 的后果是否清楚，是否有误导性的“自动完成”感；
3. session/authority 缺失、过期、篡改、actor 或权限不匹配时是否明确拒绝且状态不变；
4. 审核后修改 worktree 或移动 `main` 时，`integrate-run` 是否拒绝且不覆盖现场；
5. 成功集成后 commit trailer、目标 HEAD、运行账本、审计链与制品是否一致；
6. 清理后能否继续检查完整 run，且任务分支仍可用于追溯；
7. 哪些信息下一轮应进入 UI，哪些继续保留为显式 CLI 操作。

体验期间请保留 run 目录，不要手工改写 `records.jsonl`、`workflow.jsonl` 或制品对象；发现问题时记录命令、run id、预期和实际结果即可。
