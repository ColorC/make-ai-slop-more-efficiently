# [OMNI] origin=claude-code domain=services/_diagnosis/ux_audit ts=2026-06-30T00:00:00Z type=team status=active
# [OMNI] summary="ux_audit · build_bindings(node_id → Worker 实例)。"
# [OMNI] material_id="material:services._diagnosis.ux_audit.run"
"""ux_audit Team · 节点绑定。"""
from __future__ import annotations

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.format import create_builtin_registry

from .formats import register_formats
from .nodes import InteractionEnumerator, InfoEnumerator, NavEnumerator, Consolidator


def _registry():
    registry = create_builtin_registry()
    register_formats(registry)
    return registry


def build_bindings(input_dict: dict | None = None) -> dict[str, Worker]:
    _registry()
    return {
        "InteractionEnumerator": InteractionEnumerator(),
        "InfoEnumerator": InfoEnumerator(),
        "NavEnumerator": NavEnumerator(),
        "Consolidator": Consolidator(),
    }
