# TaskManager 聚焦应用

TaskManager 是 JobSlayer 当前主产品面，先收紧到一个可验证闭环：

```text
任务描述
  -> 与规划 Agent 多轮讨论
  -> 候选 DAG（用户应用或拒绝）
  -> 完整度检查
  -> 选择并锁定 execution target ID + source bundle hash
  -> 用户固化精确 revision
  -> 创建绑定 plan revision + 完整 target binding 的执行运行
  -> 普通 task node 逐节点授权、幂等启动与证据反馈
  -> validation node 执行 finalized profile 并保留原始命令证据
  -> 确定性验证、Reviewer 接受或源码集成、最终完成决定
```

当前实现已经闭合到“固化精确 revision → 运行装配 → 持久串行单步推进 → Kernel 节点状态/反馈
投影”，并提供显式启用的本机 Codex durable executor。默认 CLI 只启用本地追加式 run store；没有
执行开关和 `executor` 身份时不会接入外部进程。即使 provider success，也只进入 `Verifying`，
不会伪装成完成。

默认 TaskManager 现在显式登记 `brave-new-world-anygine-app-v1`。目标解析器把 runbook、testbed、
task 和 validation profile 四个源文件合成 SHA-256 source bundle，并只读检查本地
`bnw-anygine-0` Git 基线。任务图包含 JobSlayer 内部命令或遗漏 `./bnw contract` 时，目标预检会
阻止固化和 run 装配。现在 runbook 还声明固定 Anygine Git archive 与 Conan toolchain 目录
SHA-256；operator 在部署时绑定本机路径。两项 attachment 就绪后，source-controlled profile
要求 `./bnw contract`、`./bnw test --jobs 4` 和 `./bnw run --jobs 4`，不再以
portable contract 冒充真实 C++ build/Vulkan 证据。

## 当前中期目标

当前不再扩张通用工程工作台，产品退出条件收紧为一个可观察、可恢复的串行 TaskManager 闭环：

1. UI 使用左缘垂直栏拆分首页、Agent 状态、任务总控、任务编排和具体执行；任务编排仍保持左侧
   2/3 DAG、右侧 1/3 节点详情与 Agent 对话；
2. finalized run 在任意时刻至多有一个自动推进中的节点/外部副作用，并以持久 cursor 恢复；
3. coordinator 依节点 kind 调用 Agent、validation 或 human gate 路径，仍不替代权限和完成决定；
4. 每次启动、观察、验证、审查、失败和阻塞都立即反馈到 DAG、总 Log 和制品入口；
5. 用 BraveNewWorld 的一个小 Anygine App 完成“讨论 → DAG → 固化 → 串行执行 → build/smoke 证据
   → 人工完成门禁”的真实验证。

当前已经具备全部单节点 application/API 命令、完整 11-node 人工推进证据，以及按 ADR-0055 建立的
持久单步 coordinator。它拥有 append-only intent/cursor 与 run-scoped lease，每次 tick 最多调用一个
既有 application command；人工门禁、审查、失败、阻塞和修复态停止。ADR-0056 又为每个当前人工
停顿投影 revision-bound 的处理要求、详细步骤、待审证据、允许决定和禁止动作，并同时呈现在任务图、
节点详情和执行反馈中。ADR-0057 在执行页补齐证据核对、正式决定、追加式反馈与任务绑定只读 Agent；
正式按钮只调用既有治理命令，反馈和 Agent 对话不产生状态转换。ADR-0053 形成 Calm Ops 的深炭灰
三级表面、正文 13–17px 与信息收敛；当前活动 SUID `focused-task-graph@8` 保留全部 stable 决定并
固化人工确认闭环。ADR-0054 把 Agent 页改为与任务
链无关的 Codex Quick Agent：显示 provider 原始额度窗口与刷新时间，并由本机 `model/list` 驱动模型、
effort、速度和能力信息。

## 运行

普通 Windows/Linux 使用只需从仓库根启动一个 Python 脚本：

```powershell
py -3 start.py
```

```bash
python3 start.py
```

入口会检查并按需初始化 Python/Node/UI/桌面依赖，创建或复用受保护的本地 key，签发仅含
`planner + quick-agent + reviewer + approver` 角色的临时 session，连接本机已登录 Codex 的 Quick
Agent 与任务绑定只读辅助，依次启动并健康检查 TaskManager API 与 Vite proxy，再把入口
`http://127.0.0.1:4173/#/home` 装入独立 WebView2（Windows）或 Qt（Linux）窗口。关闭窗口会回收两个
后台进程树并删除本次临时 session；日志保留在 `.jobslayer/desktop/logs/`。

