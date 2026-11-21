# [OMNI] origin=claude-code domain=services/agent ts=2026-04-18
# [OMNI] material_id="material:core.agent.bus_event_emitter.implementation.py"
"""services.agent._bus — Router 事件发射助手

每次 Router.run() 前后，走这里两条事件：
- router.<name>.input   (payload = {format_id, data})
- router.<name>.output  (payload = {format_id, data, verdict_kind})

事件信封是 FactoryEvent（omnicompany.protocol.events），trace_id 贯穿整条
Agent Loop，source=agent.{router_name}。

**为什么不把 emit 塞到 Router 基类？**
- Router 基类（runtime/routing/router.py）被全项目 300+ 个 Router 继承，
  加 bus 强制会破坏现有生态（阶段 D 才统一迁移）
- 本阶段用 mixin 风格的 helper，只有 services/agent 下的 Router 用
- 阶段 D 完成后再考虑把 emit 上提到 Router 基类

**payload 大小策略**：
- input/output data 直接落盘（不截断），因为 Format 本身应是"一次有意义的事件"
- 单条 event 超过 64KB 时告警（Format 边界应该更小）
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

_PAYLOAD_WARN_BYTES = 64 * 1024  # 64KB


def _material_tags(*tags: str) -> list[str]:
    out = ["omni.material", "agent.material"]
    for tag in tags:
        if tag and tag not in out:
            out.append(tag)
    return out


def _safe_serialize(data: Any) -> Any:
    """把 payload 转成 JSON-safe 形态。

    - pydantic BaseModel → .model_dump()
    - dataclass → 通过 asdict（失败则 str()）
    - 其他不可序列化对象 → str()
    """
    try:
        json.dumps(data, default=str, ensure_ascii=False)
        return data
    except (TypeError, ValueError):
        if hasattr(data, "model_dump"):
            return data.model_dump()
        if hasattr(data, "__dict__"):
            return {k: _safe_serialize(v) for k, v in vars(data).items()}
        return str(data)


def _check_size(kind: str, router_name: str, payload: dict) -> None:
    try:
        size = len(json.dumps(payload, default=str, ensure_ascii=False).encode("utf-8"))
    except Exception:
        return
    if size > _PAYLOAD_WARN_BYTES:
        logger.warning(
            "[services.agent] router event oversized: %s %s %d bytes (>64KB)",
            router_name, kind, size,
        )


async def emit_router_input(
    bus: Any,
    *,
    trace_id: str,
    router_name: str,
    format_id: str,
    data: Any,
    parent_id: str | None = None,
) -> str | None:
    """发 router.<name>.input 事件。返回 event_id（失败返回 None）。"""
    if bus is None:
        return None
    from omnicompany.protocol.events import FactoryEvent

    payload = {
        "router": router_name,
        "format_id": format_id,
        "direction": "input",
        "data": _safe_serialize(data),
    }
    _check_size("input", router_name, payload)
    try:
        event = FactoryEvent(
            trace_id=trace_id,
            parent_id=parent_id,
            event_type=f"router.{router_name}.input",
            source=f"agent.{router_name}",
            payload=payload,
            tags=_material_tags("agent.router.input", f"format:{format_id}"),
        )
        return await bus.publish(event)
    except Exception as exc:
        logger.warning("[services.agent] emit_router_input failed: %s", exc)
        return None


async def emit_router_output(
    bus: Any,
    *,
    trace_id: str,
    router_name: str,
    format_id: str,
    data: Any,
    verdict_kind: str,
    parent_id: str | None = None,
) -> str | None:
    """发 router.<name>.output 事件。verdict_kind 取自 Verdict.kind.value。"""
    if bus is None:
        return None
    from omnicompany.protocol.events import FactoryEvent

    payload = {
        "router": router_name,
        "format_id": format_id,
        "direction": "output",
        "verdict_kind": verdict_kind,
        "data": _safe_serialize(data),
    }
    _check_size("output", router_name, payload)
    try:
        event = FactoryEvent(
            trace_id=trace_id,
            parent_id=parent_id,
            event_type=f"router.{router_name}.output",
            source=f"agent.{router_name}",
            payload=payload,
            tags=_material_tags("agent.router.output", f"format:{format_id}"),
        )
        return await bus.publish(event)
    except Exception as exc:
        logger.warning("[services.agent] emit_router_output failed: %s", exc)
        return None


async def emit_agent_signal(
    bus: Any,
    *,
    trace_id: str,
    event_type: str,
    source: str,
    payload: dict,
    parent_id: str | None = None,
    tags: list[str] | None = None,
) -> str | None:
    """发通用 agent 信号事件（非 Router input/output）。

    用于 AgentNodeLoop 自身的 turn.start / turn.end / budget_exhaust 等。
    """
    if bus is None:
        return None
    from omnicompany.protocol.events import FactoryEvent

    try:
        event = FactoryEvent(
            trace_id=trace_id,
            parent_id=parent_id,
            event_type=event_type,
            source=source,
            payload=_safe_serialize(payload) if not isinstance(payload, dict) else payload,
            tags=_material_tags(*(tags or [])),
        )
        return await bus.publish(event)
    except Exception as exc:
        logger.warning("[services.agent] emit_agent_signal failed: %s", exc)
        return None
