# ADR-0051：固定快照、只读执行的外部 UI 建议适配器

- 状态：Accepted
- 日期：2026-09-03
- 推进：[ADR-0050](0050-semantic-elastic-ui-design-contract.md)
- 上游：[`nextlevelbuilder/ui-ux-pro-max-skill`](https://github.com/nextlevelbuilder/ui-ux-pro-max-skill)、
  release `v2.15.0`

## 背景

SUID 已能精确记录页面结构、设计状态、版本和活动方案，但它刻意不内置视觉风格、颜色、排版、
可访问性和各前端技术栈的庞大知识库。UI/UX Pro Max 提供标准库 Python/BM25 检索、静态数据和 JSON
输出，可以补充这部分设计建议；其输出仍可能受查询词和通用分类影响，例如内部任务管理器查询也可能
命中营销型 landing pattern，不能被直接视为产品决定。

直接运行上游 `npx ... init --ai codex` 会把安装器、平台模板和多个 brand/slides/design-system 等
兄弟 Skill 一并带入；`@latest` 还会让上游发布状态在没有本仓评审的情况下改变执行输入。这既扩大
供应链和文件写入面，也会让第三方 `MASTER.md` 与唯一活动 SUID 形成两个相互竞争的设计真相。

## 决定

1. UI/UX Pro Max 定位为 `UIAdvisor` 后面的只读建议提供方，不是 Agent、设计状态存储、SUID renderer
   或工作流执行器。provider-neutral `UIAdviceRequest` 必须绑定活动 SUID 的 page、scheme、revision 和
   descriptor SHA-256；规范化 recommendation 只表达候选设计知识。
2. 仓库只保留 `v2.15.0` 的核心 `data/`、五个标准库 Python 脚本和原 MIT `LICENSE`。不引入 npm
   installer、平台模板、持久化命令、图片生成器或六个兄弟 Skill。快照位于独立 `third_party/`
   版本目录，来源 tag、commit、文件数、总字节数和 canonical tree SHA-256 由
   `integrations/ui-ux-pro-max/lock.json` 固定。
3. adapter 每次执行前复核 lock、仓库边界、禁止 symlink/special/额外路径并计算整棵快照 hash。
   只由 JobSlayer 构造 `search.py --json` 的 design-system/domain/stack 三种 argv；用户不能追加原始
   参数，因此 `--persist`、`--output-dir`、`--force` 和项目写入不可达。子进程使用固定解释器的
   isolated/no-bytecode 模式、临时 cwd、最小环境、20 秒 timeout 和 2 MiB stdout 上限。
4. 应用服务把未经改写的 provider JSON 注册为 `ui_advice.provider_raw` 内容寻址制品，再把包含来源、
   查询、活动 SUID binding、规范化建议和 raw artifact ID 的记录注册为
   `ui_advice.normalized_evidence`。第三方输出不会直接写入 SUID。
5. Agent 需要这些建议时，由 `UIDesignAgentRequest.advisory_evidence_artifact_ids` 显式列出 normalized
   evidence。Agent draft 必须把这些 ID 原样带入自己的 evidence，否则拒绝；现有 stale hash、stable
   修改授权、stable 晋升和活动 binding 保护继续生效。
6. `validate-ui-advisor` 是来源/内容/上游数据一致性检查，加入 `./jobslayer check`；
   `collect-ui-advice` 是唯一公共查询入口。外部快照升级必须进入新的版本目录并同时评审 license、lock、
   normalization tests 和代表性查询，不允许原位编辑或自动跟随 `main/@latest`。

## 后果

- JobSlayer 获得可离线、可重复、跨 Agent 使用的 UI/UX 候选知识，同时 SUID 仍是唯一设计状态和活动
  方案来源；一次建议查询不会调用 Codex 或其他模型，Agent 推理成本仍由后续显式步骤承担。
- 完整上游数据快照增加约 3.3 MiB 仓库体积，换来无运行时下载、无 npm 执行和可精确复现。未来只有
  实测收益超过维护成本时才升级，不建立自动更新机器人。
- tree hash 证明执行内容与评审快照一致，不证明上游建议在产品语境中正确。Agent 必须先筛选，planned
  单元仍须 implementation observation 和验证，stable 单元仍需人工/政策解锁。
- 当前没有把 UI advisor 暴露给浏览器或 TaskManager 自动调度，也没有安装原生 Codex Skill。待三个
  代表性 UI 任务取得人工接受证据后，再决定是否增加自动查询节点；该决定不能扩大 advisor 权限。

## 未采用方案

- 全量执行上游 Codex installer：引入无关 Skill 和 npm 供应链，并产生超出需求的文件面。
- 全局安装或跟随 `@latest`：环境不可复现，更新无法绑定任务和证据。
- 直接采用上游 `design-system/.../MASTER.md`：会与 SUID catalog/active binding 形成第二个权威来源。
- 把 provider 原始 JSON 塞进 SUID：污染提供方无关契约，也会让第三方字段变化破坏设计版本。
- 让 advisor 自动改 React/CSS：知识检索没有权限、视觉观察和完成判定能力。
