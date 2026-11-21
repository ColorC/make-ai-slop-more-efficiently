<!-- [OMNI] origin=claude-code domain=services/cleanup_bot ts=2026-05-04T13:00:00Z type=doc status=active belongs_to_service=cleanup_bot -->
<!-- [OMNI] material_id="material:diagnosis.cleanup_bot.design_specification.md" -->

# cleanup_bot · 设计文档

> 设计目的请看 [README.md](README.md). 怎么用请看 [SKILL.md](SKILL.md). 本文档专管**架构内部** (接口 / 决策 / 数据流 / 局限).

## 状态
- **版本**: V1 (Clean Migration 2026-04-21)
- **成熟度**: active
- **下一步**: 按需扩展扫描策略 (如正则关键词过滤、多关键词组合)

## 核心接口

- [`workers/__init__.py`](workers/__init__.py) — 3 个 Worker：
  - `EvidenceGathererWorker` (ANCHOR) — os.walk 递归扫描，收集可疑路径
  - `AnomalyDetectorWorker` (LLM) — 分析路径合法性，生成 PowerShell 清理脚本
  - `RollbackPlannerWorker` (ANCHOR) — 格式化打印清理计划（不自动删除）
- [`formats.py`](formats.py) — 4 个 Material 定义（source/internal/internal/sink）
- [`team.py`](team.py) — `build_team()` 返回 3 节点 TeamSpec

## 架构决策

### D1 三节点线性链路
EvidenceGatherer → AnomalyDetector → RollbackPlanner。无分支，PASS 直穿，扫描到可疑路径则继续，否则 EvidenceGatherer 返回 FAIL 中止链路。

### D2 最大深度 5 层保护
EvidenceGathererWorker 使用 `max_depth = 5` 限制 os.walk 深度，避免在大磁盘上无限递归。这不是铁律 A 截断（输出结果是完整路径列表，不是喂给 LLM 的内容截断）。

### D3 安全降级：只输出计划，不自动删除
RollbackPlannerWorker 只打印 PowerShell 脚本，明确要求用户手动执行。防止 AI 误判导致正常目录被删除。

### D4 Stage 3 独立文件架构
每个 Worker 独立一个文件（`workers/<name>_worker.py`），`workers/__init__.py` 仅 re-export。不使用 Diamond shortcut，`_archive/` 已删除。旧 `from ...routers import XxxRouter` 通过 `routers.py` compat shim 继续工作。

### D5 LLM 提示词专注于路径模式识别
`_CLEANUP_SYSTEM_PROMPT` 教 LLM 识别"单字母根目录"、"路径重复嵌套"等 AI 误触特征。提示词归档在 `_archive/routers_legacy.py`。

## 数据流 / 拓扑

```
外部触发
  cleanup.input   (kind.source)   ← {root_dir, keyword}
       │
       ▼
  EvidenceGathererWorker  ← os.walk 扫描，收集包含 keyword 的所有路径
       │
  cleanup.evidence  (kind.internal)   ← {keyword, evidence_str, raw_paths}
       │
       ▼
  AnomalyDetectorWorker  ← LLM 判断哪些是垃圾，生成 PowerShell 清理脚本
       │
  cleanup.plan     (kind.internal)   ← {anomaly_report: Markdown}
       │
       ▼
  RollbackPlannerWorker  ← 格式化打印到控制台，不自动删除
       │
  cleanup.done     (kind.sink)
```

## 已知局限

- **LLM 误判风险**：AnomalyDetectorWorker 依赖 LLM 判断路径是否异常，极端情况可能误判正常路径为垃圾。RollbackPlannerWorker 的"只打印不执行"设计作为最后安全防线。
- **仅检测路径名模式**：当前只靠关键词匹配路径名，无法感知目录内容（如识别空目录 vs 有数据的目录）。
- **单关键词输入**：一次只能指定一个 keyword，多关键词场景需多次调用或扩展 EvidenceGathererWorker。

## 参考资料

- `lap_auditor` 服务 — 类似的三节点诊断→LLM→输出链路设计

## 新哲学对齐 (Stage 3 Clean Migration 2026-04-21)

| 旧世界 (Router/Format/Pipeline) | 新世界 (Worker/Material/Team) |
|---|---|
| `EvidenceGathererRouter` | `EvidenceGathererWorker` |
| `AnomalyDetectorRouter` | `AnomalyDetectorWorker` |
| `RollbackPlannerRouter` | `RollbackPlannerWorker` |
| `cleanup.input/evidence/plan/done` (Format) | 同 id，Kind 标注补全 (Material F-19) |
| `PipelineSpec` / `build_pipeline()` | `TeamSpec` / `build_team()` |

迁移方式：Stage 3 完全搬迁（每个 Worker 一个独立文件，`_archive/` 已删除）+ `routers.py` 为 compat shim（仅别名 re-export）。
