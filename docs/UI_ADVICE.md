# 外部 UI/UX 建议接入

## 定位与边界

JobSlayer 将 UI/UX Pro Max 作为离线知识检索器，不把它当作 Agent、页面生成器或设计真相。当前接入
只运行固定的核心 Python/BM25 搜索；不会调用模型、访问网络、安装 npm 包、写设计系统文件或直接
修改 React/CSS。

```text
active SUID revision
        ↓ exact page/scheme/revision/hash
UIAdviceRequest
        ↓
UIAdvisor protocol
        ↓
UIUXProMaxAdvisor ── pinned data/search.py --json
        ↓                         ↓
normalized recommendations       immutable raw JSON artifact
        └──────────────┬──────────┘
                       ↓
             normalized evidence artifact
                       ↓ explicit evidence ID
              UIDesignAgentRequest / draft
                       ↓
            SUID review, implementation, verification
```

模块职责：

| 位置 | 职责 |
|---|---|
| `src/jobslayer/ui_advice/` | 提供方无关 request、recommendation、evidence 和 `UIAdvisor` protocol |
| `src/jobslayer/application/ui_advice.py` | 注册 raw/normalized 不可变制品，不解释设计真相 |
| `src/jobslayer/adapters/ui_ux_pro_max.py` | 校验固定快照、构造白名单 argv、隔离执行、规范化 provider JSON |
| `integrations/ui-ux-pro-max/lock.json` | 固定版本、来源、许可、tree hash、domain/stack 白名单 |
| `third_party/ui-ux-pro-max/2.15.0/` | 未修改的 core data/scripts 和上游 MIT LICENSE |
| `ui-designs/` | 唯一 SUID revision/catalog/active binding；不由 advisor 写入 |

## 校验与查询

先验证来源和整棵快照；该命令还运行上游的离线数据一致性检查：

```bash
./jobslayer validate-ui-advisor
```

收集一组绑定当前 TaskManager SUID 的 React 可访问性建议：

```bash
./jobslayer collect-ui-advice \
  --page-id task-manager \
  --task-id ui-task-manager \
  --request-id task-manager-react-a11y-001 \
  --query "live updates accessibility" \
  --mode stack --stack react --max-results 3
```

默认 raw/normalized 制品写入 `.jobslayer/ui-advice-artifacts`。可以通过 `--artifact-root` 指向另一个
明确的本地注册表。结构化 stdout 返回两个 manifest 和 normalized evidence；后续 Agent 请求引用
`normalized_artifact.artifact_id`，而不是复制或信任终端摘要。

总体设计方向使用 design-system 模式：

```bash
./jobslayer collect-ui-advice \
  --page-id task-manager --task-id ui-task-manager \
  --query "productivity task manager dashboard" \
  --mode design_system --project-name JobSlayer \
  --variance 3 --motion 2 --density 8
```

针对单一语义结果查询使用 domain 模式：

```bash
./jobslayer collect-ui-advice \
  --page-id task-manager --task-id ui-task-manager \
  --query "focus not obscured keyboard navigation" \
  --mode domain --domain ux --max-results 3
```

每次查询只表达一个主要意图。建议优先使用稳定、短小的英文检索词，把原始用户中文保留在任务讨论，
把生成后的英文查询同时记录在 evidence 中。完整 design-system 输出可能混入通用 landing pattern；
它只能作为候选，Agent 应丢弃与 SUID `intent/non_goals/must_not` 冲突的条目。

## 进入 SUID/Agent 的方式

1. 从后端/source registry 取得活动 SUID；CLI 自动完成这一步并锁定 descriptor hash。
2. 收集 UI advice，保存 raw 和 normalized artifact。
3. 只选择与当前 dirty/planned 单元有关的建议，把 normalized artifact ID 放进
   `UIDesignAgentRequest.advisory_evidence_artifact_ids`。
4. Agent 生成下一完整 SUID revision 或实现草稿；draft 必须保留该 evidence ID。
5. 继续执行 ADR-0050 的 elastic reconciliation。相同或 minor 差异仍是 `verify_only`，不能因存在外部
   建议就重复调整；stable material 变化仍要求澄清与 human/policy 授权。

UI advice artifact 证明“检索器返回过什么”，不证明建议正确、实现完成或视觉已接受。

## 版本升级

当前版本固定为上游 `v2.15.0` / commit
`a38d04c3d5c298c851dbe5e6ee1965ee3de42cb5`。升级时：