常用诊断模式：

```bash
python3 start.py --check
python3 start.py --smoke-test
python3 start.py --headless
```

Linux 桌面模式需要 `DISPLAY` 或 `WAYLAND_DISPLAY`；`--smoke-test` 与 headless 模式不创建窗口，
也不要求图形会话。API/Vite 的固定 loopback 健康检查显式绕过外部 HTTP 代理，避免代理环境中的
宽泛 `NO_PROXY` 写法造成误判。默认便捷入口会连接 Quick Agent adapter，但只有用户在 Agent 页发送
消息或在人工指导卡主动询问时才调用模型；它不会打开 durable task execution、validation 或
integration adapter。默认同一桌面主体即使兼有 reviewer/approver role，也不能绕过后端的源码
Reviewer/Approver 独立性校验。

需要显式选择身份、adapter、模型或 dependency attachment 时，继续使用高级手工入口。首先创建
一个本地 planner 身份：

```bash
./jobslayer create-local-identity-key .jobslayer/identity/task-manager-key.json
./jobslayer issue-local-identity-session \
  --key .jobslayer/identity/task-manager-key.json \
  --subject-id local-planner \
  --display-name "Local planner" \
  --role planner \
  --lifetime-minutes 480 \
  --output .jobslayer/identity/task-manager-session.json
```

启动默认离线、确定性的 planning fixture：

```bash
./jobslayer task-manager-api \
  --identity-session .jobslayer/identity/task-manager-session.json \
  --identity-key .jobslayer/identity/task-manager-key.json
```

如需在手工入口启用 Agent 页的容量与独立控制台，签发 session 时增加 `--role quick-agent`，API 增加：

```bash
./jobslayer task-manager-api \
  --identity-session .jobslayer/identity/task-manager-session.json \
  --identity-key .jobslayer/identity/task-manager-key.json \
  --allow-quick-agent \
  --quick-agent-model gpt-5.6-sol \
  --quick-agent-reasoning-effort xhigh \
  --quick-agent-timeout-seconds 1800
```

`discuss` turn 只读；`execute` turn 仅可写当前仓库，两者默认禁网、拒绝自动审批。容量每 30 秒刷新，
显示的是 Codex 返回的百分比窗口与 `resetsAt`，不是美元或 token 估算。Quick Agent 不记录为任务 DAG、
Run、验证或完成证据。

另开终端启动 Web 应用：

```bash
sh ./init.sh -- npm --prefix ui-framework run task-manager
```

手工模式打开 `http://127.0.0.1:4173/`。Windows 使用等价的
`.\jobslayer.cmd task-manager-api ...` 和
`.\init.cmd -- npm --prefix ui-framework run task-manager`。

需要调用本机已登录 Codex 讨论计划时，必须显式授权外部 planning adapter 并固定模型/推理等级：

```bash
./jobslayer task-manager-api \
  --identity-session .jobslayer/identity/task-manager-session.json \
  --identity-key .jobslayer/identity/task-manager-key.json \
  --planning-agent codex \
  --allow-external-planning-agent \
  --codex-model gpt-5.6-sol \
  --codex-reasoning-effort xhigh \
  --codex-timeout-seconds 900
```

该调用只生成待用户决定的规划 proposal。它不自动应用、不执行任务，也不从 ChatGPT 月订阅推断
虚假的美元余额。长任务预算与恢复口径见[长任务执行与恢复](LONG_RUNNING_EXECUTION.md)。

固化后的 task node 执行还需要一个同时具备 `planner` 与 `executor` 角色的短期 session，并在启动
API 时单独启用 durable executor：

