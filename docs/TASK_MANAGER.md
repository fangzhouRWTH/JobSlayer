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

当前实现已经闭合到“固化精确 revision → 运行装配 → Kernel 节点状态/反馈投影”，并提供显式启用的
本机 Codex durable executor。默认 CLI 只启用本地追加式 run store；没有执行开关和 `executor`
身份时不会接入外部进程。即使 provider success，也只进入 `Verifying`，不会伪装成完成。

默认 TaskManager 现在显式登记 `brave-new-world-anygine-app-v1`。目标解析器把 runbook、testbed、
task 和 validation profile 四个源文件合成 SHA-256 source bundle，并只读检查本地
`bnw-anygine-0` Git 基线。任务图包含 JobSlayer 内部命令或遗漏 `./bnw contract` 时，目标预检会
阻止固化和 run 装配。现在 runbook 还声明固定 Anygine Git archive 与 Conan toolchain 目录
SHA-256；operator 在部署时绑定本机路径。两项 attachment 就绪后，source-controlled profile
要求 `./bnw contract`、`./bnw test --jobs 4` 和 `./bnw run --jobs 4`，不再以
portable contract 冒充真实 C++ build/Vulkan 证据。

## 当前中期目标

当前不再扩张通用工程工作台，产品退出条件收紧为一个可观察、可恢复的串行 TaskManager 闭环：

1. UI 只保留单屏任务图预览：左侧 2/3 DAG，右侧 1/3 同时显示当前节点详情和 Agent 对话；
2. finalized run 在任意时刻至多有一个自动推进中的节点/外部副作用，并以持久 cursor 恢复；
3. coordinator 依节点 kind 调用 Agent、validation 或 human gate 路径，仍不替代权限和完成决定；
4. 每次启动、观察、验证、审查、失败和阻塞都立即反馈到 DAG、总 Log 和制品入口；
5. 用 BraveNewWorld 的一个小 Anygine App 完成“讨论 → DAG → 固化 → 串行执行 → build/smoke 证据
   → 人工完成门禁”的真实验证。

当前已经具备全部单节点 application/API 命令和完整 11-node 人工推进证据，但没有拥有 lease/cursor
的自动串行 coordinator。当前预览 UI 按 ADR-0047 主动隐藏固化、执行和治理按钮，先验证“图 → 节点
→ 对话”；这些后台能力没有删除或旁路。

## 运行

首次使用可以创建一个本地 planner 身份：

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

另开终端启动 Web 应用：

```bash
sh ./init.sh -- npm --prefix ui-framework run task-manager
```

打开唯一入口 `http://127.0.0.1:4173/`。Windows 使用等价的
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
可定位同一个 provider run；原始 JSONL、stderr 与 terminal result 都注册为证据。这个开关仍不会
自动固化计划、自动启动节点或绕过逐节点授权；自动串行 coordinator 尚未落实。

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

命令终止后还需调用 `observe`、`verify` 并由 Reviewer 接受 passing report，节点
才会满足后继依赖。当前 profile 必须是幂等、非发布型检查；进程在 terminal 结果原子落盘前崩溃时，
恢复可能保守重跑这些命令。attachment 不传给 Codex 实现进程；本地 validator 对其执行
前/后/采证时重新计算哈希并拒绝漂移。当前尚未用 namespace mount 强制主机文件系统只读，
因此这条路径只允许源控、精确 argv 的受信任验证命令，不运行任意 Agent 脚本。

运行数据默认写入 `<state-root>/task-manager-runs/*.jsonl`，worker 状态与 run 级 worktree 默认写入
`<state-root>/task-manager-codex/`；它们与计划 journal 分离。创建运行不调用外部模型；未提供显式
executor 开关或 session 没有 `executor` 角色时，节点执行保持失败关闭。

源码变更节点需要两个彼此独立的短期身份。Reviewer 只执行 `review-source`；Approver 只批准并写入
隔离运行分支检查点：

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

- 顶部任务切换器读取所有最新任务，可创建和刷新任务；
- 左侧约占 2/3，始终显示 DAG；候选图用虚线和 proposed 状态标示；
- 右侧约占 1/3，上半区始终显示所选节点详情，下半区始终显示 Agent 对话；
- 选中 DAG 节点后，详情和对话上下文同步切换到该节点；
- Agent 输出始终先成为候选图，用户可以拒绝或应用；
- Backlog、总 Log、target、固化、执行、验证、review/approval/integration 控件暂不在当前预览 UI
  暴露；相应受认证 API 和 application commands 仍然存在，并继续服从原有权限与证据门禁；
- 当前 App 不再装配 `#/orchestration` 或其他 legacy workbench route。

## API

`task-manager-api` 与旧规划 API 由同一个 loopback server 提供：

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/api/task-manager/session` | 取得认证主体、capabilities 和进程随机提交 token |
| GET | `/api/task-manager/tasks` | 最新多任务摘要，按活动/更新时间排序 |
| GET | `/api/task-manager/targets` | 可用执行目标、固定基线、预算和源包哈希 |
| GET | `/api/task-manager/tasks/{task_id}` | revision-bound 任务、DAG、assessment、Backlog 和完整 Log |
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
| GET | `/api/task-manager/tasks/{task_id}/artifacts` | 读取 plan-bound 规划证据描述符 |
| GET | `/api/task-manager/tasks/{task_id}/artifacts/{artifact_id}` | 哈希验证的有界文本预览 |

除 session bootstrap 外，上述接口要求 `X-JobSlayer-Session`。计划写命令要求
`expected_revision`，运行节点命令要求 `expected_run_revision`；过期写入返回 conflict，不会覆盖新
revision。run assembly 与根范围确认属于 planner 固化流程；start/run-validation/observe/retry/verify 另行要求当前
主体拥有 `EXECUTE_TASK`，accept-review 要求 `REVIEW_IMPLEMENTATION`（本地 `reviewer` role）；仅有
`planner` role 会返回 403。
源码 review 要求 `REVIEW_IMPLEMENTATION`；checkpoint approval 要求 `APPLY_DECISION`；实际 Git
checkpoint 要求 `INTEGRATE_SOURCE` 和显式 CLI opt-in。同一主体不能审查并批准同一 patch。
最终 completion gate 要求 `APPLY_DECISION`，且 Approver 不能是直接依赖的最后 Reviewer。

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

产品收紧见 [ADR-0036](adr/0036-focused-task-manager-product-surface.md)，运行装配决定见
[ADR-0037](adr/0037-plan-bound-task-manager-run-assembly.md)，BraveNewWorld 目标绑定见
[ADR-0038](adr/0038-source-pinned-bravenewworld-execution-target.md)。
源码检查点边界见
[ADR-0042](adr/0042-independent-source-review-and-isolated-run-checkpoint.md)。
validation node 边界见
[ADR-0043](adr/0043-source-bound-deterministic-validation-nodes.md)。
最终完成门禁见
[ADR-0044](adr/0044-evidence-bound-final-completion-gate.md)。
