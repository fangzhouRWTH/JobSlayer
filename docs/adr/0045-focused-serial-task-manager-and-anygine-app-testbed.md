# ADR-0045：中期主线收紧为串行 TaskManager 与 Anygine 小 App 测试床

- 状态：Accepted
- 日期：2026-09-01
- 取代范围：ADR-0038 中首个悬架 target 的当前产品选择；不改写其历史运行证据

## 背景

TaskManager 已经证明计划固化、run 装配、Codex 执行、validation、独立源码审查/审批、隔离检查点
和最终完成门禁能够闭合真实 11-node DAG。但完整闭环依赖操作者逐个点击离散命令，UI 同时展示大量
低频治理细节。继续扩张通用工作台或在旧 BraveNewWorld 网页仿真产品上增加功能，不利于验证真正的
任务编排、追踪、执行和反馈产品。

Anygine 已提供并真实验证 external build-tree consumer 边界，适合让 BraveNewWorld 以连续的小规模
原生 App 提供快速而真实的 C++/Vulkan/UI 工程任务。

## 决定

1. 中期产品退出条件只包含精简 TaskManager UI、单活节点串行 coordinator、统一反馈投影和一个
   BraveNewWorld/Anygine 小 App 的真实闭环；外围 workbench、第二执行器和分布式基础设施后置。
2. coordinator 只持有 intent/cursor/lease 并调用既有 application commands。它不拥有 task state，
   不直接修改节点，不绕过 `WorkflowKernel`、RBAC、verification、独立审批或完成门禁。
3. 一个 run 任意时刻最多允许一个 coordinator 发起的外部副作用。依赖满足时按 finalized DAG 的稳定
   顺序选择 next-ready node；human gate 停止等待授权决定，失败/阻塞停止等待显式 retry/cancel。
4. BraveNewWorld 当前内容替换为固定 Anygine public build-tree consumer；旧网页/机电模拟 refs 和脏
   worktree 在删除前先离线归档。远端使用普通 fast-forward 内容替换，不重写 Git 历史。
5. 默认 target 改为 `brave-new-world-anygine-app-v1`，固定 `bnw-anygine-0`。旧 target 文件退出当前
   checkout；已装配 run 继续从自身不可变 `execution_binding` 读取，不能依赖今天的 registry。
6. 当前 portable validation profile 只允许 `./bnw contract`。真实 Anygine build/Vulkan smoke 已证明
   新基线可运行，但在 run workspace 具备固定 engine/toolchain 的只读 dependency attachment 前，
   后续任务不能用 contract check 冒充真实构建完成。

## 后果

- 产品面和下一阶段工作量明显收束，可直接测量“是否自动、串行、可恢复、可观察”。
- 旧运行历史仍可读取和审计，registry 的产品迁移不会让已完成 run 失去 target 描述。
- 新测试床能提供真实 C++/GPU 任务，同时不会把 JobSlayer 或 BraveNewWorld 变成 Anygine 状态所有者。
- dependency attachment 是下一次真实 App 闭环的明确 blocker；在它完成前只允许规划、源码执行和
  manifest contract 验证，不允许最终宣称 C++ App 已通过完整门禁。
