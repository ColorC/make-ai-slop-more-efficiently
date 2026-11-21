# [OMNI] origin=claude-code domain=services/docauthor ts=2026-04-25T00:00:00Z type=config
# [OMNI] material_id="material:authoring.docauthor.cli_entrypoint.bindings.py"
"""docauthor service 入口 · build_bindings() + bus 驱动 run_job.

Phase C: 6 Worker 全 bus 驱动 (MaterialDispatcher + SQLiteBus).
"""
from __future__ import annotations

from typing import Any

from omnicompany.runtime.routing.router import Router

from .team import run_job, build_dispatcher, build_team_workers, summarize_events


def build_bindings(input_dict: dict[str, Any] | None = None) -> dict[str, Router]:
    """Pipeline node→Router 绑定. 适配 `omni run docauthor` CLI 入口.

    lazy import 避免启动加载 LLMClient.
    """
    from .workers.manifest_author import ManifestAuthorWorker
    from .workers.design_author import DesignDocAuthorWorker
    from .workers.reviewer import DocReviewerWorker
    from .workers.relauncher import ManifestRefineRelauncher, DesignRefineRelauncher
    from .workers.final_lander import FinalLanderWorker
    return {
        "manifest_author":          ManifestAuthorWorker(),
        "design_author":            DesignDocAuthorWorker(),
        "reviewer":                 DocReviewerWorker(),
        "manifest_refine_relauncher": ManifestRefineRelauncher(),
        "design_refine_relauncher":   DesignRefineRelauncher(),
        "final_lander":             FinalLanderWorker(),
    }


__all__ = [
    "build_bindings",
    "run_job",
    "build_dispatcher",
    "build_team_workers",
    "summarize_events",
]
