# JobSlayer benchmark records

此目录将保存外部测试床的任务登记、公开验证配置和运行结果索引，不复制测试床源码。

计划结构：

```text
benchmarks/
└── brave-new-world/
    ├── cases/             # TaskSpec、基线 commit 和公开检查
    └── results/           # 运行与证据索引，不提交大制品
```

真正的保留检查、标准答案、敏感日志和大制品不应提交到 Agent 可访问的公开仓库。它们应位于任务沙箱未挂载的本地/私有存储中，登记内容哈希和受控 URI。

在 BraveNewWorld 产生首个稳定基线前，本目录不创建虚假的 benchmark case。
