# [OMNI] origin=claude-code domain=omnicompany/doctor ts=2026-04-20T00:00:00Z type=config
# [OMNI] material_id="material:diagnosis.doctor.worker.blackboard.subscription_graph_builder.py"
"""blackboard/_shared.py — 黑板诊断子域共享工具.

核心函数:
- `load_team_workers(team_module_path)` → list[Worker class]
- `load_team_materials(team_module_path)` → list[Material instance]
- `build_subscription_graph(workers, materials)` → SubscriptionGraph

都走 Python 动态 import (团队包必须可 import). 若 Team import 失败 → 诊断报告里记录
`team_import_failed`, 诊断 Worker 返回 FAIL 跳过而非崩溃.

静态 AST 扫描作为 fallback (后续 Phase 可加), 当前全走 runtime import.
"""
from __future__ import annotations

import importlib
import inspect
from dataclasses import dataclass, field
from typing import Any


# ══════════════════════════════════════════════════════════════════════
# 数据结构
# ══════════════════════════════════════════════════════════════════════


@dataclass
class WorkerMeta:
    """单个 Worker 类的元数据抽取."""
    cls: type                               # Worker 类对象
    name: str                               # 类名
    format_in: list[str]                    # 订阅 Material id 列表 (单 str 也转成 1 元素 list)
    format_in_mode: str | None              # "and" | "or" | None (list 必须声明, str 可 None)
    format_out: str | None                  # 产出 Material id (单 str)
    source_file: str                        # 文件路径
    run_source: str                         # run() 方法源码 (用于扫 _emit_as_new_job 等)

    @property
    def is_multi_input(self) -> bool:
        return len(self.format_in) > 1


@dataclass
class MaterialMeta:
    """单个 Material 实例的元数据抽取."""
    instance: Any                           # Material / Format 实例
    id: str                                 # Material id
    tags: list[str]                         # tags 列表
    kind: str | None                        # "source" | "internal" | "sink" | None (未标)

    @property
    def has_kind(self) -> bool:
        return self.kind is not None


@dataclass
class SubscriptionGraph:
    """Team 的订阅图摘要."""
    team_module_path: str
    workers: list[WorkerMeta] = field(default_factory=list)
    materials: list[MaterialMeta] = field(default_factory=list)
    # material_id → list[Worker class name] 订阅者
    consumers_of: dict[str, list[str]] = field(default_factory=dict)
    # material_id → list[Worker class name] 产出者
    producers_of: dict[str, list[str]] = field(default_factory=dict)

    def material_by_id(self, mid: str) -> MaterialMeta | None:
        for m in self.materials:
            if m.id == mid:
                return m
        return None


# ══════════════════════════════════════════════════════════════════════
# 动态 import Team 获取 Worker / Material
# ══════════════════════════════════════════════════════════════════════


def _extract_kind(tags: list[str]) -> str | None:
    for t in tags:
        if t == "kind.source":
            return "source"
        if t == "kind.internal":
            return "internal"
        if t == "kind.sink":
            return "sink"
    return None


def _get_format_in_list(cls: type) -> list[str]:
    fi = getattr(cls, "FORMAT_IN", None)
    if fi is None:
        return []
    if isinstance(fi, str):
        return [fi]
    if isinstance(fi, (list, tuple)):
        return [s for s in fi if isinstance(s, str)]
    return []


def _safe_getsource(obj: Any) -> str:
    try:
        return inspect.getsource(obj)
    except (OSError, TypeError):
        return ""


def load_team_workers(team_module_path: str) -> list[WorkerMeta]:
    """动态 import Team.workers → 返回 WorkerMeta 列表.

    Team 必须有 `workers.ALL_WORKERS` list (Clean Migration 约定)。
    """
    workers_mod = importlib.import_module(f"{team_module_path}.workers")
    all_workers = getattr(workers_mod, "ALL_WORKERS", [])
    metas: list[WorkerMeta] = []
    for cls in all_workers:
        fi = _get_format_in_list(cls)
        fim = getattr(cls, "FORMAT_IN_MODE", None)
        # 只有显式类属性才算声明 (继承来的 Worker 基类默认 "and" 不算)
        # 检查是否在 cls.__dict__ 里 (不含 Worker 基类默认)
        fim_declared = "FORMAT_IN_MODE" in cls.__dict__
        src_file = ""
        try:
            src_file = inspect.getfile(cls)
        except (OSError, TypeError):
            pass
        run_method = getattr(cls, "run", None)
        run_src = _safe_getsource(run_method) if run_method else ""
        metas.append(
            WorkerMeta(
                cls=cls,
                name=cls.__name__,
                format_in=fi,
                format_in_mode=fim if fim_declared else None,
                format_out=getattr(cls, "FORMAT_OUT", None),
                source_file=src_file,
                run_source=run_src,
            )
        )
    return metas


def load_team_materials(team_module_path: str) -> list[MaterialMeta]:
    """动态 import Team.formats → 返回 MaterialMeta 列表.

    Team 必须有 `formats.ALL_FORMATS` (或 FORMATS) list.
    """
    formats_mod = importlib.import_module(f"{team_module_path}.formats")
    all_fmt = getattr(formats_mod, "ALL_FORMATS", None)
    if all_fmt is None:
        all_fmt = getattr(formats_mod, "FORMATS", [])
    metas: list[MaterialMeta] = []
    for inst in all_fmt:
        tags = list(getattr(inst, "tags", []))
        metas.append(
            MaterialMeta(
                instance=inst,
                id=getattr(inst, "id", "?"),
                tags=tags,
                kind=_extract_kind(tags),
            )
        )
    return metas


def build_subscription_graph(team_module_path: str) -> SubscriptionGraph:
    """加载 Team 并构造订阅图. import 失败抛 ImportError (调用方处理)."""
    workers = load_team_workers(team_module_path)
    materials = load_team_materials(team_module_path)

    consumers_of: dict[str, list[str]] = {}
    producers_of: dict[str, list[str]] = {}
    for w in workers:
        for fin in w.format_in:
            consumers_of.setdefault(fin, []).append(w.name)
        if w.format_out:
            producers_of.setdefault(w.format_out, []).append(w.name)

    return SubscriptionGraph(
        team_module_path=team_module_path,
        workers=workers,
        materials=materials,
        consumers_of=consumers_of,
        producers_of=producers_of,
    )


__all__ = [
    "WorkerMeta",
    "MaterialMeta",
    "SubscriptionGraph",
    "load_team_workers",
    "load_team_materials",
    "build_subscription_graph",
]
