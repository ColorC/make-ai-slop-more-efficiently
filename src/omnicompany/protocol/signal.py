# [OMNI] origin=human ts=2026-04-08T03:23:35Z
# [OMNI] material_id="material:protocol.signal.data_model.py"
"""Signal — 六元语义信号模型的唯一通信货币

Signal 是节点间流动的语义数据单元（V1.1 §1 唯一权威定义）。

字段：
  format     — 语义类型标签（对应 Format.id）
  text       — 自然语言摘要（人和 LLM 可直接消费）
  node_id    — 来源节点 ID（追溯用）
  meta       — 结构化载荷（路由/过滤用，关键语义不应只存于此）
  created_at — 创建时间戳

与 Intent 的区别：
  Signal = "发生了什么"（观测结论）
  Intent = "需要做什么"（任务请求）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Signal:
    """六元语义信号模型中的运行时数据单元。

    唯一定义位置。全局 import: from omnicompany.protocol import Signal
    """

    format: str
    """语义格式标识，对应 protocol/format.py Format.id。"""

    text: str
    """自然语言描述，LLM 和人类均可直接理解。"""

    node_id: str = ""
    """产生此 Signal 的节点 ID（Hook 或 Node）。"""

    meta: dict[str, Any] = field(default_factory=dict)
    """结构化载荷。在 TeamRunner 中承载节点间传递的 dict 数据。"""

    created_at: float = 0.0
    """Unix 时间戳，0.0 表示未设置。"""

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "text": self.text,
            "node_id": self.node_id,
            "meta": self.meta,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Signal":
        return cls(
            format=d.get("format", ""),
            text=d.get("text", ""),
            node_id=d.get("node_id", d.get("source", "")),  # 兼容旧字段
            meta=d.get("meta", d.get("metadata", {})),       # 兼容旧字段
            created_at=d.get("created_at", 0.0),
        )

    # ── 工厂方法：标准信号类型 ────────────────────────────────────────

    @classmethod
    def pain(cls, node_id: str, text: str, severity: str = "medium") -> "Signal":
        """构造痛觉信号。"""
        return cls(
            format="pain_signal",
            text=text,
            node_id=node_id,
            meta={"severity": severity},
        )

    @classmethod
    def stagnation(cls, text: str, rounds_stagnant: int = 0, node_id: str = "") -> "Signal":
        """构造系统停滞信号。"""
        return cls(
            format="stagnation_signal",
            text=text,
            node_id=node_id,
            meta={"rounds_stagnant": rounds_stagnant},
        )

    @classmethod
    def routing_gap(cls, text: str, recent_fails: int = 0, node_id: str = "") -> "Signal":
        """构造路由盲区信号。"""
        return cls(
            format="routing_gap_signal",
            text=text,
            node_id=node_id,
            meta={"recent_fails": recent_fails},
        )

    @classmethod
    def evolution(cls, text: str, effective: bool = True, mutation_type: str = "", node_id: str = "") -> "Signal":
        """构造进化结论信号。"""
        return cls(
            format="evolution_signal",
            text=text,
            node_id=node_id,
            meta={"effective": effective, "mutation_type": mutation_type},
        )
