# [OMNI] origin=claude-code domain=services/knowledge/__init__.py ts=2026-04-09T00:00:00Z
# [OMNI] material_id="material:learning.knowledge.package_aggregator.exports.py"
"""omnicompany.packages.services._learning.knowledge — OmniKB.

Markdown 为存储、Router 为接口、6 类条目的知识库系统。

## 文件分层
  schema.py    — 6 种 entry 类型定义 + frontmatter 解析
  store.py     — 文件树读写 (本地 markdown 文件, 经 guarded_write)
  index.py     — 内存索引 + 查询 + 引用校验
  audit.py     — 5 类一致性审计 (drift/orphan/staleness/coverage/validation)
  routers.py   — 5 个 Router (Query/Write/Locate/Audit/IndexRebuild)
  pipeline.py  — 2 条 pipeline (omnikb-audit / omnikb-seed)
  run.py       — build_*_pipeline / build_*_bindings

## 使用场景

### 从其他管线中查询知识
```python
from omnicompany.packages.services._learning.knowledge import KBStore, load_or_rebuild

index = load_or_rebuild(project_root)
omni_arch = index.find(types=["karch"], scope="omnicompany", maturity="stable")
hits = index.text_search("context compression")
```

### 从其他管线中写入
```python
from omnicompany.packages.services._learning.knowledge import (
    KBStore, KExperimentEntry,
)
store = KBStore(project_root)
entry = KExperimentEntry(
    id="kb.experiment.20260409_foo",
    name="Foo experiment",
    date_started="2026-04-09",
    hypothesis="..."
)
store.write_entry(entry, body="# ... markdown ...")
```

### 作为管线节点使用
见 pipeline.py + run.py, 注册名:
  - omnikb-audit — 全量审计
  - omnikb-seed  — 从 docs/plans/_graveyard/memory 自动 seed
"""

from __future__ import annotations

# ── Schema / Entry 类型 ────────────────────────────────────
from omnicompany.packages.services._learning.knowledge.schema import (
    OMNIKB_TYPES,
    MATURITY_VALUES,
    KFormatEntry,
    KRouterEntry,
    KArchitectureEntry,
    KDecisionEntry,
    KExperimentEntry,
    KRepoArchitectEntry,
    KnowledgeEntry,
    parse_kb_document,
    entry_class_for,
)

# ── Store (文件读写) ───────────────────────────────────────
from omnicompany.packages.services._learning.knowledge.store import KBStore

# ── Index (内存查询 + 持久化) ──────────────────────────────
from omnicompany.packages.services._learning.knowledge.index import (
    KBIndex,
    ValidationIssue,
    rebuild_index,
    load_or_rebuild,
    validate,
)

# ── Audit (5 类一致性检查) ─────────────────────────────────
from omnicompany.packages.services._learning.knowledge.audit import (
    AuditReport,
    CoverageReport,
    AnchorDrift,
    OrphanCode,
    StalenessReport,
    run_full_audit,
    format_coverage_report,
    check_code_anchors,
    find_orphan_routers,
    staleness_report,
)

# ── Hooks (可选 PeriodicHook, 由 sentinel daemon 调用) ─────
from omnicompany.packages.services._learning.knowledge.hooks import KBAuditHook

__all__ = [
    # ⚠ K-type 类（KFormatEntry/KRouterEntry/KArchitectureEntry/KDecisionEntry/
    # KExperimentEntry/KRepoArchitectEntry/KnowledgeEntry）**已从公开 __all__ 移除**
    # （2026-04-18 废弃）。外部禁止新增引用；详见 schema.py 模块 docstring。
    # 内部（knowledge/ + hypothesis/）仍可通过显式 import 使用，等 V2 重构。

    # Schema（只留通用工具）
    "MATURITY_VALUES",
    "parse_kb_document",
    # Store
    "KBStore",
    # Index
    "KBIndex",
    "ValidationIssue",
    "rebuild_index",
    "load_or_rebuild",
    "validate",
    # Audit
    "AuditReport",
    "CoverageReport",
    "AnchorDrift",
    "OrphanCode",
    "StalenessReport",
    "run_full_audit",
    "format_coverage_report",
    "check_code_anchors",
    "find_orphan_routers",
    "staleness_report",
    # Hooks
    "KBAuditHook",
]
