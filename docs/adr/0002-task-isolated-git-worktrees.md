# ADR-0002：任务隔离的 Git worktree

- 状态：Accepted
- 日期：2026-08-07

## 背景

编码 Agent 会修改文件和运行命令。若多个任务共享同一个可写 checkout，补丁、构建产物和失败清理会互相污染，也无法可靠说明修改来自哪个基线。JobSlayer Phase 0 的下一个门槛是建立一个在容器沙箱之前即可测试的仓库隔离边界。

## 决定

每个可写任务使用从固定 commit 创建的独立 Git worktree 和专属分支：

```text
source repository + resolved base commit
    -> configured workspace root / validated workspace id
    -> branch jobslayer/<workspace-id>
    -> inspect changed paths
    -> enforce TaskSpec path policy
    -> collect patch and content hash
```

领域层定义 `WorkspaceSpec`、`WorkspaceManifest`、`WorkspaceInspection` 和 `WorkspacePatch`。`WorkspaceManager` 是提供方无关协议；本地 Git CLI 实现在 adapter 层。

实现通过参数数组调用 Git，不使用 shell。创建前解析并固定 commit，拒绝已存在的目标路径或分支。清理只允许操作由管理器根目录和合法 workspace ID 推导、且仍被 Git 登记的工作区；默认拒绝清理脏工作区，不自动删除任务分支。

修改路径必须在补丁收集前与 `TaskSpec` 对照：允许路径是白名单，禁止路径具有更高优先级。未跟踪文件也属于修改范围。

## 理由

- Git 原生支持低成本共享对象库和独立工作树；
- 固定 commit 提供可重复基线；
- 不复制整个仓库，适合大型工程；
- 独立分支便于生成合并提案和人工检查；
- 协议边界允许未来替换为远程 workspace、容器卷或托管沙箱。

## 安全边界

Worktree 只解决文件工作区隔离，不是命令安全沙箱。它不限制进程、网络、CPU/GPU、密钥或对工作区外路径的访问。真实 Agent 必须在后续受限执行器或 OCI 沙箱中运行。

Git 仓库本身仍被视为不可信输入。未来执行 hook、submodule、LFS 或构建命令前必须另行制定权限策略。

## 后果

- 本地必须安装 Git；
- 同一 workspace ID/分支不能重复使用；
- 脏工作区需要先保存证据并由更高层明确处理，不能静默删除；
- 多控制器进程仍需要外部锁或事务存储避免同时分配同一 ID；
- 任务结束后分支会保留，需要后续生命周期/保留策略。

## 替代方案

- 完整复制仓库：简单但浪费空间，且大型资产成本高。
- 直接在主 checkout 修改：拒绝，缺少隔离与归属。
- 立即要求容器或远程 VM：暂缓；它们最终需要，但不能替代 Git 基线与补丁模型。
- 自动强制删除脏工作区和分支：拒绝，可能丢失未登记制品和失败证据。

## 退出策略

外部执行器只依赖 `WorkspaceManager` 和领域清单，不依赖 Git 命令或 `.git` 布局。未来可以新增容器化或远程实现，并用相同契约与合规测试替换本地适配器。
