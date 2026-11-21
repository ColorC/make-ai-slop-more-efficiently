# [OMNI] origin=ai-ide domain=services/_diagnosis/doctor/tools ts=2026-05-05T22:00:00Z type=router status=skeleton agent=ai-ide-current
# [OMNI] summary="SubmitVerdictRouter — doctor 诊断 agent 的出口检查工具. 调它通过 schema 校验 = 合法结束. 堵不如疏: 检查内容完整合法, 怎么写随意"
# [OMNI] why="step 7 dogfood 暴露 LLM 直接 JSON 嵌入 verdict.text 不调业务工具. 用户指示: 给格式跟进度检查工具, 未调用通过不能退出, 不要逆反本能"
# [OMNI] tags=tool,doctor,submit-verdict,exit-gate,skeleton
# [OMNI] material_id="material:diagnosis.doctor.tools.submit_verdict.skeleton.py"
"""SubmitVerdictRouter · doctor 诊断 agent 出口检查 (V0 骨架)

设计原则 (用户 2026-05-05 指示):
  堵不如疏 — 不靠 prompt 强迫 LLM 调 write_finding, 而是给一个出口检查工具.
  调它且校验通过 = 合法结束 loop. 不调或校验失败 = 必须重写直到通过.
  内容怎么写随意 (LLM 可以预先调 write_finding 也可以一次性所有 finding 都 inline 进 args.findings).
  本工具只负责检查内容完整合法.

调用即终止 loop:
  AgentNodeLoop 默认看 finish 工具结束. 我们让 submit_verdict 同时承担"提交 + 终止" 双角色:
  agent 调 submit_verdict 校验通过 → ToolContext.submitted_verdict 写入 verdict 数据 →
  AgentNodeLoop loop 终止 (我们不改 loop, 而是让 LLM 调完 submit_verdict 后调 finish).
  V0 实现: schema 校验 + 写 ctx.submitted_verdict, 终止靠 prompt 引导 LLM 调 finish.
  V1 待: SpecDiagnosticAgent.build_extract_result hook 检查 ctx.submitted_verdict, 没有报错强制 loop 继续.

校验内容 (V0):
  - target_entity_path / target_entity_kind / applicable_standards 元信息齐
  - findings 是 list, 每条满足 doctor.health_finding 必填字段 (entity_id / entity_kind / finding_kind / evidence / commentary / concern)
  - narrative (LLM 整体评论) 必填且非空
  - 任何 severity 字段 (critical/major/minor 等数字打分) 出现 → 报错指引: 改成 commentary + concern

## 待做 (V0 → V1)

[ ] **loop 终止守卫**: 当前 LLM 不调 submit_verdict 也能 finish 退出. 应让 SpecDiagnosticAgent override extract_result, 没 ctx.submitted_verdict 时拒绝终止
[ ] **跟 write_finding 协同**: 当前 submit_verdict 接受 inline findings, 也允许 LLM 预先 write_finding 然后 submit_verdict 只传 finding_id 列表. V1 加 mode 区分
[ ] **进度检查**: 拒绝 findings=[] (没诊断说明 agent 没干活, 强制重新检查)
[ ] **bus 事件**: submit_verdict 校验通过后 publish doctor.spec_diagnosis.verdict event 到 SQLiteBus, 让下游 registry HealthArchive 接
[ ] **测试**: 红绿样本 (verdict 字段全 / 缺 commentary / 含 severity 数字) 测校验判别力
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


# 显式禁用的"打分" 字段名 — LLM 习惯写但用户铁律拒绝
_BANNED_SCORING_FIELDS = (
    "severity", "score", "level", "tier", "confidence", "rating", "grade",
)


class SubmitVerdictRouter(SingleToolRouter):
    """doctor 诊断 agent 的出口检查工具. 调它通过校验才算合法结束."""

    TOOL_NAME: ClassVar[str] = "submit_verdict"
    DESCRIPTION: ClassVar[str] = (
        "Submit your diagnosis verdict. THIS IS THE EXIT — you must call this tool with passing schema "
        "before calling finish. The tool verifies content is complete and lawful. How you arrived at the "
        "content (inline JSON or via prior write_finding calls) is up to you. "
        "通用工具 — 4 类诊断方法 (spec/hypothesis/exemplar/plan) 都用本工具.\n"
        "Required: target_entity_path / target_entity_kind / consulted_references / findings (list) / narrative (overall commentary).\n"
        "Each finding must have: entity_id, entity_kind, finding_kind, evidence, commentary (评论), concern (来龙去脉).\n"
        "禁止 (rejection cases): severity / score / level / tier / confidence / rating / grade fields ANYWHERE in your verdict — "
        "用户铁律: 拒打分拥评论, 拒数字要来龙去脉. Use commentary + concern natural-language sentences instead.\n"
        "If schema check fails, the tool returns the failure reason; you MUST revise and re-submit. "
        "Do not call finish until submit_verdict returns success."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "target_entity_path": {
                "type": "string",
                "description": "The diagnosed entity path (echoed from the request)",
            },
            "target_entity_kind": {
                "type": "string",
                "description": "The diagnosed entity kind (echoed from the request)",
            },
            "consulted_references": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "References consulted during this diagnosis. "
                    "spec 型: docs/standards/ 文档 path:section. "
                    "hypothesis 型: hypothesis yaml id 或 path. "
                    "exemplar 型: 标杆 path. "
                    "plan 型: docs/plans/<plan>/plan.md 节. "
                    "通用: 列你实际看的引用源."
                ),
            },
            "findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "entity_id": {"type": "string"},
                        "entity_kind": {"type": "string"},
                        "finding_kind": {"type": "string"},
                        "evidence": {"type": "string"},
                        "commentary": {"type": "string"},
                        "concern": {"type": "string"},
                        "applied_standards": {"type": "array", "items": {"type": "string"}},
                        "applied_hypotheses": {"type": "array", "items": {"type": "string"}},
                        "applied_exemplars": {"type": "array", "items": {"type": "string"}},
                        "uncertainty_note": {
                            "type": "string",
                            "description": (
                                "Optional 自然语言. 显式表达对本 finding 的不确定性 (例如 '此条规范在 worker.md §X 表述模糊, 当前判定基于 LLM 对模糊描述的解读, 待规范升级复审'). "
                                "用户铁律: LLM 不得强行二元判断, 信息不足时应说'我不确定' + 为什么不确定. 不是 severity 打分, 是来龙去脉. "
                                "跟 evidence/commentary/concern 共存, 不替代 — 三字段写'判定是什么', uncertainty_note 写'判定有多少把握 + 不把握的来源'"
                            ),
                        },
                        "finding_id": {
                            "type": "string",
                            "description": "Optional. If you previously called write_finding for this finding, pass the returned id here",
                        },
                    },
                    "required": ["entity_id", "entity_kind", "finding_kind", "evidence", "commentary", "concern"],
                },
                "description": "List of findings (each is a doctor.health_finding instance). Empty list ALLOWED only when you've fully verified there are zero findings worth recording (rare)",
            },
            "narrative": {
                "type": "string",
                "description": "Your overall natural-language commentary on this entity. One paragraph. References regulations + code locations. Explains the big picture, not enumerated bullet points",
            },
        },
        "required": [
            "target_entity_path", "target_entity_kind",
            "consulted_references", "findings", "narrative",
        ],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = True
    IS_READONLY: ClassVar[bool] = True   # 不写盘 (落盘归 finalize 步骤), 仅校验 + 写 ctx

    CONSUMED_META_IO: ClassVar[tuple[str, ...]] = ()
    PRODUCED_META_IO: ClassVar[tuple[str, ...]] = ()

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        # ── 1. 元信息检查 ──
        narrative = (args.get("narrative") or "").strip()
        if len(narrative) < 30:
            raise ToolExecutionError(
                "narrative too short (< 30 chars). Write a full natural-language paragraph "
                "explaining the big picture: what stood out, why, what regulations/locations support it. "
                "Do not just list bullet points."
            )

        # ── 2. 打分字段拒绝 (堵不如疏: 给清晰指引, 不让重试越权) ──
        self._reject_scoring_fields(args, "verdict")

        findings = args.get("findings") or []
        if not isinstance(findings, list):
            raise ToolExecutionError("findings must be a list, even if empty")

        for i, f in enumerate(findings):
            if not isinstance(f, dict):
                raise ToolExecutionError(f"findings[{i}] must be a dict, got {type(f).__name__}")
            self._reject_scoring_fields(f, f"findings[{i}]")

            for required_field in ("entity_id", "entity_kind", "finding_kind", "evidence", "commentary", "concern"):
                if not (f.get(required_field) or "").strip():
                    raise ToolExecutionError(
                        f"findings[{i}] missing required field {required_field!r}. "
                        f"Each finding needs entity_id / entity_kind / finding_kind / evidence (具体引代码位置) / "
                        f"commentary (评论) / concern (来龙去脉). 写完整中文句子, 不堆代号."
                    )

            # 字段长度温和门 (防 LLM 写 'OK' / 'good' 这种空话)
            for natural_field, min_len in (("evidence", 20), ("commentary", 30), ("concern", 30)):
                val = (f.get(natural_field) or "").strip()
                if len(val) < min_len:
                    raise ToolExecutionError(
                        f"findings[{i}].{natural_field} too short (< {min_len} chars). "
                        f"This is a natural-language commentary slot. Write a full sentence with specific evidence and reasoning."
                    )

        # ── 3. 写入 ctx 让 agent 后处理可读 (取代落盘, V0 阶段) ──
        ctx_dict = dict(args)
        ctx_dict["_finding_count"] = len(findings)
        # SingleToolRouter 的 ctx 是 dataclass-ish, 不能 setattr 任意, 用 ctx.scratch dict 兜
        scratch = getattr(ctx, "scratch", None)
        if scratch is not None and isinstance(scratch, dict):
            scratch["submitted_verdict"] = ctx_dict

        logger.info(
            "[submit_verdict] OK target=%s entity_kind=%s findings=%d narrative_len=%d",
            args["target_entity_path"], args["target_entity_kind"],
            len(findings), len(narrative),
        )

        return (
            f"Verdict accepted. {len(findings)} finding(s) recorded, "
            f"narrative {len(narrative)} chars. "
            f"You may now call finish to end the diagnosis loop."
        )

    @classmethod
    def _reject_scoring_fields(cls, obj: dict, location: str) -> None:
        """检查 obj 里有没用户铁律禁的打分字段, 有则报错."""
        banned_present = [k for k in _BANNED_SCORING_FIELDS if k in obj]
        if banned_present:
            raise ToolExecutionError(
                f"{location} contains banned scoring/numeric fields: {banned_present}. "
                f"用户铁律 (2026-05-05): 拒打分拥评论, 拒数字要来龙去脉. "
                f"Remove these fields entirely. Encode the same information as natural language sentences "
                f"in commentary (评论, 一两段) + concern (来龙去脉, 为什么这是个问题, 不修会怎样, 修起来代价). "
                f"Re-submit without {banned_present}."
            )
