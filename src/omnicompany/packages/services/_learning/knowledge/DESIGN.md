<!-- [OMNI] origin=claude-code domain=services/knowledge ts=2026-04-21T00:00:00Z type=doc status=active -->
<!-- [OMNI] material_id="material:learning.knowledge.design_specification.md" -->

# knowledge · 设计文档

## 状态
- **版本**: V1 (Clean Migration 2026-04-21)
- **成熟度**: active
- **下一步**: 扩展查询能力（向量搜索）；添加 KB 条目版本管理

## 核心目的

OmniKB 知识库管理服务。通过五个独立原子操作（查询/写入/定位/审计/重建索引）把 OmniKB 的文件存储、索引和一致性审计 API 暴露为六元接口（Worker/Material）。

解决的问题：将 OmniCompany 积累的架构知识（karch/kdec/krouter/kfmt/krepo/kexp 类型条目）结构化存储，并提供精确查询和自然语言定位入口。

不解决：知识自动更新（需上游感知）；跨仓库知识融合。

## 核心接口

- [`workers/__init__.py`](workers/__init__.py) — 5 个 Worker（全部为 ANCHOR 确定性操作）：
  - `KBQueryWorker` — 多维度查询（id/type/tag/domain/scope/text）
  - `KBWriteWorker` — 写入条目到 `.omni/knowledge/`（via guarded_write）
  - `KBLocateWorker` — 自然语言定位 + code_anchors 聚合（Q2 主入口）
  - `KBAuditWorker` — 全量 5 类一致性审计（validation/drift/orphan/staleness/coverage）
  - `KBIndexRebuildWorker` — 重建 `.omni/knowledge_index.json`
- [`formats.py`](formats.py) — 10 个 Material（5 source + 5 sink，各操作独立成对）
- [`team.py`](team.py) — `build_audit_pipeline()` 返回单节点 TeamSpec（审计子管线）
- 底层数据层：`store.py`, `index.py`, `audit.py`, `schema.py` 等（不属于 Worker 层）

## 架构决策

### D1 五个独立原子操作
每个 Worker 对应一个独立的 source→sink Format 对，不形成链路。调用方按需单独触发，无强制顺序依赖（写入后可选择触发重建索引，但不在本管线内强制）。

### D2 确定性底座，无 LLM
全部 5 个 Worker 均为 ANCHOR 类（不调用 LLM），操作的是文件系统上的 `.omni/knowledge/` 目录。LLM 只在上层 absorption/repo_learner 等服务中消费 OmniKB 查询结果。

### D3 project_root 自动解析
所有 Worker 的 `__init__(project_root=None)` 通过共享 `workers/_shared.py::_project_root()` 定位 project_root（从 `__file__` 向上 6 层）。Workers 无需外部注入 project_root，直接实例化即可。

### D4 Stage 3 独立文件架构
每个 Worker 独立一个文件（`workers/kb_<name>_worker.py`），`workers/__init__.py` 仅 re-export。不使用 Diamond shortcut，`_archive/` 已删除。旧 `from ...knowledge.routers import KBXxxRouter` 通过 `routers.py` compat shim 继续工作。

### D5 只有审计操作注册为正式 TeamSpec
`build_audit_pipeline()` 将 `KBAuditWorker` 注册为可被 Guardian 调度的正式单节点管线。其他 4 个操作当前由调用方直接调用 Worker，未注册为 TeamSpec（按需扩展）。

## 数据流 / 拓扑

```
(独立操作，无链路耦合)

kb.query      → KBQueryWorker      → kb.query_result
kb.entry_draft → KBWriteWorker     → kb.entry_committed
kb.locate_query → KBLocateWorker   → kb.locate_result
kb.audit_request → KBAuditWorker   → kb.audit_report
kb.rebuild_request → KBIndexRebuildWorker → kb.index_stats

底层数据层 (被 Workers 调用, 不在总线上):
  store.py → KBStore.write_entry() / find_by_id()
  index.py → KBIndex.find() / text_search()
  audit.py → run_full_audit()
```

## 已知局限

- **文本搜索无语义向量**：KBLocateWorker 的 `text_search` 是关键词匹配，非向量语义搜索。复杂问题定位质量取决于 KB 条目的文本质量。
- **写入无锁并发保护**：`guarded_write` 是文件级操作，多 Worker 并发写入同一 entry 时可能产生竞争。当前假设 OmniKB 写入频率低，单次触发。
- **索引重建非增量**：`KBIndexRebuildWorker` 全量扫描磁盘，KB 条目多时性能下降。升级路径：改为增量 dirty-flag 机制。

## 参考资料

- `.omni/knowledge/` — OmniKB 条目存储目录（karch/kdec/krouter/kfmt/krepo/kexp）
- `.omni/knowledge_index.json` — 快速查询索引
- `repo_learner` 服务 — 消费 KBWriteWorker 将学习结果写入 OmniKB
- `absorption` 服务 — 消费 KBQueryWorker/KBLocateWorker 检索 OmniCompany 能力

## 新哲学对齐 (Stage 3 Clean Migration 2026-04-21)

| 旧世界 (Router/Format/Pipeline) | 新世界 (Worker/Material/Team) |
|---|---|
| `KBQueryRouter` | `KBQueryWorker` |
| `KBWriteRouter` | `KBWriteWorker` |
| `KBLocateRouter` | `KBLocateWorker` |
| `KBAuditRouter` | `KBAuditWorker` |
| `KBIndexRebuildRouter` | `KBIndexRebuildWorker` |
| `kb.*` (Format, 无 kind 标注) | `kb.*` (Material, F-19 kind 补全) |
| `PipelineSpec` / `build_audit_pipeline()` | `TeamSpec` / `build_audit_pipeline()` |

迁移方式：Stage 3 完全搬迁（每个 Worker 一个独立文件 + 共享 `workers/_shared.py::_project_root()`，`_archive/` 已删除）+ `routers.py` 为 compat shim（仅别名 re-export）。
