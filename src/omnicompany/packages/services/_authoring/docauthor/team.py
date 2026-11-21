# [OMNI] origin=claude-code domain=services/docauthor ts=2026-04-25T00:00:00Z type=config
# [OMNI] material_id="material:authoring.docauthor.bus_orchestrator.job_runner.py"
"""docauthor Team 组装 + bus 驱动 run_job 入口.

对齐用户 2026-04-25 硬指示:
  "所有内容都要走事件总线进行存储和调度, 不要越过事件总线做调度, 这是最重要的."

本文件是 bus 驱动的真·入口. 旧的 harness (run_phase_a/b.py) 同步 worker.run() 作
Phase A/B 演练保留作参考, 不再用于 Phase C+ 真跑.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from omnicompany.bus.base import EventBus
from omnicompany.bus.sqlite import SQLiteBus
from omnicompany.packages.services._core.omnicompany import MaterialDispatcher
from omnicompany.protocol.events import FactoryEvent
from omnicompany.runtime.routing.router import Router


def build_team_workers(
    *,
    repo_root: Path | None = None,
    dry_run: bool = False,
) -> list[Router]:
    """Phase C docauthor Team · 6 Worker bus 驱动组.

    拓扑 (订阅图由 FORMAT_IN/OUT 推导, MaterialDispatcher 按 Material 激活):

        docauthor.manifest-request ──► ManifestAuthorWorker ──► docauthor.manifest-draft
        docauthor.design-request   ──► DesignDocAuthorWorker ──► docauthor.design-draft
                                                                      │
                                                                      │ OR 模式任一到即激活
                                                                      ▼
                                                            DocReviewerWorker
                                                                      │
                                                                      ▼
                                                           docauthor.review-verdict
                                                       ┌──────────────┼──────────────┐
                                                       ▼              ▼              ▼
                                          FinalLanderWorker   ManifestRefine    DesignRefine
                                                       │       Relauncher       Relauncher
                                                       ▼              │              │
                                               docauthor.job-final    │              │
                                               (sink · 落盘 src/)      ▼              ▼
                                                                    manifest-    design-
                                                                    request       request
                                                                    (子 job ·     (子 job)
                                                                     _emit_as_
                                                                     new_job)

    **互斥**: FinalLander 与对应 kind 的 Relauncher 对同一 review-verdict 各自激活一次,
    但只有一个 Verdict.PASS (另一个 FAIL 被 dispatcher 跳过 emit). 判定条件互补:
      - FinalLander PASS iff passed=True OR iter>=max
      - Relauncher   PASS iff passed=False AND iter<max AND target_type 匹配
    """
    from .workers.manifest_author import ManifestAuthorWorker
    from .workers.design_author import DesignDocAuthorWorker
    from .workers.readme_author import ReadmeAuthorWorker
    from .workers.skill_author import SkillAuthorWorker
    from .workers.reviewer import DocReviewerWorker
    from .workers.relauncher import (
        ManifestRefineRelauncher, DesignRefineRelauncher,
        ReadmeRefineRelauncher, SkillRefineRelauncher,
    )
    from .workers.final_lander import FinalLanderWorker

    return [
        ManifestAuthorWorker(repo_root=repo_root),
        DesignDocAuthorWorker(repo_root=repo_root),
        ReadmeAuthorWorker(repo_root=repo_root),
        SkillAuthorWorker(repo_root=repo_root),
        DocReviewerWorker(repo_root=repo_root),
        ManifestRefineRelauncher(),
        DesignRefineRelauncher(),
        ReadmeRefineRelauncher(),
        SkillRefineRelauncher(),
        FinalLanderWorker(repo_root=repo_root, dry_run=dry_run),
    ]


def build_dispatcher(
    *,
    repo_root: Path | None = None,
    dry_run: bool = False,
    bus: EventBus | None = None,
    max_iterations: int = 200,
) -> MaterialDispatcher:
    """构造 MaterialDispatcher + 注册 docauthor 全套 Worker.

    bus 默认走 SQLiteBus (data/events.db · 与 omnicompany 主总线同一盘 · 所有 docauthor
    事件统一审计). 传入 MemoryBus 仅用于 unit 测试.

    max_iterations 默认 200 (远大于 3 refine × 6 worker ≈ 40 · 铁律 B 宽松预算).
    """
    workers = build_team_workers(repo_root=repo_root, dry_run=dry_run)
    if bus is None:
        bus = SQLiteBus()  # 默认 data/events.db
    return MaterialDispatcher(workers=workers, bus=bus, max_iterations=max_iterations)


async def run_job(
    *,
    kind: str,
    target: str,
    max_refine_iters: int = 1,
    notes_hint: str | None = None,
    repo_root: Path | None = None,
    dry_run: bool = False,
    bus: EventBus | None = None,
) -> list[FactoryEvent]:
    """bus 驱动跑一个 docauthor job.

    Args:
        kind: "manifest" 或 "design"
        target: 仓库相对路径 (src/omnicompany/packages/services/foo 或 domains/..)
        max_refine_iters: refine 预算上限
        notes_hint: 可选 · Author prompt 里作提示
        dry_run: True 时 FinalLander 不写盘 (观察事件流用)
        bus: 传 None 用 SQLiteBus 默认 data/events.db

    Returns:
        list[FactoryEvent] — job 产生的所有事件 (含初始 request · 全部 draft · verdict · job-final).
        每个事件已持久化到 bus (SQLiteBus 即 data/events.db).
    """
    if kind not in {"manifest", "design", "readme", "skill"}:
        raise ValueError(f"kind must be 'manifest'|'design'|'readme'|'skill', got {kind!r}")

    dispatcher = build_dispatcher(repo_root=repo_root, dry_run=dry_run, bus=bus)

    if kind == "manifest":
        initial_mid = "docauthor.manifest-request"
        payload: dict[str, Any] = {
            "target_service_path": target,
            "iter": 0,
            "max_refine_iters": max_refine_iters,
        }
    elif kind == "design":
        initial_mid = "docauthor.design-request"
        payload = {
            "target_package_path": target,
            "iter": 0,
            "max_refine_iters": max_refine_iters,
            "upgrade_from_skeleton": True,
        }
    elif kind == "readme":
        initial_mid = "docauthor.readme-request"
        payload = {
            "target_package_path": target,
            "iter": 0,
            "max_refine_iters": max_refine_iters,
        }
    else:  # skill
        initial_mid = "docauthor.skill-request"
        payload = {
            "target_package_path": target,
            "iter": 0,
            "max_refine_iters": max_refine_iters,
        }
    if notes_hint:
        payload["notes_hint"] = notes_hint

    events = await dispatcher.run_job(
        initial_material_id=initial_mid,
        initial_payload=payload,
    )
    return events


def summarize_events(events: list[FactoryEvent]) -> dict[str, Any]:
    """把 dispatcher.run_job 返回的 events 压成摘要 (非用于下游决策, 仅观测)."""
    by_type: dict[str, int] = {}
    final_event: FactoryEvent | None = None
    verdicts: list[FactoryEvent] = []
    for ev in events:
        by_type[ev.event_type] = by_type.get(ev.event_type, 0) + 1
        if ev.event_type == "docauthor.job-final":
            final_event = ev
        elif ev.event_type == "docauthor.review-verdict":
            verdicts.append(ev)
    return {
        "event_count_by_type": by_type,
        "total_events": len(events),
        "final_event_payload": (final_event.payload if final_event else None),
        "verdict_count": len(verdicts),
        "refine_iters_observed": max(
            (int((v.payload or {}).get("iter", 0)) for v in verdicts), default=0
        ),
    }


__all__ = [
    "build_team_workers",
    "build_dispatcher",
    "run_job",
    "summarize_events",
]
