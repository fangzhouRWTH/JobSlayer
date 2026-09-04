# 语义弹性 UI 描述框架

## 1. 定位

Semantic UI Design（SUID）是人、Agent、后端和前端之间的设计交换数据。它记录“页面为什么这样
组织、各区域承担什么职责、交互如何反馈、哪些约束不可破坏”，并保留一定实现自由；它不描述具体
DOM、React component、CSS selector 或像素坐标。

```text
自然语言讨论 / 设计决定
             ↓
完整 SUID candidate revision
  区域节点 + 空间关系 + 用户旅程 + 要求
             ↓
结构、hash chain、stable 保护、活动绑定校验
             ↓
后端选择一个 exact active revision
       ↙                         ↘
Agent implementation request     authenticated UI read model
观察 → 确定性处置 → 实现/验证       只展示方案与设计状态
```

工程真相边界不变：SUID 能影响“应怎样设计和检查 UI”，但不能修改任务状态、权限、执行、验证、审批、
Git 集成或完成结果。

## 2. 描述结构

每个 description 文件完整包含：

| 字段 | 精确部分 | 弹性部分 |
|---|---|---|
| identity | `page_id`、`scheme_id`、连续 revision、parent hash | 标题、总体设计意图 |
| regions | 稳定 unit ID、唯一性、实现锚点路径 | 角色、职责、内容、交互描述 |
| relations | 已知区域引用、无环 containment、关系类型、可选比例 | 约束和空间意图措辞 |
| journeys | 稳定 journey/step ID、区域引用、步骤顺序 | actor 动作与预期反馈 |
| requirements | 稳定 ID、`must/should/may/must_not` | statement 和 verification hint |
| tracking | `dirty/planned/stable`、stable evidence | evidence 所解释的设计理由 |

例如 `left_of + preferred_ratio: "2:1"` 表示宽屏信息层级应接近左二右一；它不要求 Agent 写出固定
`grid-template-columns: 2fr 1fr`。实现可以使用 Grid、Flex、布局组件或响应式断点，只要没有违反关系、
强约束和用户旅程。

当前活动示例位于：

```text
ui-designs/catalog.json
ui-designs/task-manager/focused-task-graph/v1.json
ui-designs/task-manager/focused-task-graph/v2.json
ui-designs/task-manager/focused-task-graph/v3.json  # 历史 Calm Ops revision
ui-designs/task-manager/focused-task-graph/v4.json  # 历史 Quick Agent revision
ui-designs/task-manager/focused-task-graph/v5.json  # 历史动态模型目录 revision
ui-designs/task-manager/focused-task-graph/v6.json  # 历史持久 coordinator revision
ui-designs/task-manager/focused-task-graph/v7.json  # 历史人工交互指导 revision
ui-designs/task-manager/focused-task-graph/v8.json  # 历史受治理人工确认/反馈 revision
ui-designs/task-manager/focused-task-graph/v9.json  # 历史单任务闭环与终态投影 revision
ui-designs/task-manager/focused-task-graph/v10.json # 历史可恢复固化路径 revision
ui-designs/task-manager/focused-task-graph/v11.json # 历史默认受治理执行路径 revision
ui-designs/task-manager/focused-task-graph/v12.json # 当前可恢复执行提交 revision
```

## 3. 三种设计状态

| 状态 | 含义 | Agent 默认权限 | 退出条件 |
|---|---|---|---|
| `dirty` | 描述新改、冲突、缺信息或本身需要修复 | 只修复/提议描述，不直接落代码 | 新 revision 中被明确改成 planned，或放弃该意图 |
| `planned` | 意图已经决定，属于当前可落实范围 | 先观察当前实现；实质漂移才改代码 | 实现证据和人工/政策决定支持转为 stable |
| `stable` | 已确认且有证据的设计基线 | 静态参考，不主动重做 | 显式 stable-change authorization 后由新 revision 改变 |

状态属于每个 region/relation/journey/requirement，不属于整个页面。一个活动方案可以同时包含三种状态，
从而精确缩小 Agent 的工作范围。

## 4. 弹性落实规则

Observation 必须绑定活动 `page_id + scheme_id + revision + descriptor_sha256`。除 `unknown` 外，差异
判断必须引用 evidence ID。

| 设计状态 | 未观察 | `none` / `minor` | `material` | `unknown` |
|---|---|---|---|---|
| dirty | refine description | refine description | refine description | refine description |
| planned | inspect | verify only | implement | inspect |
| stable | reference only | reference only | clarify/unlock | clarify/unlock |

`minor` 只适用于没有改变信息层级、行为结果、用户旅程或 `must/must_not` 要求的实现变化，例如等价文案、
不影响职责的间距和组件内部细节。任何改变主要/次要区域、操作结果、权限暗示、可见状态、必需内容或禁止
边界的差异都是 `material`。Agent 的分类不是事实本身；截图、DOM/无障碍快照、API response、测试或
人工复核才是关联证据。

CLI 可在没有 observations 时先得到检查范围：

