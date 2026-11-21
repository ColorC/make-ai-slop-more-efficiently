# [OMNI] origin=ai-ide domain=services/_core/meta_io ts=2026-05-02T06:00:00Z type=service status=active agent=ai-ide-current
# [OMNI] summary="MetaIO dataclass 定义 + StateCheck + Kind enum"
# [OMNI] why="跟规范 docs/standards/cli/meta_io.md 一致, 字段含 id/kind/target_type/side_effect_scope/is_atomic_semantic/state_check"
# [OMNI] tags=meta_io,definitions,foundation
# [OMNI] material_id="material:core.meta_io.type_definitions.schema.py"
"""元 IO 数据类定义."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class MetaIOKind(str, Enum):
    """I/O 性质."""

    READ = "read"
    """输入观察 - 读外部状态进 omnicompany, 不改外部状态."""

    WRITE = "write"
    """输出操作 - 改外部状态."""

    MUTATE = "mutate"
    """读改一体 - 既读外部状态又改 (例如 'compare-and-swap' / 'append-and-return-id')."""


@dataclass(frozen=True)
class StateCheck:
    """状态检查规约 - 调用前后外部状态预期."""

    precondition: str = ""
    """调用前外部状态必须满足的条件 (描述性, 后续配套自动检查 hook)."""

    postcondition: str = ""
    """调用后外部状态预期变化."""

    invariant: str = ""
    """调用前后不变量 (例: 文件 mtime 之外其他属性不变)."""


@dataclass(frozen=True)
class MetaIO:
    """一份元 IO 声明.

    "语义原子化, 尺寸上可以再分但是语义上不再分" (用户原始需求 6.6 第 2 点).

    例: meta_io.fs.read_file_text — 读一份文本文件全文返回 string. 字节大小可分 (1k / 10k),
    但语义不可分. 反例: read_csv_file_then_pick_first_row 是组合操作, 不是元 IO.
    """

    id: str
    """全局唯一 id, 例 'meta_io.fs.read_file_text'. 命名空间分级 (meta_io.<domain>.<verb>)."""

    kind: MetaIOKind
    """read / write / mutate."""

    target_type: str
    """目标资源类型: file / api / db / process / network / etc."""

    description: str
    """这条 IO 干嘛 + 前提条件. ≥ 50 字符 (跟 material description 同级要求)."""

    side_effect_scope: str
    """副作用范围 (local_filesystem.read_only / git_remote.push / external_service.update 等).
    跟 G4 锁联动用 - watched_meta_io 规则按 scope 过滤."""

    is_atomic_semantic: bool = True
    """语义原子化标识. False 表示这是组合 (留给过渡期, 长期所有 meta_io 应当 True)."""

    state_check: StateCheck = field(default_factory=StateCheck)
    """状态检查规约. 跟 hook 联动."""

    tags: tuple[str, ...] = ()
    """额外标签 (类似 material tags) — 例 'fs', 'read', 'kind.idempotent'."""

    def __repr__(self) -> str:
        return f"MetaIO(id={self.id!r}, kind={self.kind.value}, target={self.target_type})"