```bash
./jobslayer issue-local-identity-session \
  --key .jobslayer/identity/task-manager-key.json \
  --subject-id local-task-operator \
  --display-name "Local task operator" \
  --role planner --role executor \
  --lifetime-minutes 480 \
  --output .jobslayer/identity/task-manager-operator.json

./jobslayer task-manager-api \
  --identity-session .jobslayer/identity/task-manager-operator.json \
  --identity-key .jobslayer/identity/task-manager-key.json \
  --planning-agent codex \
  --allow-external-planning-agent \
  --allow-external-task-execution \
  --allow-task-manager-local-validation \
  --task-manager-dependency-attachment \
    anygine-source=/home/fangzhou/projects/Anygine/Anygine_JobSlayer \
  --task-manager-dependency-attachment \
    anygine-conan-toolchain=/home/fangzhou/projects/Anygine/Anygine/build/conan \
  --task-manager-validation-environment "DISPLAY=${DISPLAY}" \
  --task-manager-validation-environment "XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR}" \
  --codex-model gpt-5.6-sol \
  --codex-reasoning-effort xhigh \
  --codex-timeout-seconds 900
```

executor 会从目标固定基线创建 run 级独立 worktree，并以持久 start key 启动独立 worker。API 重启后
可定位同一个 provider run；原始 JSONL、stderr 与 terminal result 都注册为证据。连接 executor、
validator 或 integrator 时，API 同时装配持久串行 coordinator；具体执行页的“推进一步”按当前 run
revision 调用一次 tick。这个开关仍不会自动固化计划、在后台无限循环或绕过逐节点授权。

`--allow-task-manager-local-validation` 是与外部 Agent 执行分离的显式开关。它只读取 finalized target
中的 validation profile，在当前 clean 隔离 run worktree 上通过命令策略运行检查；不会把 validation
node 发送给 Codex。执行前可先用下列只读入口验证 baseline、2/2 attachment、图形会话和
source-bundle hash：

```bash
./jobslayer inspect-task-manager-target \
  runbooks/bnw-anygine-small-app-001-codex.json \
  --target-id brave-new-world-anygine-app-v1 \
  --dependency-attachment anygine-source=/home/fangzhou/projects/Anygine/Anygine_JobSlayer \
  --dependency-attachment anygine-conan-toolchain=/home/fangzhou/projects/Anygine/Anygine/build/conan \
  --validation-environment "DISPLAY=${DISPLAY}" \
  --validation-environment "XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR}"
```

原生 Windows 使用上级 `TestProjects` 中已同步的 BraveNewWorld、固定 Anygine detached worktree 和
Windows Conan 输出；profile 会显式执行 `.\bnw.cmd`：

```powershell
.\jobslayer.cmd inspect-task-manager-target `
  runbooks\bnw-anygine-small-app-001-codex.json `
  --target-id brave-new-world-anygine-app-v1 `
  --dependency-attachment anygine-source=D:\projects\Anygine\Anygine_JobSlayer `
  --dependency-attachment anygine-conan-toolchain=D:\projects\Anygine\Anygine_JobSlayer\build\conan-windows-debug
```

Conan 生成目录采用 `run_pinned`：源控限定支持的平台，本次装配捕获精确内容哈希，之后每次验证
继续重检并拒绝漂移。Git source 仍是 `source_pinned`，使用跨平台稳定的 Git tree 摘要。

命令终止后还需继续调用“推进一步”，由 coordinator 依次执行 `observe`、`verify`，再由 Reviewer
接受 passing report，节点才会满足后继依赖。当前 profile 必须是幂等、非发布型检查；进程在 terminal 结果原子落盘前崩溃时，
恢复可能保守重跑这些命令。attachment 不传给 Codex 实现进程；本地 validator 对其执行
前/后/采证时重新计算哈希并拒绝漂移。当前尚未用 namespace mount 强制主机文件系统只读，
因此这条路径只允许源控、精确 argv 的受信任验证命令，不运行任意 Agent 脚本。

运行数据默认写入 `<state-root>/task-manager-runs/*.jsonl`，worker 状态与 run 级 worktree 默认写入
`<state-root>/task-manager-codex/`；它们与计划 journal 分离。创建运行不调用外部模型；未提供显式
executor 开关或 session 没有 `executor` 角色时，节点执行保持失败关闭。

源码变更节点需要两个彼此独立的责任主体。技术 Reviewer 可由 human 或 agent 承担，只能针对
verification-bound 的精确 patch 形成 `review-source` 结论；Approver 必须是 human，只批准并写入
隔离运行分支检查点。HTTP/UI 的 `review-source` 仍使用已认证 reviewer 身份；受治理的 Agent review
通过 application command 写入同一种 `ReviewReport`，不能取得 approval 权限：

