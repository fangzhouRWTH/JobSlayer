# JobSlayer 初步实施路线图

## 规划口径

路线图以“可验证的闭环能力”为单位，而不是以功能数量为单位。每个阶段只有满足退出条件才进入下一阶段；日期在团队容量明确后再承诺，优先级和依赖关系已经确定。

状态标记：`[x]` 已初步落实，`[ ]` 未落实，`[~]` 仅有骨架、尚未接入真实外部系统。

## 2026-09 当前阶段目标 — TaskManager 串行闭环

中期目标不再以完整工程平台或通用仿真框架为退出条件，只交付：

1. 精简、可调试的图状 TaskManager UI；
2. 单 run 一次最多推进一个节点的持久串行 coordinator；
3. Agent/命令/验证/审查/人工门禁反馈统一回写 DAG、Backlog、总 Log 与制品；
4. BraveNewWorld + 固定 Anygine 公共 consumer 上的真实小 App 闭环。

退出条件是同一任务可在进程重启后继续，从 finalized DAG 串行到真实 Anygine build/Vulkan smoke
证据和最终人工门禁，且没有第二个状态所有者。Dagger、第二执行器、远程多租户、分布式调度和广义
仿真验证全部后置。

## Phase 0 — 契约与研究骨架（初步完成）

目标：证明外部编码执行器可以被稳定的内部契约和确定性状态机治理。

- [x] 建立仓库开发指导、架构原则和首个 ADR；
- [x] 定义 `TaskSpec`、`AgentRunSpec`、`RunEvent`；
- [x] 定义 `ArtifactManifest`、`VerificationReport`、`TransitionRecord`；
- [x] 实现合法状态转换、行为者权限和完成门禁；
- [x] 实现带序列号、哈希链校验及前缀保持原子 generation 发布的追加式 JSONL 审计日志；
- [x] 提供 CLI 闭环演示和自动化测试；
- [x] 登记首个外部实验项目 BraveNewWorld，并定义测试床方案；
- [~] 实现 Git 仓库镜像与一任务一 worktree（本地 worktree 适配器已完成，镜像/远程获取待实现）；
- [~] 实现受限本地命令运行器（命令规则、环境净化、超时/进程组终止和输出证据已完成；外部取消、网络及资源强隔离待实现）；
- [~] 接入第一个 Codex CLI 适配器并保留原始事件（adapter、假 CLI 合规测试及一次显式授权的真实模型任务已完成；生产凭据和外层隔离待实现）；
- [x] 支持从版本化 task/profile/runbook 创建、恢复和检查本地真实运行；
- [x] 建立旧 BraveNewWorld 确定性验证与完整真实 DAG 证据；旧网页/机电方向已归档，当前基线已重置并发布为 Anygine 小 App 测试床；
- [x] 提供最小人工计划/合并审批入口（决策卡、签名本地身份、RBAC、短期 authority、授权应用和控制器闭环）；
- [~] 提供可视化监督入口（真实 run 决定页和认证多任务 Dashboard 已接通；远程多租户界面后置）；
- [x] 固化 `./jobslayer` 统一入口，使 UI、完整开发验证、模块运行和安装后 CLI 共用同一 launcher；
- [x] 提供原生 Windows 仓库入口、平台化进程监督和字节稳定的 Git/制品路径，使控制平面完整开发门禁不依赖 WSL；
- [x] 用应用控制器串联一次任务、工作区、Agent、补丁、验证、审查和合并决策卡；
- [x] 区分批准与完成，并用审核 patch/tree、固定 base 和本地 fast-forward 证据闭合成功路径；
- [x] 提供只读 `inspect-readiness`，用经过完整性校验的运行语料量化自动退出条件，同时保留人工复盘门禁；
- [x] 完成本地单控制器的证据约束恢复协议：execution intent/outcome、决策卡投影、decision application、source integration 和 cleanup 均有保守分类/恢复，并通过双链原子发布及真实子进程崩溃矩阵验证；

