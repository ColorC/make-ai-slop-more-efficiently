# [OMNI] origin=ai-ide domain=services/_diagnosis/doctor/tools ts=2026-05-06T00:30:00Z type=router status=skeleton agent=ai-ide-current
# [OMNI] summary="SubmitDerivationReportRouter — HypothesisDeriverAgent 出口检查工具. 调它通过 schema 校验 = 合法结束. 跟 submit_verdict 同思路"
# [OMNI] why="阶段 2 后续 3: 派生 agent 也应有出口检查工具. 拒数字打分铁律仍适用. 校验派生过程产物完整 (hypothesis_ids + 来源 + 派生 narrative)"
# [OMNI] tags=tool,doctor,submit-derivation,exit-gate,skeleton
# [OMNI] material_id="material:diagnosis.doctor.tools.submit_derivation_report.skeleton.py"
"""SubmitDerivationReportRouter · HypothesisDeriverAgent 出口检查 (V0 骨架).

设计思路 (跟 submit_verdict 同):
  调本工具校验通过 = 合法结束 loop. 校验失败 = 必须修后重提.
  本工具不落盘, 只校验 + 写 ctx.scratch.

校验内容 (V0):
  - source_paths (一组路径, 派生时考虑了哪些源)
  - derived_hypothesis_ids (一组 id, 派生了哪些假设)
  - narrative (派生的整体说明: 用户铁律 - 拒打分要来龙去脉)
  - 任何 severity / confidence 字段拒绝
"""
from __future__ import annotations

import logging
from typing import ClassVar

from omnicompany.packages.services._core.agent.routers.single_tool import (
    SingleToolRouter,
    ToolContext,
    ToolExecutionError,
)

logger = logging.getLogger(__name__)


_BANNED_SCORING_FIELDS = (
    "severity", "score", "level", "tier", "confidence", "rating", "grade",
)


class SubmitDerivationReportRouter(SingleToolRouter):
    """HypothesisDeriverAgent 出口检查工具."""

    TOOL_NAME: ClassVar[str] = "submit_derivation_report"
    DESCRIPTION: ClassVar[str] = (
        "Submit your hypothesis derivation report. THIS IS THE EXIT — call this tool with passing schema "
        "before calling finish. The tool verifies your derivation summary is complete and lawful.\n"
        "Required: source_paths (list of source paths consulted, e.g., ['docs/standards/concepts/worker.md']), "
        "derived_hypothesis_ids (list of hypothesis ids written via write_hypothesis), "
        "narrative (overall commentary: 来龙去脉 - 派生时考虑了什么 / 派生策略 / 哪些是 hard rule 候选 / "
        "哪些是软语义 / 跟现有假设库的关系).\n"
        "禁止 (rejection cases): severity / score / level / tier / confidence / rating / grade fields ANYWHERE — "
        "用户铁律: 拒打分拥评论, 拒数字要来龙去脉. Use commentary natural-language sentences instead.\n"
        "If schema check fails, the tool returns the failure reason; you MUST revise and re-submit."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "source_paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Paths of sources you consulted to derive hypotheses (specs, plans, code paths)",
            },
            "derived_hypothesis_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Hypothesis ids you wrote via write_hypothesis. Empty list ALLOWED only when sources yielded zero novel hypotheses (rare)",
            },
            "narrative": {
                "type": "string",
                "description": (
                    "Your overall natural-language commentary on this derivation pass: "
                    "来龙去脉 — 派生时考虑了什么 / 派生策略 (broad vs narrow / 跨 entity_kind vs 单一) / "
                    "哪些假设是 hard rule 候选可转给 guardian / 哪些是软语义留给 doctor / "
                    "跟现有假设库的关系 (是否有重叠 / 升级旧假设 / 新方向). 一段, ≥30 字"
                ),
            },
        },
        "required": ["source_paths", "derived_hypothesis_ids", "narrative"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = True
    IS_READONLY: ClassVar[bool] = True

    CONSUMED_META_IO: ClassVar[tuple[str, ...]] = ()
    PRODUCED_META_IO: ClassVar[tuple[str, ...]] = ()

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        narrative = (args.get("narrative") or "").strip()
        if len(narrative) < 30:
            raise ToolExecutionError(
                "narrative too short (< 30 chars). Write a full natural-language paragraph "
                "explaining your derivation strategy, what you considered, hard-rule vs soft-semantic split, "
                "and relation to existing hypothesis library."
            )

        # 拒打分字段
        banned_present = [k for k in _BANNED_SCORING_FIELDS if k in args]
        if banned_present:
            raise ToolExecutionError(
                f"derivation report contains banned scoring/numeric fields: {banned_present}. "
                f"用户铁律: 拒打分拥评论, 拒数字要来龙去脉. "
                f"Encode same information as natural-language narrative. Re-submit without {banned_present}."
            )

        source_paths = args.get("source_paths") or []
        if not isinstance(source_paths, list):
            raise ToolExecutionError("source_paths must be a list")
        if not source_paths:
            raise ToolExecutionError(
                "source_paths empty — you must consult at least one source (spec/plan/code) to derive hypotheses. "
                "If genuinely none, you should not be calling submit_derivation_report"
            )

        derived = args.get("derived_hypothesis_ids") or []
        if not isinstance(derived, list):
            raise ToolExecutionError("derived_hypothesis_ids must be a list (empty list allowed but rare)")

        # 写入 ctx.scratch
        scratch = getattr(ctx, "scratch", None)
        if scratch is not None and isinstance(scratch, dict):
            scratch["submitted_derivation"] = dict(args)

        logger.info(
            "[submit_derivation_report] OK sources=%d derived=%d narrative_len=%d",
            len(source_paths), len(derived), len(narrative),
        )

        return (
            f"Derivation report accepted. {len(derived)} hypothesis(es) derived from "
            f"{len(source_paths)} source(s), narrative {len(narrative)} chars. "
            f"You may now call finish to end the derivation loop."
        )
