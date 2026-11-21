# [OMNI] origin=ai-ide domain=services/_core/meta_io ts=2026-05-02T06:00:00Z type=service status=active agent=ai-ide-current
# [OMNI] summary="元 IO 实施层 - 语义原子化 I/O 单元注册 + tool 声明 consumed/produced + state_check"
# [OMNI] why="用户原始需求 6.6: 所有 I (输入观察) 和 O (输出操作) 统一注册为元 IO. 这层让 tool 操作可被状态检查 + 反查根除 + 跟 G4 锁联动 watched_meta_io 灵活规则"
# [OMNI] tags=meta_io,tool,state-check,foundation
# [OMNI] material_id="material:core.meta_io.module_aggregate.exports.py"
"""元 IO 实施层 (用户原始需求 6.6).

详细规范见 `docs/standards/cli/meta_io.md`.

核心 API:
  MetaIO              - 一份元 IO 声明 (id / kind / target_type / state_check)
  META_IO_REGISTRY    - 字符串 id → MetaIO 的注册表
  register_meta_io    - 注册新元 IO
  list_meta_io        - 列已注册元 IO

跟 G2 联动: registry/meta.py 的 type=meta_io 已加. omni register --kind=meta_io 也可以 (但
更常见是代码内声明 + 启动时 register_meta_io).

跟 tool 联动: tool 实施时声明 CONSUMED_META_IO / PRODUCED_META_IO 类属性, agent 调度
时可走元 IO 索引找出"哪些 tool 能读 X / 写 Y".
"""
from __future__ import annotations

from omnicompany.packages.services._core.meta_io.definitions import (
    MetaIO,
    MetaIOKind,
    StateCheck,
)
from omnicompany.packages.services._core.meta_io.registry import (
    META_IO_REGISTRY,
    register_meta_io,
    list_meta_io,
    get_meta_io,
)
from omnicompany.packages.services._core.meta_io.builtins import (
    register_builtin_meta_io,
)
from omnicompany.packages.services._core.meta_io.audit import (
    emit_meta_io_audit,
    query_audit,
)
from omnicompany.packages.services._core.meta_io.state_check import (
    MetaIOStateCheckHook,
)


# 启动时登记内置元 IO
register_builtin_meta_io()


__all__ = [
    "MetaIO",
    "MetaIOKind",
    "StateCheck",
    "META_IO_REGISTRY",
    "register_meta_io",
    "list_meta_io",
    "get_meta_io",
    "register_builtin_meta_io",
    "emit_meta_io_audit",
    "query_audit",
    "MetaIOStateCheckHook",
]