退出条件进展：临时仓库已完整验证到 `Completed` 和 cleanup；真实 BNW 滤波任务已由 Codex 产生补丁并经确定性测试、独立 Agent 审查形成可审计 `MergeReview` 提案。源码定义的跨平台语料已在原生 Windows 与 Ubuntu/WSL2 真实重建并通过：21 个不同 task、20 个 reviewed task、1 个决定后完成闭环、3 个负路径、0 个无效 run。自动 fixture 明确不冒充人类；真实操作者决定体验与复盘已经完成。GitHub Actions matrix 已定义，但远端运行仍需在明确授权提交后取得状态，不属于本地完成证据。短期执行顺序与证据见 [短期基础设施开发计划](SHORT_TERM_INFRASTRUCTURE_PLAN.md)。

## Phase 1 — 工程 MVP

依赖：Phase 0 闭环稳定并完成至少 20 个内部样例任务复盘。

- [x] PostgreSQL 状态存储和迁移机制；
- [x] 上下文包选择、版本、大小预算和内容哈希；
- [~] 制品注册表和对象存储接口（提供方无关协议与本地内容寻址 adapter 已完成；远程对象存储、权限和保留策略待实现）；
- [ ] Dagger 构建/测试定义；
- [~] 基于风险、错误类型和预算的有界重试（预算预留/扣减/超限取消、attempt/repair 上限已完成；自动修复编排待实现）；
- [~] 小时级任务本地持久控制面（provider-neutral start identity、多维 hard/soft/observe/unavailable
  预算、append-only SQLite event/checkpoint、lease orphan 接管和显式 retry，以及 API 重启可定位的
  TaskManager 本机 Codex worker、运行级 worktree 与原始证据已完成；长任务服务与 TaskManager run 的
  统一 lease/checkpoint、机器重启恢复、后台调度与共享事务装配待实现）；
- [~] OpenTelemetry 统一运行层级（provider-neutral sink、官方 API adapter 与无敏感标量事件已完成；生产 exporter 和跨进程 trace 传播待实现）；
- [ ] Langfuse 或 Phoenix 二选一的观测验证；
- [ ] Promptfoo 的模型/提示回归套件；
- [x] 本地认证项目仪表板、证据面板和审批状态视图；
- [~] TaskManager 聚焦 UI（当前 App 已激进收束为单屏任务图：左 2/3 React Flow DAG，右 1/3 同时显示节点详情与 Agent 对话；legacy route、Backlog/总日志 tab 和常驻治理控件已退出装配；下一步强化当前节点状态和对话调整闭环）；
- [~] 协作式任务编排与执行（计划讨论/固化、source-pinned target、run 装配、持久 Codex、确定性 validation、源码 review/checkpoint、最终 evidence-bound human gate、真实 11-node 完成证据和 Anygine source/toolchain 内容绑定已具备；默认 target 已切换为 `brave-new-world-anygine-app-v1`；下一步是单活节点串行 coordinator 与重启恢复）；
- [x] 跨平台开发环境初始化入口（仓库 venv、固定校验的用户级 Node LTS、lockfile UI install、只读 JSON 检测与离线/分组件模式）；
- [ ] OpenHands 适配器的有界 PoC。

退出条件进展：SQLite/PostgreSQL 已证明重启后状态和证据不丢失；scripted 与 fake-Codex adapter 已在完全相同任务/验证契约下形成确定性比较；使用者可在认证界面查看多运行状态、证据和审批结果，并在认证审查页创建决定。第二个真实付费执行器和远程审批平台仍是后续产品能力，不能由测试替身冒充。

## Phase 2 — 图形、仿真与训练领域验证

依赖：已有真实 C++/Python 工程任务和可重复基线。

- [ ] C++ 编译矩阵、静态分析和 sanitizer 配置；
- [ ] shader 编译、验证层、headless capture 与图像差分；
- [ ] 固定种子仿真回放、不变量和数值容差；
- [ ] 性能、内存和 VRAM 阈值；
- [ ] 训练烟雾测试、配置/数据/检查点血缘；
- [ ] 按实测成功率选择模型和执行器；
- [ ] 架构决策和任务上下文联动；
- [ ] 高风险任务的独立评审规则。

退出条件：至少一种图形或仿真任务可以用领域证据自动阻止错误补丁进入合并评审。

