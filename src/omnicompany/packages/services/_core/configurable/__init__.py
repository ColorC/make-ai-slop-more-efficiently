# [OMNI] origin=ai-ide domain=services/_core/configurable ts=2026-05-02T07:30:00Z type=service status=active agent=ai-ide-current
# [OMNI] summary="ConfigurableHook + ConfigurableTool - 跟 ConfigurableAgent 同路线的配置驱动外包装"
# [OMNI] why="hook/tool 跟 agent 一样应当配置驱动. 业务侧立新 hook/tool 一行 SPEC 即可, prompt/工作区/触发器走配置. 业务子类需细粒度逻辑可继承 + override"
# [OMNI] tags=configurable,hook,tool,foundation,v2
# [OMNI] material_id="material:core.configurable.package_exports.exports.py"
"""ConfigurableXxx 外包装 (跟 ConfigurableAgent 同路线).

模块布局:
  __init__.py          - 公共入口
  hook_spec.py         - HookSpec dataclass + ConfigurablePeriodicHook / ConfigurableEventHook
  tool_spec.py         - ToolSpec dataclass + ConfigurableTool / ConfigurableAsyncTool

实施 v1 (本包): 把现有 hook/tool 概念配置驱动化, 不动 protocol 层基类
(BaseHook/PeriodicHook/EventHook/BaseTool/AsyncBaseTool).

业务示例 (hook):

    from omnicompany.packages.services._core.configurable import (
        HookSpec, ConfigurablePeriodicHook,
    )

    class MyDailyHook(ConfigurablePeriodicHook):
        SPEC = HookSpec(
            id="mydomain.daily_check",
            name="MyDailyCheck",
            poll_every=1440,   # 每天一次
            output_materials=("mydomain.health_report",),
            ...
        )
"""
from __future__ import annotations

from omnicompany.packages.services._core.configurable.hook_spec import (
    HookSpec,
    ConfigurablePeriodicHook,
    ConfigurableEventHook,
)
from omnicompany.packages.services._core.configurable.tool_spec import (
    ToolSpec,
    ConfigurableTool,
    ConfigurableAsyncTool,
)

__all__ = [
    "HookSpec",
    "ConfigurablePeriodicHook",
    "ConfigurableEventHook",
    "ToolSpec",
    "ConfigurableTool",
    "ConfigurableAsyncTool",
]
