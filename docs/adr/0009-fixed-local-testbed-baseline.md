# ADR-0009：显式登记并只读检查外部测试床固定基线

- 状态：Accepted
- 日期：2026-08-07

## 背景

BraveNewWorld 已从空仓库形成 BNW-0 本地根提交和固定标签。JobSlayer 后续创建 worktree、比较 Agent 或复现验证前，必须能区分“计划中的仓库”“当前本地基线”和“远端已发布基线”，不能依赖操作人员的自然语言记忆。

## 决策

1. `TestbedSpec` 可登记固定 40 位 commit、Git tag、发布状态和结构化验证命令。
2. 发布状态必须显式记录；本地提交和标签存在不等于远端可拉取。
3. 通过提供方无关 `TestbedInspector` 协议和本地 Git adapter 只读观察 checkout。
4. 本地基线门禁同时要求：工作树干净、HEAD 命中登记 commit、tag 解引用后命中同一 commit、origin 命中任一登记 URL。
5. `local_checkout_hint` 相对 JobSlayer 源码仓库根解析，并允许 CLI 显式覆盖；它仍只是本机提示，不进入可移植任务契约。
6. `inspect-testbed` 只报告事实并返回门禁退出码，不执行测试、不创建 commit/tag、不 fetch 或 push，也不改变 JobSlayer 工作流状态。
7. 完整开发 `check` 继续只验证机器可读登记，避免其他开发端必须在固定相邻目录拥有外部仓库；需要本地基线时显式运行 inspection。

## 后果

- JobSlayer 可以在真实任务前拒绝脏工作树、错 HEAD、漂移标签或未登记远端。
- `published: false` 清楚表达当前 BNW-0 只存在于本机；其他开发端在推送前无法按登记 commit 拉取。
- 当前 inspection 是事实快照，不证明远端包含 commit，也不运行 `verification_command`。远端发布检查和受治理验证配置属于下一迭代。
- 不采用自动 clone/fetch，因为它会把只读确认扩大为网络和仓库变更，且当前没有凭据、重试和远端证据政策。