## Phase 3 — 持久与分布式执行

小时级任务需求已经出现，因此先用现有 SQLite/artifact/lease 验证本地控制面契约，不提前增加
分布式依赖。仅在服务级调度、天级任务、远程 GPU 或并行训练有退出证据后引入：

- [ ] Temporal 持久工作流；
- [ ] Ray 仿真/训练任务；
- [ ] 远程 GPU worker 与硬件指纹；
- [ ] 对象存储、配额、调度和恢复；
- [ ] Kubernetes 与 Argo/Flyte 的有界评估。

退出条件：长任务可从故障中恢复，GPU/训练制品可追溯，控制平面不重复实现集群调度器。

## Phase 4 — 多项目平台

- [ ] 多仓库与跨项目依赖；
- [ ] RBAC、团队和租户隔离；
- [ ] 可复用工作流/验证模板；
- [ ] 组织级方法库和历史失败集；
- [ ] 提供方组合管理与灰度发布；
- [ ] 仅在独立 Agent 服务确有互操作需求时评估 A2A。

## 接下来三个迭代

### 迭代 A：UI 收束与可观察当前节点

1. [x] 移除全局 workbench 导航和 legacy route，根入口只装配 TaskManager；
2. [x] 固定左 2/3 DAG、右 1/3 节点详情与 Agent 对话的单屏结构；
3. [ ] DAG 默认突出 running/blocked/next-ready 节点，详情先显示当前反馈；
4. [ ] 强化围绕所选节点的对话调整、候选图 diff 与焦点保持；
5. [~] 已用真实 API 数据完成 1440×1000 浏览器检查；待加入自动交互/截图回归。

### 迭代 B：持久串行 coordinator

1. [ ] 定义 provider-neutral coordinator intent/cursor/lease，不新增状态所有者；
2. [ ] 确定性选择唯一 next-ready node，同一 run 至多一个自动副作用；
3. [ ] 按 node kind 路由 Agent、validation 或 human wait，并在每一步后刷新投影；
4. [ ] 覆盖 API/worker/机器重启、重复 tick、失败、阻塞、取消和显式 retry；
5. [ ] 保留所有权限、verification、独立 review/approval 与最终 completion gate。

### 迭代 C：Anygine 小 App 真实闭环

1. [x] BraveNewWorld 清空旧当前内容并发布 `bnw-anygine-0` 基线；
2. [x] 建立 public Anygine build-tree consumer、`hello-task`、真实 C++ build 与 3-frame Vulkan smoke；
3. [x] 为 run workspace 建立固定 Anygine checkout/toolchain 的内容绑定、前后漂移检查 attachment；
4. [x] 将真实 build/CTest/GPU smoke 作为 source-controlled validation checks，并在隔离 worktree 通过真实部署验证；
5. [ ] 选择一个具体小 App，经 TaskManager 串行完成完整规划、执行、反馈和最终人工门禁。

## 风险登记

| 风险 | 早期信号 | 当前缓解 |
|---|---|---|
| 框架对象侵入领域模型 | 公开 API 出现厂商类 | 适配器边界和 ADR 审查 |
| Agent 叙述被当作完成证据 | 无测试也进入完成态 | 状态机完成门禁 |
| 审计记录可被静默改写 | 历史状态无法重放 | 追加日志与哈希链 |
| 过早基础设施化 | 尚无真实任务就引入集群 | 阶段退出条件 |
| 沙箱形同虚设 | 共享目录、长期密钥、开放网络 | 一任务一工作区和最小权限设计 |
| 模型使用量失控 | 单任务 token 与缓存输入显著增长 | task/runbook 显式 token、费用、上下文和尝试上限；执行前预留、运行中扣减、超限持久化后取消 |
| 验证只覆盖通用 Web 任务 | GPU/仿真缺陷无法复现 | Phase 2 领域验证配置 |

说明：Phase 0 哈希链用于发现意外或局部篡改，不防御有权重写整个日志的主体。Phase 1 已增加事务存储和认证写入；生产阶段仍需要密钥托管、备份、保留策略与外部审计锚点。
