# [OMNI] origin=claude-code domain=software_engineering/generated ts=2026-04-08T03:23:42Z
# [OMNI] material_id="material:domains.software_engineering.generated.pipeline_bindings.builder.py"
from typing import Any

from omnicompany.runtime.routing.router import Router

from .pipeline import build_pipeline
from .routers import CalculateStatsRouter, ValidateInputRouter
from .formats import register_formats

# 重新导出 build_pipeline
__all__ = ["build_pipeline", "build_bindings", "register_formats"]


def build_bindings(input_dict: dict[str, Any] | None = None) -> dict[str, Router]:
    """
    构建节点 ID 到 Router 实例的绑定

    Args:
        input_dict: 可选的输入参数字典，用于配置 Router（当前管线无需配置）

    Returns:
        dict[str, Router]: 节点 ID 到 Router 实例的映射
    """
    _ = input_dict  # 当前管线不使用输入配置

    bindings: dict[str, Router] = {
        "validate_input_node": ValidateInputRouter(),
        "calculate_stats_node": CalculateStatsRouter(),
    }

    return bindings