```bash
./jobslayer issue-local-identity-session \
  --key .jobslayer/identity/task-manager-key.json \
  --subject-id local-source-approver \
  --display-name "Local source approver" \
  --role approver \
  --lifetime-minutes 60 \
  --output .jobslayer/identity/task-manager-approver.json

./jobslayer task-manager-api \
  --identity-session .jobslayer/identity/task-manager-approver.json \
  --identity-key .jobslayer/identity/task-manager-key.json \
  --allow-task-manager-checkpoint-integration
```

该进程可读取既有 run 并执行 `approve-checkpoint` / `integrate-checkpoint`，但没有 planner、executor 或
reviewer 权限。adapter 只在已有 run worktree branch 上提交已审查的精确 patch；不会 checkout、合并
或修改项目主干，也不会 push/deploy。完成后可重启原 planner/executor/reviewer 进程继续后继节点。

## 界面

- 窗口左缘 84px 垂直栏按固定顺序切换首页、Codex Quick Agent、Backlog/总控、任务编排和具体执行；
  窄屏缩为 54px。活动项同时使用位置条、边框、背景、图标和 `aria-current`，不是颜色单一提示；
- 首页显示 Logo、产品说明、真实任务统计、工作入口和活动 SUID 摘要；
- Quick Agent 页显示 Codex 原始限额窗口、刷新倒计时、模型/工作区边界和独立流式控制台；讨论只读，
  快速执行只写当前仓库且默认禁网，不复制或改变任务状态；
- Backlog/总控页读取全部任务，并显示选中任务的积压和最近追加式事件；
- 任务编排页的左侧约占 2/3，显示 DAG；右侧约占 1/3，上半区显示节点详情，下半区显示 Agent 对话；
- 选中 DAG 节点后，详情和对话上下文同步切换到该节点；
- 具体执行页显示真实 run binding/stage/revision 与节点 Kernel 状态、最新反馈和证据数量；无 run 时
  显示明确空状态；
- 计划或 run 需要人工输入时，相关图节点显示“需要人工处理”；节点详情显示紧凑指导，执行页显示完整
  指导卡，其中包括允许角色/capability、绑定版本、前置要求、编号步骤、待审证据、决定效果和禁止动作；
- 执行页正式决定要求逐项核对证据、填写人工理由并确认动作边界；按钮调用既有 RBAC/revision-bound
  application command。选择调整或暂不批准时只追加反馈，节点继续等待；
- 每张 run 级指导卡带独立任务 Agent 辅助区。它只读解释要求或起草反馈，request/response/error 均
  绑定 task/run/node/guidance/revision 写入 run 哈希链，不能替用户批准；
- 顶部低干扰摘要来自后端选择的活动 SUID binding，显示 scheme/revision 与
  dirty/planned/stable 计数；前端不读取源控描述路径或自行选择方案；
- 外部 UI/UX Pro Max 当前只通过独立 CLI 产生绑定该 SUID 的候选 evidence；浏览器不会直接调用
  provider，建议也不会自动改图、改页面或改变节点状态；
- Agent 输出始终先成为候选图，用户可以拒绝或应用；
- proposal、固化和 run assembly 继续使用编排页既有入口；执行页暴露当前人工门的正式
  scope/review/approval 控件，自动执行、validation 和 integration 仍只经明确配置的后端能力；
- 五个稳定展示 hash 是 `#/home`、`#/agent`、`#/control`、`#/orchestration`、`#/execution`；旧
  `#/task-manager` 兼容映射到任务编排，不会恢复 legacy mock workbench。

## API

