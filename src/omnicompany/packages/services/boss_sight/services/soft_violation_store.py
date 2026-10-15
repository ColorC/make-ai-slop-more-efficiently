# [OMNI] origin=ai-ide ts=2026-05-24 type=infra
# [OMNI] material_id="material:dashboard.boss_sight.services.soft_violation_store.py"
"""SoftViolationStore — 软 guard 违规集中累计 (块 5 R2 / §6.3).

用户原话 §6.3:
> 大部分都是软的, 也就是最终返回的时候再集中看有多少风险操作或者违规写入位置

设计:
- 进程内 dict, 按 subagent_id 累计软违规事件
- chat.py `_make_can_use_tool` deny 时 (软 mode) 调 store.record(...)
- ControllerWaker subagent.completed 时 store.drain(subagent_id) 拿清单附在 inject msg
- 不持久化 — 跨重启不需要, 这是 turn-scope 数据
"""

from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass, field


@dataclass
class SoftViolation:
    subagent_id: str
    tool_name: str
    tool_input_summary: str
    denial_message: str
    recorded_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)


class SoftViolationStore:
    """进程内 violations 累计 + drain on completed."""

    def __init__(self) -> None:
        self._by_subagent: dict[str, list[SoftViolation]] = {}
        self._lock = threading.RLock()

    def record(
        self,
        *,
        subagent_id: str,
        tool_name: str,
        tool_input_summary: str,
        denial_message: str,
    ) -> None:
        with self._lock:
            self._by_subagent.setdefault(subagent_id, []).append(SoftViolation(
                subagent_id=subagent_id,
                tool_name=tool_name,
                tool_input_summary=tool_input_summary[:200],
                denial_message=denial_message[:400],
            ))

    def drain(self, subagent_id: str) -> list[SoftViolation]:
        """取走某 subagent 的所有违规并清空."""
        with self._lock:
            return self._by_subagent.pop(subagent_id, [])

    def peek(self, subagent_id: str) -> list[SoftViolation]:
        """不清空查."""
        with self._lock:
            return list(self._by_subagent.get(subagent_id, []))

    def total_count(self) -> int:
        with self._lock:
            return sum(len(v) for v in self._by_subagent.values())


# Module-level singleton — ccdaemon 同进程共用
_singleton: SoftViolationStore | None = None
_singleton_lock = threading.Lock()


def get_soft_violation_store() -> SoftViolationStore:
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = SoftViolationStore()
        return _singleton


__all__ = ["SoftViolation", "SoftViolationStore", "get_soft_violation_store"]
