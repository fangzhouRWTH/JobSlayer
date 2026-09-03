# ADR-0050：版本化语义弹性 UI 描述与后端活动方案

- 状态：Accepted
- 日期：2026-09-03
- 推进：[ADR-0047](0047-single-screen-task-graph-preview.md)、
  [交互设计指南](../INTERACTION_DESIGN_GUIDE.md)

## 背景

当前 TaskManager 已有明确的单屏布局决策，但设计意图主要散布在 ADR、说明文档、React/CSS 和对话
上下文中。人可以理解“左侧约三分之二是 DAG，右侧是节点详情与 Agent 对话”，AI 却很难稳定判断：

- 哪些内容是不可随意重做的已确认基线；
- 哪些内容已经决定但尚待落实；
- 哪些文字仍有歧义，应先修复描述而不是直接改代码；
- 当前实现只存在可接受的视觉差异时，是否还需要反复调整；
- 多个页面方案并存时，哪一个精确版本才是当前设计输入。

把所有细节写成像素、CSS 或组件 DSL 会过早锁死实现，并诱导运行时动态生成低质量界面；只保留自由
Markdown 又无法做引用完整性、版本、状态、并发基线和活动方案检查。

## 决定

1. 引入 provider-neutral 的 Semantic UI Design（SUID）契约。每个 revision 是一个自包含 JSON
   描述，以稳定 ID 表达页面区域、空间/包含关系、用户旅程和强度分级要求。图结构、引用、层级、
   `must/must_not` 与版本关系是精确的；`intent`、职责、内容、反馈和验证提示使用中英文自然语言，
   允许实现弹性。SUID 不是 React tree、CSS、低代码 schema 或可执行脚本。
2. 每个语义单元必须标为：
   - `dirty`：描述本身有变更、冲突或歧义，只允许先澄清/修复描述；
   - `planned`：设计意图已经决定，可进入实现观察、验证和必要的代码落实；
   - `stable`：已有证据支持的静态参考，默认不得纳入重做。
   `stable` 单元必须携带 evidence ID。修改或删除前一 revision 的 stable 单元，下一 revision 必须
   对每个单元给出由 human/policy actor 记录的精确授权、理由和证据；Agent 不能充当授权 actor。
3. 弹性落实不使用不可审计的相似度分数。Agent/检查器提交绑定活动描述哈希的 implementation
   observation，并把差异归为 `none/minor/material/unknown`、附上摘要和证据。JobSlayer 用固定表得到
   动作：dirty → `refine_description`；planned + none/minor → `verify_only`；planned + material →
   `implement`；planned + unknown/未观察 → `inspect`；stable + none/minor/未观察 →
   `reference_only`；stable + material/unknown → `clarify`。因此轻微实现差异不会触发反复改写，
   但模糊判断也不能越过 stable 保护。
4. 一个 `(page_id, scheme_id, revision)` 对应一个完整、内容寻址的描述。后续 revision 必须连续并
   精确绑定前一 canonical SHA-256，不使用隐藏继承或跨文件局部覆盖。当前 source-controlled catalog
   登记全部受管理 revision，并对每个 page 只允许一个活动 binding；binding 精确包含 scheme、
   revision、hash、human/policy 激活决定和证据。多个 scheme 可以并存，增加候选 revision 不会
   自动切换活动方案。
5. Agent 输入/输出使用 provider-neutral `UIDesignAgentRequest` / `UIDesignAgentDraft`。草稿必须绑定
   当前活动 hash、形成下一连续 revision 并通过结构、引用、stable 保护和 hash-chain 校验。Agent
   可以生成或修复候选描述，但没有活动方案写 API，也不能通过前端状态激活自身草稿。当前激活变更
   通过受评审的 source-control catalog 决定；未来持久 adapter 仍须位于同一 `UIDesignQuery`/应用
   边界之后。
6. TaskManager 后端加载并验证 catalog，认证 read API 只返回当前活动 read model 和状态计数。
   React 前端不读取源控路径、不选择活动方案、不解释描述生成组件，只显示低干扰的方案/revision/
   状态摘要。具体实现仍由源码、测试和人工评审决定。
7. `validate-ui-design` 和 `inspect-ui-design` 成为统一 CLI；前者验证完整历史、内容 hash、stable
   授权与唯一活动绑定，后者输出活动完整描述及基于可选 observations 的确定性执行计划。源控 SUID
   catalog 校验加入 `./jobslayer check`，防止 UI 描述在完成门禁之外漂移。

## 后果

- 人、Agent、后端和前端拥有同一份可交付的中间语义数据，同时仍允许 React/CSS、组件组合和细节
  视觉有多种合理实现。
- “相近就不重做”成为显式、可测试的处置规则，而不是 Agent 自行声称已经足够接近；差异分类仍可能
  来自 Agent 判断，因此必须保留绑定和 evidence，不能冒充确定性视觉验证。
- 当前 source-control catalog 依赖 Git review 证明激活 actor 的真实性；schema 中的 actor 字段本身
  不是密码学签名。若未来允许运行时修改活动方案，必须新增认证 command、expected revision、审计
  journal 和授权策略，不能复用只读 API 偷渡写入。
- 完整 revision 会重复未变化的自然语言，但避免继承顺序和 fragment 合并造成同一方案出现多个有效
  解释；canonical hash 独立于 JSON 缩进与键顺序。
- SUID 只追踪设计状态，不替代 TaskManager DAG、WorkflowKernel、验证报告、审批或完成判定。

## 未采用方案

- 纯 Markdown：适合讨论，但无法确定性验证关系、状态、版本和单一激活。
- 像素/CSS/React AST DSL：精确但失去语义弹性，并把描述层变成第二个前端运行时。
- 由 Agent 直接编辑当前活动描述：缺少 stale-base、stable 保护与人工激活边界。
- 用单一数值相似度自动决定是否改代码：阈值缺乏可解释语义，也无法区分 `must_not` 漂移与装饰差异。
