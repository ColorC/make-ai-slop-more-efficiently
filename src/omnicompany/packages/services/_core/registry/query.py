# [OMNI] origin=claude-code domain=services/registry ts=2026-04-11T00:00:00Z
# [OMNI] material_id="material:core.registry.query_engine.chain_builder.py"
"""
Registry Query — 统一查询接口

提供对 InstanceRegistry 的结构化查询能力。
设计原则：查询是只读操作，不修改 registry。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

from .instance import InstanceEntry, InstanceRegistry


@dataclass
class QueryResult:
    """查询结果封装。"""
    entries: list[InstanceEntry]
    query_desc: str  # 人可读的查询描述

    def __len__(self) -> int:
        return len(self.entries)

    def __iter__(self):
        return iter(self.entries)

    def summary(self) -> str:
        lines = [f"Query: {self.query_desc}", f"Results: {len(self.entries)} entities"]
        by_type: dict[str, int] = {}
        for e in self.entries:
            by_type[e.type] = by_type.get(e.type, 0) + 1
        for t, n in sorted(by_type.items()):
            lines.append(f"  {t}: {n}")
        return "\n".join(lines)

    def entity_ids(self) -> list[str]:
        return [e.entity_id for e in self.entries]


class RegistryQuery:
    """链式查询构建器。

    用法示例：
        q = RegistryQuery(registry)
        result = q.type("router").package("demogame.team_table").execute()
        result = q.type("router").attr_eq("attrs.format_in_kind", "fstring").execute()
    """

    def __init__(self, registry: InstanceRegistry) -> None:
        self._registry = registry
        self._type: Optional[str] = None
        self._package: Optional[str] = None
        self._name_contains: Optional[str] = None
        self._filters: list[Callable[[InstanceEntry], bool]] = []
        self._desc_parts: list[str] = []

    def type(self, type_name: str) -> "RegistryQuery":
        self._type = type_name
        self._desc_parts.append(f"type={type_name}")
        return self

    def package(self, pkg: str, prefix: bool = True) -> "RegistryQuery":
        """按 package 过滤。prefix=True 时匹配前缀（含子包）。"""
        self._package = pkg
        self._desc_parts.append(f"package={'~' if prefix else '='}{pkg}")
        if prefix:
            self._filters.append(
                lambda e, p=pkg: e.package == p or e.package.startswith(p + ".")
            )
        else:
            self._filters.append(lambda e, p=pkg: e.package == p)
        return self

    def name_contains(self, substr: str) -> "RegistryQuery":
        self._desc_parts.append(f"name~={substr}")
        self._filters.append(lambda e, s=substr: s.lower() in e.name.lower())
        return self

    def attr_eq(self, dotted_key: str, value: Any) -> "RegistryQuery":
        """按 attrs 中的字段值过滤。支持点分路径（如 'attrs.format_in'）。"""
        self._desc_parts.append(f"{dotted_key}={value!r}")
        parts = dotted_key.split(".")
        def _check(entry: InstanceEntry, parts=parts, value=value) -> bool:
            obj: Any = entry
            for part in parts:
                if isinstance(obj, dict):
                    obj = obj.get(part)
                else:
                    obj = getattr(obj, part, None)
                if obj is None:
                    return False
            return obj == value
        self._filters.append(_check)
        return self

    def attr_truthy(self, dotted_key: str) -> "RegistryQuery":
        """按 attrs 中的字段是否为真值过滤。"""
        self._desc_parts.append(f"{dotted_key}=<truthy>")
        parts = dotted_key.split(".")
        def _check(entry: InstanceEntry, parts=parts) -> bool:
            obj: Any = entry
            for part in parts:
                if isinstance(obj, dict):
                    obj = obj.get(part)
                else:
                    obj = getattr(obj, part, None)
            return bool(obj)
        self._filters.append(_check)
        return self

    def custom(self, fn: Callable[[InstanceEntry], bool], desc: str = "") -> "RegistryQuery":
        """自定义过滤函数。"""
        if desc:
            self._desc_parts.append(desc)
        self._filters.append(fn)
        return self

    def execute(self) -> QueryResult:
        # 获取候选集
        if self._type:
            candidates = self._registry.list_by_type(self._type)
        else:
            candidates = self._registry.list_all()

        # 应用所有过滤器
        for f in self._filters:
            candidates = [e for e in candidates if f(e)]

        desc = ", ".join(self._desc_parts) if self._desc_parts else "all"
        return QueryResult(entries=candidates, query_desc=desc)


def query(registry: InstanceRegistry) -> RegistryQuery:
    """便捷入口：创建一个新的查询构建器。"""
    return RegistryQuery(registry)
