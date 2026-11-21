# [OMNI] origin=ai-ide domain=services/_core/meta_io ts=2026-05-02T06:00:00Z type=service status=active agent=ai-ide-current
# [OMNI] summary="META_IO_REGISTRY - id → MetaIO 字典 + 注册函数"
# [OMNI] why="进程内单例注册表, 启动时填. 跟 G2 InstanceRegistry 是两层 (G2 是持久化, 这是运行时索引)"
# [OMNI] tags=meta_io,registry,runtime-index
# [OMNI] material_id="material:core.meta_io.runtime_registry.index.py"
"""元 IO 运行时注册表.

进程内 dict, 启动时通过 `register_meta_io()` 填. 不持久化 (持久化走 G2 InstanceRegistry
type=meta_io).

约定: 一个 id 只能注册一次 (重复同一对象 OK, 不同对象抛 ValueError).
"""
from __future__ import annotations


from omnicompany.packages.services._core.meta_io.definitions import MetaIO


META_IO_REGISTRY: dict[str, MetaIO] = {}


def register_meta_io(meta_io: MetaIO) -> None:
    """登记一份 MetaIO 到运行时注册表.

    重复同一对象是 no-op; 同名不同对象 → ValueError.
    """
    existing = META_IO_REGISTRY.get(meta_io.id)
    if existing is not None and existing is not meta_io:
        # 看是不是字段全等 (frozen dataclass __eq__ 用值比较)
        if existing != meta_io:
            raise ValueError(
                f"meta_io {meta_io.id!r} 已注册成不同对象. "
                f"已存在: kind={existing.kind} target={existing.target_type}; "
                f"新尝试: kind={meta_io.kind} target={meta_io.target_type}"
            )
    META_IO_REGISTRY[meta_io.id] = meta_io


def list_meta_io(*, kind: str | None = None, target_type: str | None = None) -> list[MetaIO]:
    """列已注册元 IO, 可按 kind / target_type 过滤."""
    items = list(META_IO_REGISTRY.values())
    if kind:
        items = [m for m in items if m.kind.value == kind]
    if target_type:
        items = [m for m in items if m.target_type == target_type]
    return items


def get_meta_io(meta_io_id: str) -> MetaIO | None:
    """按 id 查."""
    return META_IO_REGISTRY.get(meta_io_id)
