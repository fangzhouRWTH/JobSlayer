# BraveNewWorld Anygine 小 App 测试床

## 当前定位

BraveNewWorld 是 JobSlayer 的快速外部工程测试床。它以固定版本的 Anygine 为基础引擎，持续实现
边界清晰、可构建、可运行、可验证的小规模原生 App，用来反复验证：

```text
任务讨论 -> DAG -> 用户固化 -> 串行节点执行 -> build/runtime 证据 -> 反馈 -> 最终人工门禁
```

它不再开发网页端机电设备物理模拟，也不拥有 JobSlayer 的工作流状态。Anygine 仍是独立引擎库，
BraveNewWorld 只消费其 public CMake targets。

## 固定事实

| 项目 | 当前值 |
|---|---|
| 仓库 | `https://github.com/fangzhouRWTH/BraveNewWorld.git` |
| 当前基线 | `d4947e7fdca4f70970c04fcf61221b55afddfb25` |
| 标签 | `bnw-life-game-1`（仅本机，待发布） |
| Anygine 仓库 | `https://github.com/fangzhouRWTH/Anygine.git` |
| Anygine pin | `28b4934c24fdad6b8f45b945a89a6ada51703f5d` |
| 当前 App | `hello-task`、`life-game` |
| portable baseline gate | `./bnw contract` |
| 完整测试床 gate | `./bnw check --engine-root <pinned-worktree>` |

source-controlled 登记为 [`testbeds/brave-new-world.json`](../../testbeds/brave-new-world.json)，
默认 TaskManager target 为 `brave-new-world-anygine-app-v1`：

- task：[`tasks/bnw-anygine-small-app-001.json`](../../tasks/bnw-anygine-small-app-001.json)；
- runbook：[`runbooks/bnw-anygine-small-app-001-codex.json`](../../runbooks/bnw-anygine-small-app-001-codex.json)；
- validation profile：[`validation-profiles/brave-new-world-anygine-app-v1.json`](../../validation-profiles/brave-new-world-anygine-app-v1.json)。

## 引擎接入边界

BraveNewWorld 是顶层 CMake project，以绝对源码路径注册固定 Anygine worktree。当前只允许引擎已经
验证的 build-tree consumer targets：

- `Anygine::Engine`；
- `Anygine::GraphicsVulkan`；
- `Anygine::RendererCore`；
- `Anygine::RuntimeAssets`；
- `Anygine::UI`。

不允许复制 Anygine 源文件、包含 `Private` 目录、修改引擎 checkout、把 first-party Anygine Apps
当作公共 API，或在验证阶段隐式下载依赖。

## 已完成的基线验证

2026-09-01 在固定 Anygine worktree 上完成：

1. engine commit、Git/CMake/CTest 和 Conan toolchain doctor；
2. 冷配置和编译 `BraveNewWorldHelloTask` / `BraveNewWorldBuildAll`；
3. CTest 1/1 manifest contract；
4. RTX 5080 上真实 Vulkan Required validation、public Renderer/UI 初始化和固定 3 帧呈现；
5. 0 个 Vulkan validation error，并输出稳定 success marker。

这些事实证明新基线本身可构建和运行，但不自动证明未来隔离任务的补丁通过相同门禁。

## TaskManager 部署绑定

JobSlayer 现在从 runbook 读取两项源控 attachment requirement：

- `anygine-source`：固定 commit/origin、clean worktree 与确定性 Git archive SHA-256；
- `anygine-conan-toolchain`：固定生成目录 SHA-256，只向命令暴露其 `conan_toolchain.cmake`。

operator 使用 `--task-manager-dependency-attachment ID=PATH` 提供本机路径；本地绝对路径不写入
task/profile。图形 smoke 所需 `DISPLAY`/`WAYLAND_DISPLAY`/`XDG_RUNTIME_DIR` 必须用独立
`--task-manager-validation-environment` 显式提供，且只有 runbook allowlist 中的非敏感名称可接受。

2026-09-01 的真实隔离 validation 结果：

| check | 结果 | 用时 |
|---|---|---:|
| `./bnw contract` | passed / exit 0 | 64 ms |
| `./bnw test --jobs 4` | passed / exit 0 | 65,777 ms |
| `./bnw run --jobs 4` | passed / exit 0 | 1,416 ms |

该次 run 同时证明 2/2 attachment identity 与 run binding 一致，命令后 worktree clean、
changed paths 为空。三份 command result、workspace inspection 和 dependency inspection 都以内容哈希证据产生。

当前 local runner 通过执行前/后/采证时重新计算身份来拒绝 attachment 写入，尚未使用
namespace/ACL 强制只读 mount；因此仅允许源控、精确 argv 的受信任 validation commands。
另外，toolchain 所引用的 Conan package cache 还没有独立 closure manifest，当前证据是真实构建证据，
不是 hermetic/reproducible-build 声明。

## 旧方向与恢复

旧 Python/browser、滤波和悬架工作已退出当前 checkout。远端通过普通 fast-forward 内容替换保留 Git
历史，没有 force-push。删除旧本地 refs/worktrees 前创建了完整 Git bundle 和 worktree tar，位置：

`/home/fangzhou/projects/JobSlayer/TestProjects/Archive/BraveNewWorld-pre-anygine-20260901`

归档哈希和删除范围记录在 JobSlayer 与 BraveNewWorld 各自的追加式开发日志中。旧证据只用于历史
审计，不再作为当前 target 或产品方向。
