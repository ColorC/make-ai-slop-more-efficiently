# [OMNI] origin=human ts=2026-04-08T03:23:35Z
# [OMNI] material_id="material:protocol.event_envelope.envelope.py"
"""
OmniCompany 统一事件协议

FactoryEvent 是系统中所有信息流动的标准信封。
从任务下发到 Agent 的每一步思考、每一次工具调用，
都被封装为 FactoryEvent 在 Redis Streams 上流转。

设计原则:
1. 身份唯一: ULID 保证可排序 + 全局唯一
2. 因果可追踪: trace_id 贯穿任务全程, parent_id 建立因果链
3. 负载自由: payload 是 dict，具体 schema 由 EventType 约定
4. 编排桥接: contract_id 关联 LGF 节点，打通意图层与执行层
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field
from ulid import ULID


def _ulid_str() -> str:
    return str(ULID())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class EventMetadata(BaseModel):
    """事件附属元数据，用于成本核算与可观测性"""

    # LLM 调用相关
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    model: str | None = None
    cost_usd: float | None = None
    latency_ms: float | None = None

    # 工具调用相关
    tool_name: str | None = None
    duration_ms: float | None = None

    # 自由扩展
    extra: dict[str, Any] = Field(default_factory=dict)


class FactoryEvent(BaseModel):
    """OmniCompany 统一事件信封

    每一个在总线上流动的数据包都是一个 FactoryEvent。
    它是系统的"单一事实来源"的最小单元。

    字段分组:
    - 身份: id, trace_id, parent_id
    - 分类: event_type, source
    - 负载: payload
    - 契约: contract_id
    - 时间: timestamp
    - 元数据: metadata
    """

    # 身份
    id: str = Field(default_factory=_ulid_str)
    """事件唯一 ID (ULID: 可排序 + 时间编码 + 随机)"""

    trace_id: str
    """贯穿整个任务生命周期的追踪 ID。
    从 TASK_INTENT 创建，所有后续事件共享同一 trace_id"""

    parent_id: str | None = None
    """因果链: 触发本事件的上游事件 ID。
    例如 TOOL_RESULT 的 parent_id 指向对应的 TOOL_CALL"""

    # 分类
    event_type: str
    """事件类型 (EventType 枚举值)。
    使用 str 而非 EventType 以支持未来的自定义扩展类型"""

    source: str
    """发射源标识。格式: "{component_type}.{instance_name}"
    例如: "agent.mock-coder", "controller.main", "system.infra-agent" """

    # 负载
    payload: dict[str, Any] = Field(default_factory=dict)
    """事件特定数据。具体结构由 event_type 约定:
    - task.intent:  {"instruction": str, "constraints": dict}
    - agent.tool.call: {"tool": str, "args": dict}
    - agent.state.change: {"from_state": str, "to_state": str}
    等。保持 dict 而非类型化子类，降低协议层耦合"""

    # 契约
    contract_id: str | None = None
    """关联的 LGF 编排节点/边 ID。
    桥接意图编排层与执行层的锚点"""

    # 时间
    timestamp: datetime = Field(default_factory=_utcnow)
    """事件产生时间 (UTC)"""

    # 语义标签
    tags: list[str] = Field(default_factory=list)
    """语义标签，点分层级命名。用于细粒度路由过滤。
    例: ["demogame.benchmark.battle", "unity.lua", "hero.greed"]"""

    # 元数据
    metadata: EventMetadata | None = None
    """附属元数据 (LLM 成本、工具耗时等)。可选"""

    def to_stream_dict(self) -> dict[str, str]:
        """序列化为 Redis Streams XADD 所需的 flat dict (所有 value 为 str)"""
        return {"data": self.model_dump_json()}

    @classmethod
    def from_stream_dict(cls, data: dict[bytes, bytes]) -> FactoryEvent:
        """从 Redis Streams XREAD 返回的 bytes dict 反序列化"""
        raw = data[b"data"]
        return cls.model_validate_json(raw)
