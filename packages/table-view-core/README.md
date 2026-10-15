# @omni/table-view-core

通用 table-view 设施的共享核：**契约 + 唯一相等语义 + 公式忠实 diff + 审批 hash + round-trip 校验 harness**。

计划真源：`omnicompany/docs/plans/_cross/[2026-08-18]TABLE-VIEW-FACILITY/plan.md`（§3.1 契约口径）。
消费方：spark-workshop 的 `/tables` 页、demogame-config portal 的表格页签。本包**不含任何 demogame/Spark 特化路径**——格式读写走插件，业务类型系统可整体替换。

## 契约（contract/*.schema.json）

- **TableDoc**（`omni.table-doc/v1`）：来源无关的表模型。Cell 四值：`v` 投影/导出值、`sv` 源侧原始值、`f` 公式原文、`fv` 公式缓存值。公式必须忠实携带。
- **TableChangeSet**（`omni.table-changeset/v1`）：CRUD 原子；`current*` 一律由核心从 TableDoc 覆写（杜绝编造当前值）；Create 撞 key / Update 缺 key / (key,field) 重复 → fail closed。
- **TableDiff**（`omni.table-diff/v1`）：主键对齐；change kind 四类 `value / formula / formula-result / source`。

## 不变式

1. **相等语义只有一处**：`typesys.js` 的 `equals(a,b,type)`。数值跨 int/float 相等、不与字符串互转、布尔不等于 0/1、NaN 抛错。diff/期望闸/round-trip 全走它。
2. **期望 diff 不手推**：`expectedDiff(doc, cs) = semanticDiff(doc, applyChangeSet(doc, cs))`——期望与实际共用同一把尺子。
3. **导出放行 = `diffsExactlyEqual(expected, actual).ok`**（"表格完全正确之前不导出"的机器化）。
4. **插件可用 = 过 `verifyRoundTrip`**：read→write→read 语义定点（含公式层），丢公式的 writer 会被 harness 抓住。

## IO 插件约定

进程无关：reader = 产出 TableDoc JSON 的任何程序/函数；writer = 消费 TableDoc(+ChangeSet) 写原生格式。跨语言（如 demogame 侧 Python 的 openpyxl/COM reader）走 JSON over stdio/文件，manifest `{id, formats[], direction[], capabilities[], version}`。

## 测试

```
node test/core-smoke.mjs
```
