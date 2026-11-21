# [OMNI] origin=ai-ide domain=services/_core/configurable ts=2026-05-02T07:30:00Z type=service status=active agent=ai-ide-current
# [OMNI] summary="HookSpec frozen dataclass + ConfigurablePeriodicHook / ConfigurableEventHook"
# [OMNI] why="跟 AgentSpec / ConfigurableAgent 同路线. hook 配置 (id/触发源/输出 material/工作区/审批门禁) 全字段化"
# [OMNI] tags=configurable,hook,spec
# [OMNI] material_id="material:core.configurable.hook_specification.definitions.py"
"""ConfigurableHook 定义.

跟 ConfigurableAgent + AgentSpec 同路线. hook v2 形态.

PeriodicHook / EventHook 是 protocol 层基类, ConfigurablePeriodicHook /
ConfigurableEventHook 是配置驱动外包装, 业务子类一行 SPEC 即可.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar, Mapping, Sequence

from omnicompany.protocol.hook import PeriodicHook, EventHook
from omnicompany.protocol.signal import Signal


# ── HookSpec dataclass ─────────────────────────────────────────────


@dataclass(frozen=True)
class HookSpec:
    """hook 配置 spec, 跟用户原始需求 6.5 (hook 模板) 对齐.

    必填: id + name. 其他字段空集合默认.
    """

    # 注册信息
    id: str
    name: str
    domain: str = ""
    parent_worker_kind: str = "hook"
    registry_namespace: str = "services.hook.instances"

    # 触发源
    trigger_kind: str = "periodic"
    """periodic (PeriodicHook 子类用) / event (EventHook 子类用)."""

    poll_every_rounds: int = 1
    """PeriodicHook: 每 N 轮触发一次 (1 = 每轮)."""

    event_filter: Mapping[str, Any] = field(default_factory=dict)
    """EventHook: 触发的事件过滤条件 (event_type / source / tags 等)."""

    # 产出 material (有入无出 — hook 产 source material 触发下游)
    output_materials: tuple[str, ...] = ()
    primary_output: str = ""

    # 上下文 (hook 周期性观测的资源)
    observed_resources: tuple[str, ...] = ()
    """hook 观察的资源路径列表 (例 'data/_runtime/...' 文件路径)."""

    # 审批门禁
    gates: Sequence[Mapping[str, Any]] = ()

    # 严重度阈值
    severity_threshold: str = "info"
    """info / warn / error - hook 触发的 Signal 默认 severity 阈值."""

    # 不允许自定义代码 (跟 agent 同硬规则)
    allow_custom_code: bool = False

    # 红绿测试基线
    test_baseline: Mapping[str, Any] = field(default_factory=dict)


# ── ConfigurablePeriodicHook + ConfigurableEventHook ──────────────────


class ConfigurablePeriodicHook(PeriodicHook):
    """配置驱动 PeriodicHook. 子类只需声明 SPEC = HookSpec(...) 即可跑.

    重写 poll() 走业务逻辑. 框架自动:
    - 走 SPEC.poll_every_rounds 控制 should_poll
    - 走 SPEC.severity_threshold 过滤产出 Signal
    """

    SPEC: ClassVar[HookSpec | None] = None

    @classmethod
    def _resolve_spec(cls) -> HookSpec:
        if cls.SPEC is None:
            raise RuntimeError(
                f"{cls.__name__}.SPEC is None. ConfigurablePeriodicHook 子类必须设 SPEC."
            )
        return cls.SPEC

    def should_poll(self, round_num: int) -> bool:
        spec = self._resolve_spec()
        if spec.poll_every_rounds <= 1:
            return True
        return round_num > 0 and round_num % spec.poll_every_rounds == 0

    async def poll(self, db_path: str, round_num: int) -> list[Signal]:
        """业务子类必须 override. 默认返回空列表."""
        return []


class ConfigurableEventHook(EventHook):
    """配置驱动 EventHook. 子类设 SPEC + override on_event() 即可.

    框架自动:
    - 走 SPEC.event_filter 过滤事件 (event_type / source 匹配)
    - 走 SPEC.severity_threshold 过滤产出 Signal
    """

    SPEC: ClassVar[HookSpec | None] = None

    @classmethod
    def _resolve_spec(cls) -> HookSpec:
        if cls.SPEC is None:
            raise RuntimeError(
                f"{cls.__name__}.SPEC is None. ConfigurableEventHook 子类必须设 SPEC."
            )
        return cls.SPEC

    def _matches_filter(self, event: dict) -> bool:
        spec = self._resolve_spec()
        f = dict(spec.event_filter)
        if not f:
            return True
        for k, v in f.items():
            if event.get(k) != v:
                return False
        return True

    def on_event(self, event: dict) -> list[Signal]:
        """业务子类必须 override. 默认空."""
        if not self._matches_filter(event):
            return []
        return []
