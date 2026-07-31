# [OMNI] origin=claude-code domain=omnicompany/repair ts=2026-04-20T00:00:00Z type=router
# [OMNI] material_id="material:core.repair.repair_loop.orchestrator.py"
"""FormatRepairAgentLoopWorker — Repair Team Worker (Format 修复分组 · #3).

Worker 协议:
  FORMAT_IN  = repair.fmt.request
  FORMAT_OUT = repair.fmt.report

职责: Format 修复 AgentLoop — 诊断 → LLM 规划 → Patch → 重新诊断, 循环至 A 级或达到上限。

注: 名称含 "AgentLoop" 是对当前单体 while 循环形态的叙述, 继承 Worker (等同 Router)。
Phase 1 新 runtime 到位后将按 R-19 重构为 Context Script + LLM + Tool Script 三件套。
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind

from .format_patcher import FormatPatcherWorker
from .repair_planner import RepairPlannerWorker

_DEFAULT_SOURCE_ROOT = Path("e:/WindowsWorkspace/omnicompany/src/omnicompany")


def _run_diagnosis(format_id: str, source_root: str) -> dict:
    """对 format_id 跑完整诊断链, 返回 health_record dict。"""
    from omnicompany.packages.services._diagnosis.doctor.run import _run_hard_diagnosis
    return _run_hard_diagnosis(format_id, source_root)


class FormatRepairAgentLoopWorker(Worker):
    """Format 修复 AgentLoop: 诊断 → LLM 规划 → Patch → 重新诊断, 循环至 A 级或达到上限。

    每轮迭代:
      1. 运行完整诊断（doctor 管线的 HARD 节点, 不含 LLM desc_eval）
      2. 若 grade == 'A', 结束
      3. 提取 Format 源码段, 调用 RepairPlannerWorker (LLM) 生成 delta
      4. 调用 FormatPatcherWorker 将 delta 写入源文件
      5. 重复

    输出: repair.fmt.report
    """

    DESCRIPTION = "Format 修复 AgentLoop：诊断 → LLM 规划 → Patch，循环至 A 级"
    FORMAT_IN = "repair.fmt.request"
    FORMAT_OUT = "repair.fmt.report"

    def __init__(self, model: str | None = None):
        self._planner = RepairPlannerWorker(model=model)
        self._patcher = FormatPatcherWorker()

    def run(self, input_data: Any) -> Verdict:
        format_id: str = input_data["format_id"]
        source_root: str = input_data.get("source_root", str(_DEFAULT_SOURCE_ROOT))
        max_iter: int = int(input_data.get("max_iterations", 3))

        # 契约变更 #02 (2026-04-25): 改用 v2 字段 verdict + passed · counts, 不用 grade/score
        iterations: list[dict] = []
        initial_verdict: str | None = None
        current_verdict: str | None = None
        current_passed: bool = False

        for i in range(1, max_iter + 1):
            # ── 1. 诊断 ──
            health_record = _run_diagnosis(format_id, source_root)
            verdict = health_record.get("verdict", "uncertain")
            counts = health_record.get("counts", {})
            passed = bool(health_record.get("passed", False))

            if initial_verdict is None:
                initial_verdict = verdict
            current_verdict = verdict
            current_passed = passed

            iter_entry: dict = {
                "iter": i,
                "verdict_before": verdict,
                "counts_before": counts,
                "passed_before": passed,
            }

            if passed and verdict == "healthy":
                iter_entry["note"] = "诊断通过, 无需修复"
                iterations.append(iter_entry)
                break

            # ── 2. 提取 Format 源码段 ──
            source_excerpt = self._extract_source_excerpt(format_id, source_root, health_record)

            # ── 3. LLM 规划 delta ──
            attempt = {
                "format_id": format_id,
                "source_root": source_root,
                "health_record": health_record,
                "source_excerpt": source_excerpt,
                "iter": i,
            }
            plan_result = self._planner.run(attempt)
            plan_out = plan_result.output if hasattr(plan_result, "output") else plan_result
            delta: dict = plan_out.get("delta", {})
            iter_entry["delta"] = delta

            if not delta:
                iter_entry["note"] = "LLM 未给出修复建议，停止循环"
                iterations.append(iter_entry)
                break

            # ── 4. Patch 源文件 ──
            patch_result = self._patcher.run({**plan_out})
            patch_out = patch_result.output if hasattr(patch_result, "output") else patch_result
            patch_ok: bool = patch_out.get("patch_ok", False)
            iter_entry["patch_ok"] = patch_ok
            iter_entry["patch_applied_fields"] = patch_out.get("patch_applied_fields", [])
            if not patch_ok:
                iter_entry["patch_error"] = patch_out.get("patch_error", "unknown")

            # ── 5. 重新诊断, 记录本轮 verdict_after ──
            if patch_ok:
                health_after = _run_diagnosis(format_id, source_root)
                verdict_after = health_after.get("verdict", "uncertain")
                passed_after = bool(health_after.get("passed", False))
                counts_after = health_after.get("counts", {})
                current_verdict = verdict_after
                current_passed = passed_after
            else:
                verdict_after = verdict
                passed_after = passed
                counts_after = counts
            iter_entry["verdict_after"] = verdict_after
            iter_entry["counts_after"] = counts_after
            iter_entry["passed_after"] = passed_after

            iterations.append(iter_entry)

            if passed_after and verdict_after == "healthy":
                break
            if not patch_ok:
                break

        success = current_passed and current_verdict == "healthy"
        report = {
            "format_id": format_id,
            "source_root": source_root,
            "initial_verdict": initial_verdict or "uncertain",
            "final_verdict": current_verdict or "uncertain",
            "final_passed": current_passed,
            "success": success,
            "iterations": iterations,
        }

        return Verdict(
            kind=VerdictKind.PASS if success else VerdictKind.FAIL,
            confidence=1.0,
            output=report,
            diagnosis=(
                f"RepairLoop: {format_id} {initial_verdict}→{current_verdict} "
                f"({'OK' if success else 'FAIL'}) in {len(iterations)} iter(s)"
            ),
        )

    def _extract_source_excerpt(self, format_id: str, source_root: str, health_record: dict) -> str:
        """提取 Format() 定义块的源码文本 (最多 80 行)。"""
        extracted = health_record.get("extracted", {})
        defined_in: str = extracted.get("defined_in", "")
        if not defined_in:
            return ""

        source_root_path = Path(source_root) if source_root else _DEFAULT_SOURCE_ROOT
        target_path = source_root_path.parent / defined_in
        if not target_path.exists():
            target_path = source_root_path / defined_in
        if not target_path.exists():
            target_path = Path(defined_in)
        if not target_path.exists():
            return ""

        try:
            src = target_path.read_text(encoding="utf-8")
            tree = ast.parse(src)
            lines = src.splitlines(keepends=True)

            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                is_format = (isinstance(func, ast.Name) and func.id == "Format") or (
                    isinstance(func, ast.Attribute) and func.attr == "Format"
                )
                if not is_format:
                    continue
                for kw in node.keywords:
                    if kw.arg == "id":
                        try:
                            if ast.literal_eval(kw.value) == format_id:
                                start = node.lineno - 1
                                end = min(node.end_lineno, start + 80)
                                return "".join(lines[start:end])
                        except Exception:
                            pass
        except Exception:
            pass
        return ""
