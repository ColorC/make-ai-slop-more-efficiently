# [OMNI] origin=ai-ide domain=services/_core/configurable ts=2026-05-02T07:30:00Z type=service status=active agent=ai-ide-current
# [OMNI] summary="ToolSpec frozen dataclass + ConfigurableTool / ConfigurableAsyncTool"
# [OMNI] why="跟 AgentSpec / HookSpec 同路线. tool 配置 (id/操作性质/CONSUMED/PRODUCED meta_io/状态绑定) 全字段化"
# [OMNI] tags=configurable,tool,spec
# [OMNI] material_id="material:core.configurable.tool_specification.definitions.py"
"""ConfigurableTool 定义.

跟 ConfigurableAgent + ConfigurablePeriodicHook 同路线.

protocol 层 BaseTool / AsyncBaseTool 是基类, 这层加配置驱动外包装.
对应 omnicompany 6.6: tool 概念 (操作绑定状态 + 元 IO 注册).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar, Mapping

from omnicompany.protocol.tool import BaseTool, AsyncBaseTool


@dataclass(frozen=True)
class ToolSpec:
    """tool 配置 spec, 跟用户原始需求 6.6 (tool 模板) 对齐.

    必填: id + name. 其他字段空集合默认.
    """

    # 注册信息
    id: str
    name: str
    domain: str = ""
    parent_worker_kind: str = "tool"
    registry_namespace: str = "services.tool.instances"

    # 工具操作性质 (tag 列表) — 跟 agent_first.md §8 工具范式对齐
    operation_kind: str = "read"
    """read (输入观察) / write (输出操作) / mutate (读改一体)."""

    operation_tags: tuple[str, ...] = ()
    """例 ('fs', 'idempotent', 'binary'). 跟元 IO 的 tags 对齐."""

    # 元 IO 声明 (跟 SingleToolRouter.CONSUMED/PRODUCED_META_IO 对齐)
    consumed_meta_io: tuple[str, ...] = ()
    produced_meta_io: tuple[str, ...] = ()

    # 输入 schema
    input_schema: Mapping[str, Any] = field(default_factory=dict)
    """JSON schema 描述工具参数."""

    # 状态检查 (用户原话"操作应当和状态绑定")
    state_check_precondition: str = ""
    state_check_postcondition: str = ""
    state_check_invariant: str = ""

    # 不允许自定义代码 (硬规则)
    allow_custom_code: bool = False

    # 红绿测试基线
    test_baseline: Mapping[str, Any] = field(default_factory=dict)


class ConfigurableTool(BaseTool):
    """配置驱动同步 tool. 子类设 SPEC + override execute() 即可.

    框架自动:
    - 把 SPEC 字段曝光给守护 / agent / 注册中心
    - 自动跟元 IO 注册表联动 (consumed_meta_io / produced_meta_io)
    """

    SPEC: ClassVar[ToolSpec | None] = None

    @classmethod
    def _resolve_spec(cls) -> ToolSpec:
        if cls.SPEC is None:
            raise RuntimeError(
                f"{cls.__name__}.SPEC is None. ConfigurableTool 子类必须设 SPEC."
            )
        return cls.SPEC

    def execute(self, input_data: Any) -> dict[str, Any]:
        """业务子类必须 override (跟 BaseTool 签名一致)."""
        raise NotImplementedError(
            f"{type(self).__name__} 必须 override execute(input_data). "
            f"SPEC={self._resolve_spec().id if self.SPEC else None}"
        )


class ConfigurableAsyncTool(AsyncBaseTool):
    """配置驱动异步 tool."""

    SPEC: ClassVar[ToolSpec | None] = None

    @classmethod
    def _resolve_spec(cls) -> ToolSpec:
        if cls.SPEC is None:
            raise RuntimeError(
                f"{cls.__name__}.SPEC is None. ConfigurableAsyncTool 子类必须设 SPEC."
            )
        return cls.SPEC

    async def execute(self, input_data: Any) -> dict[str, Any]:
        raise NotImplementedError(
            f"{type(self).__name__} 必须 override async execute(input_data). "
            f"SPEC={self._resolve_spec().id if self.SPEC else None}"
        )
