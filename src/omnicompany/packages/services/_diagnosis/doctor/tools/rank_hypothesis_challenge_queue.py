# [OMNI] origin=ai-ide domain=services/_diagnosis/doctor/tools ts=2026-05-07T13:30:00Z type=router status=active agent=ai-ide
# [OMNI] summary="RankHypothesisChallengeQueueTool — 包 HypothesisChallengeQueue 作 agent 可调的 SingleToolRouter. MetaDiagnosticAgent 死局时调它拿排序后假设队列"
# [OMNI] why="V4-2 接通 ChallengeQueue 跟 MetaDiagnosticAgent. schema §三步骤 1-2 排序工具暴露给元诊断 — 元诊断 prompt 已说'死局时优先怀疑 confidence=low+risk=high', 现立工具真支持"
# [OMNI] tags=tool,doctor,challenge-queue,meta-diagnostic,V4
# [OMNI] material_id="material:diagnosis.doctor.tools.rank_hypothesis_challenge_queue.py"
"""RankHypothesisChallengeQueueTool · 假设质疑队列排序工具.

MetaDiagnosticAgent (或其他诊断 agent) 在以下场景调本工具:
- 元诊断走 10 问遇死局 → 调本工具按 a/b/c 优先级排假设, 看应优先怀疑哪几条
- 准备调 ChallengeDiagnosticAgent 前 → 拿 ranked queue 决定喂哪条焦点假设

跟 ChallengeQueue (纯函数) 区别: 本工具加 IO 层 (扫 hypotheses_dir 加载 yaml).
"""
from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import yaml

from omnicompany.packages.services._core.agent.routers.single_tool import (
    SingleToolRouter,
    ToolContext,
    ToolExecutionError,
)
from omnicompany.packages.services._diagnosis.doctor.builders.hypothesis_challenge_queue import (
    HypothesisChallengeQueue,
)


def _project_root() -> Path:
    here = Path(__file__).resolve()
    for p in (here, *here.parents):
        if (p / "src" / "omnicompany").is_dir() and (p / "docs").is_dir():
            return p
    return here.parents[6] if len(here.parents) > 6 else here.parent


_PROJECT_ROOT = _project_root()


def _load_hypotheses_from_dir(hyp_dir: Path) -> list[dict]:
    """扫目录加载所有 .yaml/.yml 的 dict (跳非 dict)."""
    hyps: list[dict] = []
    if not hyp_dir.exists() or not hyp_dir.is_dir():
        return hyps
    for ext in ("*.yaml", "*.yml"):
        for path in sorted(hyp_dir.glob(ext)):
            try:
                with path.open(encoding="utf-8") as f:
                    data = yaml.safe_load(f)
            except Exception:
                continue
            if isinstance(data, dict):
                hyps.append(data)
    return hyps


class RankHypothesisChallengeQueueTool(SingleToolRouter):
    """按 V1 metadata 排假设质疑优先级 (a/b/c) — schema §三步骤 1-2 工具."""

    TOOL_NAME: ClassVar[str] = "rank_hypothesis_challenge_queue"
    DESCRIPTION: ClassVar[str] = (
        "Rank hypotheses by challenge priority following hypothesis_system_schema §三步骤 1-2: "
        "a (+1000) confidence=low + risk=high; "
        "b (+100) untested + applies_to matches problem_context; "
        "c (+10×N) depended_by N hypotheses ≥ threshold (default 3); "
        "falsified +1. "
        "Use this when meta-diagnosis hits an impasse OR before invoking ChallengeDiagnosticAgent. "
        "Returns top-N ranked hypothesis ids with priority_score and reasons (one per line)."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "hypotheses_dir": {
                "type": "string",
                "description": "Path to dir containing hypothesis yaml files (relative to project root, e.g. 'data/services/doctor/hypotheses')",
                "default": "data/services/doctor/hypotheses",
            },
            "applies_to": {
                "type": "string",
                "description": "Problem context applies_to (worker/material/team/agent/hook/tool/plan). Triggers b-class boost when matches hypothesis applies_to. Empty disables b-class.",
                "default": "",
            },
            "focus_count": {
                "type": "integer",
                "description": "Truncate to top N. Default 5, max 30.",
                "default": 5,
                "maximum": 30,
            },
            "depended_by_threshold": {
                "type": "integer",
                "description": "C-class depended_by threshold (default 3 per schema §三步骤 2.c).",
                "default": 3,
                "minimum": 1,
            },
            "include_frozen": {
                "type": "boolean",
                "description": "Include hypotheses with verification_status='falsified' or 'real_world_validated' (default false — these are frozen and should not appear in challenge queue per V7 2026-05-07).",
                "default": False,
            },
        },
        "required": [],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = True
    IS_READONLY: ClassVar[bool] = True

    CONSUMED_META_IO: ClassVar[tuple[str, ...]] = ()
    PRODUCED_META_IO: ClassVar[tuple[str, ...]] = ()

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        hyp_dir_rel = (args.get("hypotheses_dir") or "data/services/doctor/hypotheses").strip()
        applies_to = (args.get("applies_to") or "").strip()
        focus_count = int(args.get("focus_count") or 5)
        if focus_count > 30:
            focus_count = 30
        depended_by_threshold = int(args.get("depended_by_threshold") or 3)

        # 路径必在项目根内
        full_dir = (_PROJECT_ROOT / hyp_dir_rel).resolve()
        try:
            full_dir.relative_to(_PROJECT_ROOT)
        except ValueError:
            raise ToolExecutionError(f"hypotheses_dir 必须在项目根内: {hyp_dir_rel}")

        if not full_dir.exists():
            return f"NO_DIR hypotheses_dir 不存在: {hyp_dir_rel}"

        hyps = _load_hypotheses_from_dir(full_dir)
        if not hyps:
            return f"EMPTY_DIR hypotheses_dir 无 yaml: {hyp_dir_rel}"

        problem_context = {"applies_to": applies_to} if applies_to else None
        # V7: include_frozen 默认 False (skip_frozen=True)
        skip_frozen = not bool(args.get("include_frozen", False))
        queue = HypothesisChallengeQueue()
        result = queue.rank(
            hyps,
            problem_context=problem_context,
            focus_count=focus_count,
            depended_by_threshold=depended_by_threshold,
            skip_frozen=skip_frozen,
        )

        # 写 ctx scratch 让 agent 后续读完整结果 (含 hypothesis_dict)
        scratch = getattr(ctx, "scratch", None)
        if scratch is not None and isinstance(scratch, dict):
            scratch["last_challenge_queue_result"] = [
                {
                    "hypothesis_id": e.hypothesis_id,
                    "priority_score": e.priority_score,
                    "priority_reasons": e.priority_reasons,
                    "hypothesis_dict": e.hypothesis_dict,
                }
                for e in result.ranked
            ]

        # 返简短摘要给 agent
        if not result.ranked:
            return f"RANKED_0 {result.summary}"

        summary_lines = [
            f"RANKED_{len(result.ranked)} loaded={result.total_input} skipped={len(result.skipped)} "
            f"top_score={result.ranked[0].priority_score}"
        ]
        for entry in result.ranked:
            summary_lines.append(
                f"  {entry.hypothesis_id} score={entry.priority_score} reasons={entry.priority_reasons}"
            )
        if focus_count < len(hyps):
            summary_lines.append(
                f"  (showing top {focus_count} of {len(hyps)} hypotheses; full list in ctx.scratch.last_challenge_queue_result)"
            )
        return "\n".join(summary_lines)
