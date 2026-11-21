# [OMNI] origin=claude-code domain=services/tech_debt ts=2026-04-18T00:00:00Z
# [OMNI] material_id="material:diagnosis.tech_debt.module_aggregate.exports.py"
"""tech_debt — REGISTRY.md 的 I/O 与管理层（consumer 视角）。

与 guardian / semantic_auditor 的分工：
  - guardian 扫规则 → 写 §活跃违规（producer）
  - semantic_auditor 调 LLM → 写 §语义合规待审（producer）
  - tech_debt 读全视图 + resolve 条目（consumer + resolver）

CLI 对应关系见 cli/commands/debt.py（omni debt list / stats / resolve）。
"""
from __future__ import annotations

from .registry_io import (
    RegistryRow,
    RegistrySnapshot,
    ResolveResult,
    AppendResult,
    load_registry,
    list_rows,
    compute_stats,
    resolve_row,
    append_row,
    SECTION_SPECS,
)
from .events import (
    ARCHEvent,
    KNOWN_EVENT_TYPES,
    KNOWN_INITIATORS,
    append_event,
    read_events,
)
from .drift_checker import (
    DriftFinding,
    check_design_md_drift,
    check_plan_drift,
    run_drift_audit,
)

__all__ = [
    "RegistryRow",
    "RegistrySnapshot",
    "ResolveResult",
    "AppendResult",
    "load_registry",
    "list_rows",
    "compute_stats",
    "resolve_row",
    "append_row",
    "SECTION_SPECS",
    "ARCHEvent",
    "KNOWN_EVENT_TYPES",
    "KNOWN_INITIATORS",
    "append_event",
    "read_events",
    "DriftFinding",
    "check_design_md_drift",
    "check_plan_drift",
    "run_drift_audit",
]
