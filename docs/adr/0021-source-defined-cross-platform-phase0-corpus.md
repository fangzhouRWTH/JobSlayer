# ADR-0021：Phase 0 语料由源码定义并通过真实服务跨平台重建

- 状态：Accepted
- 日期：2026-08-12

## 背景

`inspect-readiness` 已能只读验证本地 run，但空状态目录无法提供 Phase 0 退出证据。
直接提交生成后的绝对路径、Git worktree 和运行制品既不可移植，也容易把手写摘要
误当成真实闭环。自动化语料还不能冒充真实人工决定体验。

Windows checkout 经 WSL 的 Git 读取还暴露出两个平台差异：POSIX 上制品 manifest
按设计是只读文件，篡改测试必须先显式解除只读；CRLF working tree 需要在 diff
门禁中按 Git checkout 规则规范化后再检查空白。

## 决定

1. 源码只保存版本化 corpus definition，列出唯一 case id 和预期动作；生成后的
   testbed、control inputs、worktree、run ledger、journal 和 artifacts 保存在新建
   的输出目录，不提交机器相关绝对路径。
2. `Phase0CorpusBuilder` 创建固定 Git baseline 和源码绑定的 task/profile/runbook/
   patch，然后只通过 `LocalRunCoordinator` 的公共 execute/review/decision/
   integrate/cleanup 服务形成证据，不直接写 run 摘要或状态。
3. 默认定义生成 21 个不同 task：20 个进入独立审查，覆盖批准完成、要求修改、
   取消和待决定；额外一个通过真实失败验证进入 `Repairing`。
4. 自动决定仅是明确标记的 deterministic fixture。报告固定写入
   `evidence_class: deterministic_fixture`、`human_confirmation_claimed: false`，
   不能满足真实人工体验确认。
5. 输出目录必须不存在，生成器拒绝覆盖；中途失败保留部分证据供调查。完成时再
   用独立 `Phase0ReadinessEvaluator` 检查全部 run，门禁不通过则构建失败。
6. GitHub Actions 对 Ubuntu 与 Windows 使用同一 definition 分别完整 `check`、
   构建 corpus 并二次 `inspect-readiness`。本地也以原生 Windows 和 Ubuntu/WSL2
   复核同一流程。
7. 开发 diff 门禁使用 `git -c core.autocrlf=true diff --check`，让 CRLF checkout
   先遵守 Git 文本规范化再检查尾随空白和冲突标记；这不会忽略普通尾随空白。

## 理由

- 源码定义比提交机器生成状态更可审查，同时真实走控制器能防止“只造一份通过的
  JSON 报告”。
- 明确区分 fixture 与人工体验，保留自动回归价值且不削弱审批语义。
- 同一生成器在两种宿主重建证据，比维护 Windows/POSIX 两套金丝雀数据更不易漂移。

## 后果

- `build-phase0-corpus` 默认约一分钟并创建多个 Git worktree，不进入每次普通
  `check`；小型四路径版本进入单元/集成套件。
- 完整 corpus 输出位于忽略的 `.jobslayer/phase0-corpus`，需要作为验证证据保留时
  由操作者归档，不被当作源码。
- CI 定义已经具备双平台门禁；在变更提交和推送前，本地 WSL 结果不能冒充远端
  GitHub Actions 的成功状态。
- 真实人工计划/决定体验仍必须由操作者提交并复盘，生成器不会自动替代。

## 未选择的方案

- 提交 21 份完整 run 目录：拒绝，包含平台路径和 Git worktree 关系，体积大且不
  可移植。
- 直接写 readiness 所需摘要：拒绝，绕过 kernel、制品注册、验证和审计链。
- 把 fixture actor 宣称为真实人类：拒绝，会污染 Phase 0 的人工退出证据。
