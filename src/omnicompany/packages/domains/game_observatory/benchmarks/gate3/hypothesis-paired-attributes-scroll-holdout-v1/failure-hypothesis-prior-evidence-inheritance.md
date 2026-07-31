# Gate 3 失败假设：引用既有事实时来源没有自动继承

## 观察

`hypothesis-run-03` 的目标、可见线索、swipe 几何与结果均通过外部复核，`generator.prior_fact_id` 也正确；`evidence_ids` 只有当前截图 artifact，遗漏了既有事实中的 `evidence.step.cf890d262045405db24777777d0ad88b`。

## 可证伪假设

来源继承属于确定性数据关系，不应交给语言模型记忆。`propose_probe` 在既有事实精确模式下自动合并引用事实的全部 `evidence_ids` 后，同一冻结输入生成的 ledger 会同时包含当前截图与 Gate 2 路线证据。

## 重跑判据

- `generator.prior_fact_id` 指向被引用事实。
- `evidence_ids` 同时包含当前 artifact 和被引用事实的全部来源。
- 其他已通过字段无回归。