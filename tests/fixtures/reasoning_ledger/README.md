# Reasoning Ledger Fixtures

本目录存放推理账本的版本化 fixture。它们不是运行时数据，而是未来 schema validator、索引器、迁移脚本和回归测试读取的最小判别样本。

放这里的原因：

- 需要进入 Git，避免 ignored `data/` 导致关键样例丢失。
- 需要被测试直接读取。
- 体积应小，内容应稳定，语义应清楚。

不放这里的内容：

- 大型运行日志、trace、LLM 全量 transcript。
- 还没确定是否保留的临时草稿。
- 某个服务运行时自然产生的私有数据。

这些内容仍应进入 `.omni/sandbox/drafts/`、`data/services/...`、`data/workspaces/...`，或以后建立的本地只读/本地 git 归档。

## Cases

| Case | 语义 | 历史别名 |
|---|---|---|
| `cases/explicit_fail_edge_requirement_falsified/` | “Team 必须有显式 FAIL edge”这条过窄假设被反例证伪，并升级为“必须有完整错误处理路径” | `H-034`, `H-038` |
