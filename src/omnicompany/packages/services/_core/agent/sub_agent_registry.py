# [OMNI] origin=claude-code domain=services/agent ts=2026-05-04 type=infrastructure
# [OMNI] material_id="material:core.agent.sub_agent.registry.py"
"""SubAgentRegistry — 注册可被 SubAgentRouter spawn 的子 agent 类型.

CC 对齐 (build-src/src/coordinator/coordinatorMode.ts):
  Agent({description, subagent_type, prompt}) 创建子 agent 跑独立任务.
  subagent_type → 注册的 AgentNodeLoop 子类.

我们的统一抽象 — 之前 repo/learner / landmark_picker 都用 ad-hoc 方式
spawn (asyncio.new_event_loop + 直接构造类). 现在通过这个 registry 走.

用法:
  SubAgentRegistry.register("code-reviewer", CodeReviewerAgent, description="...")
  SubAgentRegistry.register("explorer", ExplorerAgent, description="...")

agent prompt 里的 subagent_type 由 SubAgentRouter 查 registry.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class _SubAgentEntry:
    name: str
    agent_class: type
    description: str
    # 可选: 限制工具集 / 自定义 model / 等. 现在最简, 直接用 agent_class 自身的默认.
    config_overrides: dict[str, Any] = field(default_factory=dict)


class SubAgentRegistry:
    """进程级单例 subagent_type → agent class 注册表."""

    _entries: dict[str, _SubAgentEntry] = {}

    @classmethod
    def register(
        cls,
        name: str,
        agent_class: type,
        *,
        description: str = "",
        config_overrides: dict[str, Any] | None = None,
    ) -> None:
        """注册一个 subagent_type. 重复注册会覆盖 (同 type 多次注册 last-wins, 用于 hot-reload)."""
        if not name or not isinstance(name, str):
            raise ValueError(f"sub-agent name must be non-empty string, got {name!r}")
        cls._entries[name] = _SubAgentEntry(
            name=name,
            agent_class=agent_class,
            description=description or (agent_class.__doc__ or "").splitlines()[0] if agent_class.__doc__ else "",
            config_overrides=dict(config_overrides or {}),
        )

    @classmethod
    def unregister(cls, name: str) -> bool:
        """移除注册. True = 之前存在."""
        return cls._entries.pop(name, None) is not None

    @classmethod
    def get(cls, name: str) -> _SubAgentEntry | None:
        return cls._entries.get(name)

    @classmethod
    def list_types(cls) -> list[str]:
        return sorted(cls._entries.keys())

    @classmethod
    def list_descriptions(cls) -> list[tuple[str, str]]:
        """返回 [(name, description), ...] 用于在 SubAgentRouter DESCRIPTION 自动列出."""
        return [(e.name, e.description) for e in cls._entries.values()]

    @classmethod
    def clear(cls) -> None:
        """测试用 — 清空注册表."""
        cls._entries.clear()


__all__ = ["SubAgentRegistry"]
