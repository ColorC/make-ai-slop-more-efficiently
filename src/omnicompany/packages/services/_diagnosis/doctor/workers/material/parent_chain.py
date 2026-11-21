# [OMNI] origin=claude-code domain=omnicompany/doctor ts=2026-04-22T00:00:00Z type=worker
# [OMNI] material_id="material:diagnosis.doctor.worker.material.parent_connectivity_checker.py"
"""MaterialParentChainWorker — parent 合法性 + 连通性注记 (HARD, Stage 3 Clean Migration 2026-04-22).

Worker 协议:
  FORMAT_IN  = doctor.material.extracted
  FORMAT_OUT = doctor.material.check.parent-chain

诊断目标: 检查 Format 的 parent 字段合法性, 并记录管线连通性 (仅注记, 不评分).

设计原则:
  - 连通性 (是否有 FORMAT_IN/OUT 引用) 反映管线实现状态, 不是 Format 定义质量
    → 连通性仅在 detail 中注记, 不作为 pass/fail 子项
  - parent 字段是 Format 定义必要元素: 缺失或值不合法才降分

parent 合法值: 'requirement' 或形如 'domain.something' 的合法 Format ID
"""
from __future__ import annotations

from typing import Any

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind

from ._shared import DOMAIN_PATTERN


class MaterialParentChainWorker(Worker):
    """检查 parent 字段合法性; 连通性 (INPUT/OUTPUT 引用数) 仅注记不评分."""

    DESCRIPTION = "检查 parent 字段合法性; 连通性 (INPUT/OUTPUT 引用数) 仅注记不评分"
    FORMAT_IN = "doctor.material.extracted"
    FORMAT_OUT = "doctor.material.check.parent-chain"

    def run(self, input_data: Any) -> Verdict:
        material_id: str = input_data["material_id"]
        extracted = input_data.get("extracted", {})
        format_obj: dict = extracted.get("format_obj", {})
        usages: list = extracted.get("usages", [])
        parent: str = format_obj.get("parent") or ""

        input_usages = [u for u in usages if "INPUT" in u.get("role", "")]
        output_usages = [u for u in usages if "OUTPUT" in u.get("role", "")]

        sub_checks = []

        # ── parent 字段合法性 (唯一评分项) ──
        if parent:
            parent_ok = parent == "requirement" or (
                "." in parent and bool(DOMAIN_PATTERN.match(parent))
            )
            sub_checks.append(("parent 格式合法", parent_ok,
                               f"parent='{parent}'" if parent_ok
                               else f"parent='{parent}' 不是 'requirement' 或合法 Format ID"))
        else:
            sub_checks.append(("parent 字段存在", False,
                               "Material (Format 对象)缺少 parent 字段 (应为 'requirement' 或父 Format ID)"))

        passed_count = sum(1 for _, ok, _ in sub_checks if ok)
        all_pass = passed_count == len(sub_checks)

        # 连通性注记 (不参与评分, 写入 detail)
        conn_note = f"INPUT {len(input_usages)} 处, OUTPUT {len(output_usages)} 处"
        if len(input_usages) == 0 and len(output_usages) == 0:
            conn_note += " [孤立, 尚无实现节点]"

        check_result = {
            "check": "parent_chain",
            "passed": all_pass,
            "severity": "HIGH" if not all_pass else "INFO",
            "detail": f"{conn_note}, parent={parent!r}",
            "sub_checks": [
                {"name": n, "passed": ok, "detail": d}
                for n, ok, d in sub_checks
            ],
        }

        return Verdict(
            kind=VerdictKind.PASS,
            confidence=1.0,
            output={
                "material_id": material_id,
                "source_root": input_data.get("source_root", ""),
                "sig_diff_ok": input_data.get("sig_diff_ok", True),
                "extracted": input_data.get("extracted", {}),
                "check_parent_chain": check_result,
            },
            diagnosis=f"ParentChain: INPUT={len(input_usages)} OUTPUT={len(output_usages)} parent={parent!r}",
        )
