# [OMNI] origin=claude-code domain=dashboard/boss_sight/services ts=2026-06-25T00:00:00Z type=service
# [OMNI] material_id="material:dashboard.boss_sight.services.active_context.py"
"""active_context —— dashboard 级"当前选中的对话/上下文"共享态(跟随脊梁)。

权威: omnicompany/docs/plans/dashboard/[2026-06-24]MULTIAGENT-AND-REVIEW-REDESIGN/plan.md (P0)

把 reviewstage 现成的 "active_material 广播→所有面跟随" 范式抬到对话层:
谁设了 active_context(VSCode 页签焦点解析器 / multiagent view 点选 / 审阅台),
就广播给所有订阅者(审阅台跟随视图、peek 面板)。本模块只管 set/get/subscribe,
WS 广播由路由层把 subscribe 回调接到现有 stream hub。
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable

_lock = threading.Lock()
_current: dict[str, Any] = {"session_id": None, "kind": None, "source": None, "ts": 0.0}
_subscribers: list[Callable[[dict[str, Any]], None]] = []

# 合法 kind: conversation(claude/codex 对话) / material(审阅材料) / plan
_VALID_KINDS = {"conversation", "material", "plan"}


def get_active() -> dict[str, Any]:
    with _lock:
        return dict(_current)


def set_active(session_id: str | None, kind: str = "conversation", source: str = "ui") -> dict[str, Any]:
    """设当前选中上下文并广播。session_id=None 表示清空。"""
    k = kind if kind in _VALID_KINDS else "conversation"
    with _lock:
        # 同一上下文重复 set 不重复广播(防抖)。
        if _current.get("session_id") == session_id and _current.get("kind") == k:
            _current["source"] = source
            _current["ts"] = time.time()
            return dict(_current)
        _current.update(session_id=session_id, kind=k, source=source, ts=time.time())
        snap = dict(_current)
    for cb in list(_subscribers):
        try:
            cb(snap)
        except Exception:  # noqa: BLE001 — 单个订阅者失败不该挡住其余
            pass
    return snap


def subscribe(cb: Callable[[dict[str, Any]], None]) -> Callable[[], None]:
    """订阅 active_context 变化;返回取消订阅的闭包。"""
    _subscribers.append(cb)

    def _unsub() -> None:
        try:
            _subscribers.remove(cb)
        except ValueError:
            pass

    return _unsub


def _reset_for_test() -> None:
    """测试用:清状态与订阅者。"""
    with _lock:
        _current.update(session_id=None, kind=None, source=None, ts=0.0)
    _subscribers.clear()
