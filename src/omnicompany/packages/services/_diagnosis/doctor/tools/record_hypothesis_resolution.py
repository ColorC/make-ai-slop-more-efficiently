# [OMNI] origin=ai-ide domain=services/_diagnosis/doctor/tools ts=2026-05-07T11:05:00Z type=router status=active agent=ai-ide
# [OMNI] summary="RecordHypothesisResolutionTool — 假设证否成立时落 resolution + status='falsified'. 跟 record_hypothesis_challenge 配套, 走 schema §三步骤 4 完整证否分支"
# [OMNI] why="schema §三步骤 4 '证否成立 → status=falsified, 落 resolution'. ChallengeAgent 走完证否流程后调本工具落档"
# [OMNI] tags=tool,doctor,resolution,falsified,V3
# [OMNI] material_id="material:diagnosis.doctor.tools.record_hypothesis_resolution.py"
"""RecordHypothesisResolutionTool · 假设证否落档工具.

跟 record_hypothesis_challenge 配套但语义不同:
- record_hypothesis_challenge: 提质疑, status='active'/'red_green_pass' → 'challenged' (可逆)
- record_hypothesis_resolution: 证否成立, status → 'falsified' (不可逆 — 按 schema §三步骤 4
  '已证否的应当封存')

工作:
- 读目标假设 yaml
- 加 resolution 字段 (含 ts/falsifying_evidence/method/falsifier_id)
- status → 'falsified'
- verification_status → 'falsified'
- 写回 yaml

V0 边界:
- 必先 challenged (verification_status 不能直接从 untested → falsified, 走完整流程)
  按 schema §三步骤 3-4 顺序: 提质疑 → 收集证据 → 证否成立 → falsified
- falsified 跟 real_world_validated 已封存的不允许再 resolution (跟 challenge 同)
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
from omnicompany.packages.services._diagnosis.doctor.builders.hypothesis_resolution_recorder import (
    HypothesisResolutionRecorder,
)


def _project_root() -> Path:
    here = Path(__file__).resolve()
    for p in (here, *here.parents):
        if (p / "src" / "omnicompany").is_dir() and (p / "docs").is_dir():
            return p
    return here.parents[6] if len(here.parents) > 6 else here.parent


_PROJECT_ROOT = _project_root()


class RecordHypothesisResolutionTool(SingleToolRouter):
    """落档假设证否 — 改 yaml status='falsified' + 加 resolution."""

    TOOL_NAME: ClassVar[str] = "record_hypothesis_resolution"
    DESCRIPTION: ClassVar[str] = (
        "Record falsification verdict on a hypothesis (terminal — sets status='falsified' and "
        "verification_status='falsified', appends resolution dict). "
        "Use this only after challenge → evidence collection → falsification stands. "
        "Requires hypothesis to be in 'challenged' state already (call record_hypothesis_challenge first). "
        "Rejects if hypothesis is already falsified or real_world_validated (frozen). "
        "Returns: {falsified, hypothesis_id, status_before, resolution}."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "hypothesis_yaml_path": {
                "type": "string",
                "description": "Path to the hypothesis yaml (relative to project root)",
            },
            "falsifying_evidence": {
                "type": "string",
                "description": "Specific evidence falsifying the hypothesis (1-3 sentences). Cite file:line / standard / fixture.",
            },
            "method": {
                "type": "string",
                "description": "Falsification method: red_green_test / historical_instance / standards_authority / manual",
                "default": "manual",
            },
            "falsifier_id": {
                "type": "string",
                "description": "Who falsified. Default 'ai-ide'.",
                "default": "ai-ide",
            },
        },
        "required": ["hypothesis_yaml_path", "falsifying_evidence"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = False
    IS_READONLY: ClassVar[bool] = False

    CONSUMED_META_IO: ClassVar[tuple[str, ...]] = ()
    PRODUCED_META_IO: ClassVar[tuple[str, ...]] = ()

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        hyp_path = (args.get("hypothesis_yaml_path") or "").strip()
        evidence = (args.get("falsifying_evidence") or "").strip()
        if not hyp_path:
            raise ToolExecutionError("hypothesis_yaml_path 必填")
        if not evidence:
            raise ToolExecutionError("falsifying_evidence 必填 (1-3 句具体证据)")

        full_path = (_PROJECT_ROOT / hyp_path).resolve()
        try:
            full_path.relative_to(_PROJECT_ROOT)
        except ValueError:
            raise ToolExecutionError(f"hypothesis_yaml_path 必须在项目根内: {hyp_path}")
        if not full_path.exists():
            raise ToolExecutionError(f"假设 yaml 不存在: {hyp_path}")

        try:
            with full_path.open(encoding="utf-8") as f:
                hyp_dict = yaml.safe_load(f)
        except Exception as e:
            raise ToolExecutionError(f"读 yaml 失败: {e}")

        if not isinstance(hyp_dict, dict):
            raise ToolExecutionError("yaml 顶层非 dict")

        recorder = HypothesisResolutionRecorder()
        result = recorder.record(
            hyp_dict, evidence,
            method=args.get("method") or "manual",
            falsifier_id=args.get("falsifier_id") or "ai-ide",
        )

        if not result.falsified:
            return f"REJECTED hypothesis_id={result.hypothesis_id} | {result.skip_reason}"

        # 写回 yaml
        try:
            with full_path.open("w", encoding="utf-8") as f:
                yaml.safe_dump(result.upgraded_dict, f, allow_unicode=True, sort_keys=False)
        except Exception as e:
            raise ToolExecutionError(f"写 yaml 失败: {e}")

        res = result.resolution
        return (
            f"FALSIFIED hypothesis_id={result.hypothesis_id} | "
            f"status: '{res['status_before']}' → 'falsified' | "
            f"verification_status: '{res['verification_status_before']}' → 'falsified' | "
            f"method={res['method']} | yaml: {hyp_path}"
        )