`task-manager-api` 与旧规划 API 由同一个 loopback server 提供：

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/api/task-manager/session` | 取得认证主体、capabilities 和进程随机提交 token |
| GET | `/api/task-manager/ui-design` | 取得后端唯一活动的语义 UI description 与状态计数 |
| GET | `/api/task-manager/quick-agent/capacity` | 读取 Codex 原始限额窗口；`?refresh=1` 强制重新查询 |
| GET | `/api/task-manager/quick-agent/models` | 读取本机可用模型及 effort、service tier、输入模态和 Agent runtime；`?refresh=1` 强制查询 |
| GET | `/api/task-manager/quick-agent/session` | 读取当前进程内独立会话与规范化流式事件 |
| POST | `/api/task-manager/quick-agent/messages` | 以显式 `discuss` / `execute` 模式及可选 `model`、`reasoning_effort`、`service_tier` 发起一轮 |
| POST | `/api/task-manager/quick-agent/cancel` | 中断活动 turn，不改变任务状态 |
| POST | `/api/task-manager/quick-agent/new-session` | 清空 UI 投影并解除 thread 指针，不删除 Codex 历史 |
| GET | `/api/task-manager/tasks` | 最新多任务摘要，按活动/更新时间排序 |
| GET | `/api/task-manager/targets` | 可用执行目标、固定基线、预算和源包哈希 |
| GET | `/api/task-manager/tasks/{task_id}` | revision-bound 任务、DAG、assessment、Backlog、完整 Log 与当前 `human_actions` 指导 |
| POST | `/api/task-manager/tasks` | 创建任务并记录首个 Agent 候选图 |
| POST | `/api/task-manager/tasks/{task_id}/messages` | 追加讨论并记录新候选图 |
| POST | `/api/task-manager/tasks/{task_id}/proposal/apply` | 用户应用精确 proposal |
| POST | `/api/task-manager/tasks/{task_id}/proposal/reject` | 用户拒绝精确 proposal |
| POST | `/api/task-manager/tasks/{task_id}/target` | 选择并锁定 target ID 与当前 source bundle hash |
| POST | `/api/task-manager/tasks/{task_id}/finalize` | 用户固化通过 assessment 的 revision |
| POST | `/api/task-manager/tasks/{task_id}/runs` | 为当前 finalized revision 创建唯一精确绑定的 run |
| POST | `/api/task-manager/tasks/{task_id}/runs/{run_id}/nodes/{node_id}/start` | 授权节点并以持久启动键幂等 start/locate |
| POST | `/api/task-manager/tasks/{task_id}/runs/{run_id}/nodes/{node_id}/observe` | 拉取并验证一次 provider feedback |
| POST | `/api/task-manager/tasks/{task_id}/runs/{run_id}/nodes/{node_id}/retry` | 对 failed/blocked 节点显式授权新 attempt |
| POST | `/api/task-manager/tasks/{task_id}/runs/{run_id}/nodes/{node_id}/run-validation` | 按 finalized target profile 授权并幂等运行 validation node |
| POST | `/api/task-manager/tasks/{task_id}/runs/{run_id}/nodes/{node_id}/confirm-scope` | 以 finalized plan 证据确认无依赖根范围门禁 |
| POST | `/api/task-manager/tasks/{task_id}/runs/{run_id}/nodes/{node_id}/verify` | 将 adapter workspace 事实编译为确定性 verification report |
| POST | `/api/task-manager/tasks/{task_id}/runs/{run_id}/nodes/{node_id}/accept-review` | Reviewer 接受无源码差异的阶段性交付物 |
| POST | `/api/task-manager/tasks/{task_id}/runs/{run_id}/nodes/{node_id}/review-source` | Reviewer 接受 verification-bound 精确源码补丁 |
| POST | `/api/task-manager/tasks/{task_id}/runs/{run_id}/nodes/{node_id}/approve-checkpoint` | 独立 Approver 批准隔离运行分支检查点 intent |
| POST | `/api/task-manager/tasks/{task_id}/runs/{run_id}/nodes/{node_id}/integrate-checkpoint` | 幂等提交精确补丁并以集成证据完成源码节点 |
| POST | `/api/task-manager/tasks/{task_id}/runs/{run_id}/nodes/{node_id}/approve-completion` | 独立 Approver 依据最终 passing/acceptance evidence 批准 sink human gate |
| POST | `/api/task-manager/tasks/{task_id}/runs/{run_id}/human-actions/{guidance_id}/feedback` | 追加绑定当前 decision/revision 的人工反馈，不改变节点状态 |
| POST | `/api/task-manager/tasks/{task_id}/runs/{run_id}/human-actions/{guidance_id}/assistant` | 先记录请求，再追加只读 Agent 回答或失败，不执行决定 |
| GET | `/api/task-manager/tasks/{task_id}/artifacts` | 读取 plan-bound 规划证据描述符 |
| GET | `/api/task-manager/tasks/{task_id}/artifacts/{artifact_id}` | 哈希验证的有界文本预览 |

除 session bootstrap 外，上述接口要求 `X-JobSlayer-Session`。计划写命令要求
`expected_revision`，运行节点命令要求 `expected_run_revision`；过期写入返回 conflict，不会覆盖新
revision。run assembly 与根范围确认属于 planner 固化流程；start/run-validation/observe/retry/verify 另行要求当前
主体拥有 `EXECUTE_TASK`，accept-review 要求 `REVIEW_IMPLEMENTATION`（本地 `reviewer` role）；仅有
`planner` role 会返回 403。
源码 review 要求 `REVIEW_IMPLEMENTATION`；checkpoint approval 要求 `APPLY_DECISION`；实际 Git
checkpoint 要求 `INTEGRATE_SOURCE` 和显式 CLI opt-in。同一主体不能审查并批准同一 patch。
无源码差异的 validation deliverable 可由 human 或明确命名的 policy 接受，agent 不得接受；这只确认
确定性证据，不等于最终完成决定。最终 completion gate 要求 `APPLY_DECISION`，且 Approver 必须是
human，也不能是直接依赖的最后 Reviewer。
人工 feedback 要求 `RECORD_DECISION`；任务绑定 Agent 要求独立
`ASSIST_HUMAN_DECISION`（本地 `quick-agent` role）。两者都要求当前 task/run/guidance 与 plan/run
revision 精确匹配；Agent capability 不隐含任何 review、approval 或 execution capability。

## 真相与边界

`TaskManagerService` 是 read/application facade，不是新的状态机。它组合
`TaskOrchestrationService` 的计划记录与 `TaskManagerExecutionService` 的 run 记录。Agent 对话进入
审计日志，但 Agent 不能应用 proposal、固化计划、授权执行或决定完成。当前运行层满足：

1. plan 保存 target ID + source bundle hash，run 输入绑定 finalized `plan_id + revision + record_hash`
   和完整 target binding；
2. 任务转换只通过 `WorkflowKernel.transition`，transition history 随 run snapshot 受哈希链保护；
3. provider side effect 前持久化 `provider_start_key`，adapter 必须 `start_or_locate`；
4. executor 事件规范化，同时原始日志登记为 task/run-bound 制品；
5. validation adapter 只运行 finalized profile 并返回 raw command/workspace facts；检查通过与否由
   TaskManager 编译 report，Agent 或 adapter 都不能替代该决定；
6. provider success 只到 `Verifying`；无源码差异的阶段性交付物需要 passing verification report 与
   授权 reviewer evidence 才能成为 `DeliverableAccepted`，源码任务仍要求 review、approval 与集成证据；
7. 源码任务的审批 intent 在 Git 副作用前持久化；adapter 只向隔离 run branch 写入精确 patch，重试
   校验同一 base/commit/branch/clean tree，不拥有主干合并、push 或部署权限；
8. 规划支线/调整形成新 revision，不改写历史或当前 run 输入。
9. 持久单步 coordinator 只选择并调用下一条 application command；遇到 review、checkpoint approval、
   human gate、repair 或失败即停顿，不取得这些治理决定。
10. `human_actions` 只从上述权威状态派生；它解释谁应按哪些步骤审哪些证据，不授予 capability、执行
    command、持久化第二份状态或替代最终决定。
11. human feedback 与 assistant request/outcome 作为 immutable artifacts 和 node interaction 前缀追加到
    run 哈希链；它们不改变 Kernel transition history，正式按钮仍走第 2 条的唯一状态路径。

产品收紧见 [ADR-0036](adr/0036-focused-task-manager-product-surface.md)，运行装配决定见
[ADR-0037](adr/0037-plan-bound-task-manager-run-assembly.md)，BraveNewWorld 目标绑定见
[ADR-0038](adr/0038-source-pinned-bravenewworld-execution-target.md)。
源码检查点边界见
[ADR-0042](adr/0042-independent-source-review-and-isolated-run-checkpoint.md)。
validation node 边界见
[ADR-0043](adr/0043-source-bound-deterministic-validation-nodes.md)。
最终完成门禁见
[ADR-0044](adr/0044-evidence-bound-final-completion-gate.md)。
持久单步推进与停顿边界见
[ADR-0055](adr/0055-persistent-single-step-task-manager-coordinator.md)。
人工交互指导契约见
[ADR-0056](adr/0056-revision-bound-human-action-guidance.md)。
正式人工决定、追加式反馈与只读辅助边界见
[ADR-0057](adr/0057-governed-human-decision-controls-and-assistance.md)。
