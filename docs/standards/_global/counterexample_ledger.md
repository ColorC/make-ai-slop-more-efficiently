# 反例账本 · 死规则在线监测

> **状态**: 2026-04-30 L1 立 (跟 0 反例铁律配套硬性要求)
> **关系**: `llm_first.md` 原则 1 的实施层 — 0 反例铁律的"持续验证机制"

## 核心要求

**任何死规则触发, 必须同步挂反例警告 hook. 任何一个反例出现, 立即落账 + 触发反思.**

死规则在立档时是"当前样本 0 反例". 但生产数据持续变, 规则随时可能踩反例. 反例账本是规则的在线安全网.

## 死规则 vs LLM 输出

死规则 (硬编码 if/分类/正则/yaml 映射) 必须挂. LLM 输出本身不是死规则, 不必挂同样的反例账本 (LLM 已是动态判断, 错了下次自纠).

但: **LLM 学样本归纳出的规则, 一旦固化为代码 (非 prompt 引导), 就成了死规则, 必须挂账本**.

## 实施 4 要素

每条死规则触发位置必须含:

### 1. 触发记录

```python
# 规则触发即落盘 (经事件总线 / 直接落盘)
rule_trace.record({
    "rule_id": "grid_mount_anchor_v1",          # 全局唯一
    "rule_version": "1",
    "trigger_context": {                        # 触发时上下文 (复现用)
        "module": "iceblock",
        "node_name": "point_3",
        "parent_name": "layout_ice_area",
        # ... 任何能追溯反例的字段
    },
    "rule_output": {"anchor_min": [0, 0], "pivot": [0.5, 0.5]},
    "timestamp": "...",
})
```

### 2. 反例对比机制

反例需要"真值"对比. 真值来源 (按可达性):
- **GT 真值** (跑分场景 — 验证器 fail 反推): 验证器看 GT 跟 GEN 字段不一致 + 该字段是规则给的 → 反例
- **LLM 二次判断** (生产场景 — 规则跟 LLM 看同输入, 答案不一致): LLM 给 X, 规则给 Y → 反例 (不一定规则错, 但需审)
- **下游 fail 反推** (如下游用了规则输出, 数据不可用): 这次规则错

### 3. 反例账本落盘

每发现 1 条反例:

```
data/domains/<domain>/counterexamples/<rule_id>_<date>.jsonl
```

每行 1 反例:
```json
{
  "rule_id": "grid_mount_anchor_v1",
  "rule_output": {"anchor_min": [0, 0]},
  "ground_truth": {"anchor_min": [0.5, 0.5]},
  "trigger_context": {...},
  "verification_source": "GT_validator | LLM_secondary | downstream_fail",
  "timestamp": "...",
  "reflected": false        # 是否已审过
}
```

### 4. 反思触发

**每出 1 条反例**, 必须人工或 LLM 看一眼 — 不能堆积. 触发方式:
- **生产数据上跑分时**: 跑完输出 `counterexamples_<rule_id>.md` 报告, 列每条反例 + 上下文 + 建议处置
- **规则首次反例触发时**: 立报警到对话或 PR 流程, 不静默累计
- **累计阈值**: ≥ 3 条反例 → 规则降级 (从死规则降为 LLM fallback) 或回退

反思的判:
- 反例是 "样本噪声" (1-2 个 + 边角 case)? → 加边界条件修规则
- 反例是 "结构性问题" (规则假设错)? → 回退死规则, 走 LLM
- 反例 ≥ 3 + 跨多 case? → 该规则不应存在, 立刻回退

## 触发频次

不是每次都跑, 但必须可触发:
- **每次 V<N> 跑分**: 强制跑反例对比, 落盘账本
- **每周 / 重要 PR 前**: 累计反例审查

## 跟 0 反例铁律的关系

`llm_first.md` 原则 1 立"立规则前必 LLM 验证 0 反例". 反例账本是**立规则后**的持续验证 — 防止"立时 0 反例, 后续生产数据出反例没人发现".

两者配套使用, 缺一不可.

## 死规则清单 (demogame_ux 域 2026-04-30)

| 规则 ID | 状态 | 真值源 | 账本路径 |
|---|---|---|---|
| `grid_mount_anchor_v1` | 修后保留 (B-#3) | GT 验证器 + step12 跑时 | `data/domains/demogame_ux/counterexamples/grid_mount_anchor_v1_*.jsonl` |
| `typical_subtree_keep_llm_v1` | LLM 唯一路径 (B-#4) | GT 验证器 (子节点存在性) | 同上 |
| `decoration_classify_llm_v1` | LLM 缓存 (B-#5) | GT m_Name 真用法 | 同上 |
| `naming_style_v1` (9 条 yaml) | LLM 缓存 (C-#6) | 4 GT 抽样 | 同上 |
| `usually_unpacked_v1` | LLM 反推 (C-#7) | 4 GT 验证 | 同上 |

## 一句话

> **死规则不是放出来就完了 — 必须带在线反例监测. 出 1 个反例就反思 1 次, ≥ 3 个反例就回退.**