1. 审查新的 release、license、安全公告和数据来源；不要使用浮动 `main` 或 `@latest`；
2. 从精确 tag 仅导入 `src/ui-ux-pro-max/data/`、五个顶层 Python 脚本和 `LICENSE` 到新的版本目录；
3. 不删除旧目录，不原位修改旧快照，不导入 installer/templates/sibling skills；
4. 计算并更新 lock 的 source commit、tree SHA-256、文件数和字节数；
5. 更新 normalization/漂移/Windows-POSIX 回归及三类代表性查询；
6. 运行 `./jobslayer validate-ui-advisor` 和完整 `./jobslayer check`，经独立评审后再切换 lock。

canonical tree hash 按相对 POSIX 路径排序，并依次哈希 `path + NUL + bytes + NUL`。任何增加、删除、
编辑、symlink 或特殊文件都会失败关闭。

## 当前退出条件

首个“垂直版面导航”试验已经完成：UX/design-system evidence 经人工筛选进入 SUID v2，React 专项零
结果被保留但未冒充依据；五个桌面版面和一个窄屏版面已做真实浏览器 observation。该试验没有让
advisor 自动写代码或成为活动设计来源。

| 请求 | normalized artifact | 结果与处置 |
|---|---|---|
| `task-manager-navigation-react-001` | `artifact-0378ca2f954a4beebea42645d948d3d8` | React stack 返回 0 项，如实保留，不作为依据 |
| `task-manager-navigation-ux-001` | `artifact-dd6cb3139969440ea2e4825cf61493c9` | 采用 active state、原生 control 语义、键盘/focus 与 deep link 原则 |
| `task-manager-control-dashboard-001` | `artifact-98b43b053cc6482ba2a8ec623a0d4f75` | 采用克制高密度工具风格和非颜色单一状态；拒绝通用营销 pattern 与自动换色 |

三份请求均绑定先前活动的 `focused-task-graph@1` / `7940b5e6...d49e`，经 human decision 后才进入
`focused-task-graph@2`。原始输出 hash、来源 commit 和完整 recommendation 保留在本地内容寻址注册表；
ADR-0052 与本表提供 source-controlled 决策轨迹。

第二个“Calm Ops 可读性”试验绑定活动 v2 / `cdb4e3be...126e`，用于回应产品负责人“信息过多且文字
费力”的实际反馈：

| 请求 | normalized artifact | 结果与处置 |
|---|---|---|
| `task-manager-calm-ops-style-001` | `artifact-579492d815934dd1a7a7c4405bd9ca39` | 采用 Data-Dense 的 12–14px 基线、Accessible 的对比/focus、Dark OLED 的低眩光；Fluent 只作 calm hierarchy 参考 |
| `task-manager-calm-ops-ux-001` | `artifact-18e161b85cf74982aea57858f959707c` | 采用可读字号、4.5:1 对比、非颜色单一状态、受控行长与长 token 换行 |
| `task-manager-calm-ops-system-001` | `artifact-dbc2eebf09594ffb80f201cebeb81b46` | 采用 Minimalism/Swiss 和中等 density；拒绝 FAQ landing、自动色板与远程字体 import |
| `task-manager-calm-ops-disclosure-001` | `artifact-f5f3bfa95b184ebca5c57b83a05c2bee` | 未命中渐进披露；返回的自动轮播等结果与本任务无关，不进入 SUID activation evidence |

人工作出信息删减决定后形成 `focused-task-graph@3` 与 ADR-0053。Skill 没有自动写 CSS、决定删除哪些
任务事实或把外部组件库加入运行时。

第三个试验针对活动 v3 的独立 Quick Agent，查询“streaming agent chat quota remaining reset time
execution mode safety”，形成 normalized `artifact-98595c026e0c4a93865427be27a292bc` 与 raw
`artifact-7d88df5d8e6b496aa248e6bc87a4b432`。只采纳流式反馈持续可见和安全标签不隐藏；命中的 stacking
context 建议与本需求无关。人工决定后形成 `focused-task-graph@4` 与 ADR-0054，advisor 没有获得
Codex 权限、没有调用 Agent，也没有直接修改活动 binding。

在自动查询进入 TaskManager DAG 前，先完成并人工接受三组试验：DAG 信息密度/层级、节点反馈与错误
可访问性、右侧详情/对话的窄屏重排。每组都必须证明建议能转成有限 planned 变化、不会触发 stable
无意义重构，且 Windows/Linux 得到等价结构化结果。
