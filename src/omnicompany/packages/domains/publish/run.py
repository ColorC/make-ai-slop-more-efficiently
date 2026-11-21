# [OMNI] origin=ai-ide domain=publish ts=2026-06-15T00:00:00Z type=cli_entry status=active
# [OMNI] summary="publish domain 的 bindings 工厂: 节点 ID → Router 实例。"
# [OMNI] why="框架级统一:Team 的节点要绑到具体 Worker。run.py 出 bindings, 与 team.py 节点 id 对齐。"
# [OMNI] tags=publish,run,bindings,backup

from __future__ import annotations

from typing import Any

from omnicompany.runtime.routing.router import Router


def build_teamworkspace_snapshot_bindings(input_dict: dict[str, Any] | None = None) -> dict[str, Router]:
    """publish.teamworkspace_snapshot 的节点 ID → Router 绑定(3 节点确定性管线)。"""
    # 顺带把 Format 契约登记进全局 registry(供 omni formats / 校验)
    try:
        from omnicompany.protocol.format import get_format_registry
        from .formats import register_formats
        register_formats(get_format_registry())
    except Exception:
        pass  # 登记失败不阻断管线运行(formats 是契约元数据)

    from .routers.pipeline import CommitPush, ScanSource, StageMirror

    return {
        "scan": ScanSource(),
        "stage": StageMirror(),
        "commit_push": CommitPush(),
    }
