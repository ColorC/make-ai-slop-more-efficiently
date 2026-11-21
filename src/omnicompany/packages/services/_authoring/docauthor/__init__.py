# [OMNI] origin=claude-code domain=services/docauthor ts=2026-04-25T00:00:00Z type=config
# [OMNI] material_id="material:authoring.docauthor.module_aggregate.exports.py"
"""docauthor — 自动文档作者 service (bus 驱动).

- Phase A: ManifestAuthorWorker (单 Worker · 单次 LLM)
- Phase B: + DesignDocAuthorWorker + DocReviewerWorker + 同步 harness refine 循环
- Phase C: **全 bus 驱动** (MaterialDispatcher + SQLiteBus 持久化), 加 2 Relauncher + FinalLander,
  放弃同步 harness. Reviewer **不打分**, 保留完整 issue + evidence.

用法:
    # CLI 入口 (推荐)
    omni docauthor scan                        # 列出需要 docauthor 处理的目标
    omni docauthor run manifest <svc>          # 跑单 manifest job
    omni docauthor run design <pkg>            # 跑单 design job
    omni docauthor run-all                     # 全量处理所有赤字目标

    # Python 入口 (程序化)
    from omnicompany.packages.services._authoring.docauthor.team import build_team, run_job
    events = await run_job(kind="manifest", target="src/.../foo", max_refine_iters=1)
"""
from __future__ import annotations

from .workers.manifest_author import ManifestAuthorWorker
from .workers.design_author import DesignDocAuthorWorker
from .workers.reviewer import DocReviewerWorker
from .workers.relauncher import ManifestRefineRelauncher, DesignRefineRelauncher
from .workers.final_lander import FinalLanderWorker
from .formats import (
    MANIFEST_REQUEST, MANIFEST_DRAFT,
    DESIGN_REQUEST, DESIGN_DRAFT,
    REVIEW_REQUEST, REVIEW_VERDICT,
    JOB_FINAL,
    register_formats,
)


__all__ = [
    "ManifestAuthorWorker",
    "DesignDocAuthorWorker",
    "DocReviewerWorker",
    "ManifestRefineRelauncher",
    "DesignRefineRelauncher",
    "FinalLanderWorker",
    "MANIFEST_REQUEST", "MANIFEST_DRAFT",
    "DESIGN_REQUEST", "DESIGN_DRAFT",
    "REVIEW_REQUEST", "REVIEW_VERDICT",
    "JOB_FINAL",
    "register_formats",
]
