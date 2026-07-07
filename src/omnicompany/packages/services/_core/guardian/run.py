# [OMNI] origin=omnicompany domain=omnicompany/guardian ts=2026-04-05T17:04:51Z
# [OMNI] material_id="material:core.guardian.run.binding_builder.py"
"""guardian.run — Bindings 构建 + 便捷入口"""

from __future__ import annotations

from typing import Any

from omnicompany.runtime.routing.router import Router


class _NoopBus:
    """Constructor-only bus; TeamRunner injects the real EventBus before run()."""

    async def publish(self, event: Any) -> str:
        return "noop_event_id"

    def emit(self, *args: Any, **kwargs: Any) -> None:
        return None


def build_bindings(input_dict: dict[str, Any] | None = None) -> dict[str, Router]:
    """构建管线节点→Router 绑定。"""
    from omnicompany.packages.services._core.guardian.routers import (
        FsScannerRouter,
        ArchAuditorRouter,
        HealthReporterRouter,
    )

    project_root = None
    model = None
    if input_dict:
        project_root = input_dict.get("project_root")
        model = input_dict.get("model")

    return {
        "fs_scanner": FsScannerRouter(project_root=project_root),
        "arch_auditor": ArchAuditorRouter(),
        "health_reporter": HealthReporterRouter(model=model, bus=_NoopBus()),
    }


# build_patrol_bindings 移除 (2026-05-05 诊断重制 step 8) — patrol_worker LLM 巡查归档,
# 概念并入 doctor _hypothesis/. guardian 留纯规则部分.