```bash
./jobslayer inspect-ui-design ui-designs/catalog.json --page-id task-manager
./jobslayer inspect-ui-design ui-designs/catalog.json --page-id task-manager \
  --observations examples/ui-design-navigation-observations.example.json
```

提供 observation JSON 后，同一命令输出每个单元的确定性 action：

```bash
./jobslayer inspect-ui-design ui-designs/catalog.json \
  --page-id task-manager \
  --observations examples/ui-design-observations.example.json
```

## 5. 版本与活动方案

### 新 revision

1. 读取活动 revision 和 canonical hash；
2. 创建新的完整文件，如 `v2.json`，revision 必须恰好加一并填写 parent hash；
3. 只修改 dirty/planned 单元；如确需修改/删除 stable 单元，为每个 unit ID 增加单独的
   `stable_change_authorizations`，actor 必须是 human 或 policy，且带理由和 evidence；
4. 把新文件及 canonical hash 加入 `catalog.descriptors`；此时活动 binding 保持旧 revision；
5. 运行 `./jobslayer validate-ui-design ui-designs/catalog.json`；
6. 经过设计评审后，再显式把该 page 的唯一 active binding 切换到新 scheme/revision/hash。

Description 使用 canonical JSON hash，因此仅改变缩进或键顺序不会创建新的语义版本。历史文件不删除、
不原位改写；Git 保留提交级审查与恢复能力。

### 新方案

替代布局使用新的 `scheme_id` 并从 revision 1 开始。它可以和现有方案同时登记、比较和演进，但
`active_bindings` 对同一个 `page_id` 只能有一条。登记 candidate 不等于激活，前端也没有选择活动
方案的本地状态。

## 6. Agent 生成与修复

Agent adapter 接收 `UIDesignAgentRequest`：活动完整描述、精确 hash、用户 instruction 和可选
observations；返回 `UIDesignAgentDraft`：基线 hash、摘要、下一完整 revision 和原始制品引用。请求还可
显式引用 `advisory_evidence_artifact_ids`；draft 必须原样保留这些引用，否则拒绝。

外部 UI/UX 知识不直接进入 description。当前固定版本的 UI/UX Pro Max 只读 adapter 先把 provider
原始 JSON 与规范化建议登记为不可变制品，并绑定本次活动 page/scheme/revision/hash；Agent 只消费
明确列出的 evidence ID。建议内容不能激活方案、解锁 stable 单元或证明实现完成。模块边界、命令和
升级规则见[外部 UI/UX 建议接入](UI_ADVICE.md)与
[ADR-0051](adr/0051-pinned-read-only-ui-advice-adapter.md)。

系统接受 candidate 前检查：

- draft 是否仍基于当前活动 hash；
- page/scheme 是否未漂移、revision 是否连续、parent hash 是否一致；
- unit/region/journey 引用、containment 和路径是否合法；
- stable 单元是否完全保留，或具有逐项 human/policy 授权；
- Agent 是否只被记录为作者，而没有冒充激活者。

Agent-authored revision 也不能把 dirty/planned 单元自行提升为 stable；该提升必须出现在 human/policy
作者的新 revision 中并携带 stability evidence。

Agent 可以修复 dirty 描述、补充计划内容或根据证据建议升级状态；它不能调用 UI read API 激活方案。
当前 source-control 方式由人工审查 catalog 变更。未来若增加在线编辑器，必须另建带签名身份、expected
revision、幂等键和追加审计的 command API。

## 7. 后端与前端边界

TaskManager 启动时验证整个 catalog。认证请求：

```text
GET /api/task-manager/ui-design
```

返回唯一活动 binding、完整 semantic description 和状态计数。当前 React 页面只用 binding 与计数
显示设计状态摘要，不读取 descriptor 路径、不决定活动版本，也不把 description 动态渲染为 UI。

这使后端未来可把 source-controlled registry 替换为数据库/事务 adapter，而不改变前端 read model；
同时让 Agent 使用同一语义数据开发真正的源代码，而不是在浏览器中建立第二套页面定义。

## 8. 验证

```bash
./jobslayer validate-ui-design ui-designs/catalog.json
./jobslayer inspect-ui-design ui-designs/catalog.json --page-id task-manager
./jobslayer check
```

完整门禁会拒绝 invalid JSON、越界/symlink 文件、身份或 hash 不匹配、revision 断链、多个活动方案、未知
区域引用、containment 环、stable 无证据和 stable 改动未逐项授权。

## 9. 当前限制

- observation 的 `minor/material` 仍需 Agent、视觉检查或人工判断；框架只固定输入、证据和处置，不
  假装能确定性理解所有视觉差异；
- 当前没有在线 SUID 编辑器、活动方案 command API 或负责生成/修复 revision 的 Codex UI-design
  adapter；已接入的 UI advice adapter 只检索离线知识，不是 Agent；
- 当前只登记 TaskManager 一个 scheme/revision。多方案、历史 revision 和 stable 解锁已经由模型与
  测试覆盖，但尚未制造无业务价值的第二套页面；
- planned 单元只有在形成新 revision、补齐验证 evidence 并明确升级后才成为 stable；代码已出现并不
  自动等于设计已接受。
