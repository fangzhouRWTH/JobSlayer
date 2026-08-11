# ADR-0013：跨平台本地控制平面与平台适配边界

- 状态：Accepted
- 日期：2026-08-10

## 背景

原 Phase 0 实现虽然以 Python 和 Git 为主，却把仓库入口、虚拟环境路径、
进程组终止、Git patch stdin、文件 URI 与只读权限都隐式建立在 POSIX 行为上。
在原生 Windows 上，`./jobslayer` 不会可靠执行，`os.killpg` 不存在，文本模式
会向 patch 写入 CRLF，硬链接共享的只读属性还会阻止临时文件清理。这意味着
“必须使用 WSL”成为了偶然实现约束，而不是产品决定。

JobSlayer 的领域契约、状态机、权限和证据门禁不应依赖操作系统。平台差异应
留在仓库入口与执行 adapter 边界，同时不得为了方便而改变受治理命令或证据
字节。

## 决定

1. 保留 POSIX `./jobslayer`，新增原生 Windows `jobslayer.cmd`；两者只选择
   Python、bootstrap `src` 并进入同一个 `jobslayer.launcher:main`。
2. Python 根入口依次识别 `.venv/Scripts/python.exe` 和
   `.venv/bin/python`；显式 `JOBSLAYER_PYTHON` 缺失时必须失败，不得回退。
3. 在 `jobslayer.execution` 内集中平台进程监督：POSIX 使用新 session 与
   process-group signals，Windows 使用新 process group、`CTRL_BREAK` 和
   `taskkill /T /F`。adapter 只消费统一函数，不把平台对象放入领域模型。
4. Codex executable 接受非空 argv 前缀，使测试和受控调用可明确使用
   `python script.py`，默认生产命令仍为 `codex`。
5. Git patch 通过二进制 stdin 重放，Git 空设备使用 `os.devnull`；源控
   `.diff` 禁止行尾转换，POSIX launcher 强制 LF。
6. 文件 URI 使用标准平台路径转换。POSIX 继续用文件模式做本地只读加固；
   Windows 不把 `chmod` 当作不可变保证，仍以内容地址和读取时哈希复核为真相。
7. 外部测试床 argv 不按宿主系统隐式翻译。某个 profile 登记 `./bnw` 时，
   原生 Windows 执行仍需测试床提供对应入口或使用 POSIX 兼容环境。

## 理由

- 控制平面语义保持提供方和平台无关；差异集中在可替换 adapter/launcher。
- 二进制 patch I/O 和固定 EOL 规则保护哈希、tree 与恢复判断，不用“文本看起来
  一样”替代证据一致性。
- 显式保留外部命令的平台要求，避免控制平面偷偷改写验证规则。
- 不新增运行时依赖，也不提前引入容器或 Windows Job Object 基础设施。

## 后果

- 原生 Windows 可以创建 `.venv`、运行统一 `check`、执行临时 Git 闭环并
  测试 Codex adapter 生命周期。
- Windows 的进程树清理是本地监督能力，不是对恶意子进程的安全边界；生产
  隔离仍需要后续 OCI/VM adapter。
- Windows 文件只读位不承担制品不可变语义；本地攻击者仍可重写整个存储，
  这与 Phase 0 哈希链的既有威胁边界一致。
- 未提供 Windows 命令的外部测试床仍可合法保持 POSIX-only，且必须在运行前
  明确暴露，而不是由 JobSlayer 猜测替代命令。

## 备选方案

- **规定只能使用 WSL**：实现成本最低，但把产品无关的偶然约束固化进开发流。
- **在领域模型中加入操作系统分支**：能表达多套命令，但会污染当前稳定契约；
  在出现真实多平台测试床需求前不采用。
- **立即引入容器或 Windows Job Object**：能加强隔离/清理，但超出当前路线图
  退出条件，也不能替代本次入口和字节稳定问题。

## 复审与退出

当真实 Windows 测试床需要平台专属验证命令时，新增 ADR 设计显式、版本化的
命令选择契约；不得在 adapter 内按系统静默换命令。当本地 Windows 进程树
清理出现无法回收的实测样例，评估 Job Object 或外层 worker，并以确定性
子进程逃逸测试作为引入条件。
