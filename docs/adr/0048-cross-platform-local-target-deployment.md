# ADR-0048：跨平台绑定本地目标依赖与验证命令

- 状态：Accepted
- 日期：2026-09-03
- 修正：[ADR-0013](0013-cross-platform-local-control-plane.md)、
  [ADR-0014](0014-unified-cli-and-process-supervisor-interface.md) 的“外部 argv 尚无 Windows 实现”边界
- 推进：[ADR-0046](0046-content-bound-local-dependency-attachments.md)

## 背景

BraveNewWorld 已发布 Anygine 小 App 基线，但首次在原生 Windows 重新部署时发现三类问题：

1. `git archive` tar 字节会受 Git/平台实现影响，同一 commit 在 Linux/Windows 得到不同 SHA-256；
2. Conan 生成目录包含主机平台和绝对 cache 路径，不能用一个 Linux 目录哈希冒充所有部署；
3. validation profile 只登记 `./bnw`，虽然仓库已有 `bnw.cmd`，受治理 runner 仍不能明确选择 Windows
   命令。

## 决定

1. Git checkout attachment 改用带版本域分隔的 `git ls-tree --full-tree -r -z` 结果计算 SHA-256。
   摘要绑定 Git mode/type/object/path，不再绑定 tar 容器字节；固定 revision、origin 和 clean tree
   检查保持不变。
2. attachment 明确区分 `source_pinned` 与 `run_pinned`。前者继续要求源控期望哈希；后者只允许
   非 Git、源控列出的主机平台，在 run 装配时捕获实际内容哈希，并把平台、期望/观察哈希和本机
   路径固化进不可变 binding。验证前、命令后和采证时仍按首次捕获哈希重检，内容漂移失败关闭。
3. `CommandRule`/`ValidationCheckSpec` 可登记显式 platform argv。领域校验逐平台验证 check 与 policy
   一致；local adapter 只选择当前平台的已登记 argv，不猜测扩展名或改写任意外部命令。
4. Windows 文本投影统一换行为 LF，原始 stdout/stderr 字节数和 SHA-256 继续按未改写字节记录。
5. 外部目标部署就绪性由 `inspect-task-manager-target` 显式验证；确定性单元测试不再把开发机绝对
   目录作为测试 fixture。

## 后果

- 同一 Anygine Git commit 可以在 Windows/Linux 得到相同源码摘要。
- Windows MSVC Conan toolchain 在本次 run 首次装配时固定，后续不能静默变化；它仍是受信任
  build-host prerequisite，不等于 hermetic package closure。
- BraveNewWorld profile 在 POSIX 使用 `./bnw`，在 Windows 使用 `.\bnw.cmd`，两者都由源控
  明确授权并进入原始命令证据。
- 已持久化的旧 attachment 和 validation profile 继续通过默认字段读取；新 run 使用新的 source
  bundle hash，不能与旧 binding 混用。
