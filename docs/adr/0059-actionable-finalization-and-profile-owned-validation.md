# ADR-0059：让固化阻塞可恢复，并由锁定 profile 拥有验证命令

- 状态：Accepted
- 日期：2026-09-04
- 依据：[ADR-0033](0033-evidence-bound-codex-planning-adapter.md)、
  [ADR-0038](0038-source-pinned-bravenewworld-execution-target.md)、
  [ADR-0046](0046-content-bound-local-dependency-attachments.md)、
  [ADR-0058](0058-one-task-one-run-and-terminal-projection.md)

## 背景

新建 3D Life Game 任务在 plan R3 已通过结构检查并绑定 target，但固化按钮被六个 target blocker 禁用：
BraveNewWorld 登记仍停在首次基线、普通桌面入口没有传入本机 Anygine 两项 attachment，而且通用规划
fixture 没在自然语言节点中逐字重复三条 `./bnw` 命令。UI 只能显示错误，既不能更新过期 source
binding，也没有告诉用户如何让启动进程获得依赖，因此指导和按钮形成死路。

验证命令本已存在于内容寻址、Run 绑定的 source-controlled validation profile；继续依赖自然语言
字符串重复并没有增加执行安全性。

## 决定

1. required validation command 始终由 Run 冻结的 validation profile 强制附加和执行。DAG 未逐字重复
   命令降为 warning，不单独阻止 finalize/assembly；跨项目指令、source/profile drift、错误 Git 基线
   和未就绪 attachment 继续作为 blocker。
2. 尚无 Run 且 target source bundle 缺失或漂移时，编排页把唯一主动作切换为“更新目标绑定”。它调用
   既有 revision-bound target command，形成新 plan revision，然后重新计算预检；不能用前端覆盖 hash。
3. `start.py` 默认规划 adapter 改为本机 Codex `gpt-5.6-sol` / `xhigh`，仍只生成待用户应用的 proposal，
   不自动固化。启动时从标准项目布局发现 Anygine source/toolchain，或读取两个明确环境变量；后端仍
   核对 source pin、Git origin/cleanliness 与目录内容 hash，发现路径不等于信任路径。
4. 当前 BraveNewWorld 本机开发基线推进到已完成 Life Game 的
   `d4947e7fdca4f70970c04fcf61221b55afddfb25`，本地 tag 为 `bnw-life-game-1`，登记
   `published=false`。在明确发布授权前不推送该 tag，不向其他主机宣称可获取。
5. 默认桌面入口仍不连接 durable executor、validator 或 source integrator。本决定只修复规划/固化
   死路，不以单一高权限身份绕过后续 reviewer/approver 独立性。
6. 发布并激活 SUID `focused-task-graph@10`，保留 v9 全部 stable 单元。

## 后果

- 当前 R3 只需点击一次“更新目标绑定”，下一 read model 即可给出可用的“固化任务流”。
- 新任务使用真实规划 Agent，不再默认得到与目标无关的固定五节点模板；Agent 输出仍非权威。
- 确定性执行契约由 profile/hash 保证，不再由脆弱的文字包含判断承担。
- 非标准目录或另一台机器仍会明确显示 attachment blocker，并可通过环境变量修复。
- 本机 baseline tag 是可审计但未发布的外部状态；发布需要另行授权。

## 未采用方案

- 无条件放开所有 target blocker：会允许错误基线和未验证依赖进入 Run。
- 在浏览器自动重绑或固化：会隐藏 plan revision 变化和人类决定。
- 自动把三条命令写进 Agent 文本：重复 profile 真相且可能制造计划噪声。
- 默认启动完整高权限执行/审查/集成：会模糊角色独立性，超出本次固化修复范围。
