# [OMNI] origin=human ts=2026-04-08T03:23:35Z
# [OMNI] material_id="material:protocol.perception_hooks.interfaces.py"
"""Hook — 六元语义信号模型的感知层（V1.1 §1 唯一权威定义）

Hook = 感官。在特定条件下观测环境并发出 Signal。
只感知，不决策，不修改状态。

两种形态：
  PeriodicHook — 周期性轮询（每 N 轮）
  EventHook    — 事件驱动（特定事件发生后立即触发）

全局 import: from omnicompany.protocol import PeriodicHook, EventHook
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from omnicompany.protocol.signal import Signal


class BaseHook(ABC):
    """所有 Hook 的基类。"""

    @abstractmethod
    def should_poll(self, round_num: int) -> bool:
        """判断本轮是否应触发观测。"""
        ...


class PeriodicHook(BaseHook):
    """周期性感知 Hook：每 N 轮调用一次 poll()。

    实现规范：
    - poll() 只读 DB/状态，不写
    - 不调用 LLM
    - Signal.text 必须是自然语言（不是裸数值）
    """

    @abstractmethod
    async def poll(self, db_path: str, round_num: int) -> "list[Signal]":
        """观测当前状态，返回触发的 Signal list（空 = 未触发）。"""
        ...

    def should_poll(self, round_num: int) -> bool:
        return True  # 子类覆盖以实现冷却逻辑


class EventHook(BaseHook):
    """事件驱动 Hook：特定事件发生后立即触发。

    实现规范：
    - on_event() 在事件写入后同步调用
    - 保持内部状态（累计失败次数等）
    - 触发后重置状态
    """

    @abstractmethod
    def on_event(self, event: dict) -> "list[Signal]":
        """响应事件，返回触发的 Signal list（空 = 未触发）。"""
        ...

    def should_poll(self, round_num: int) -> bool:
        return True  # EventHook 不走 poll()，此方法保持接口兼容
