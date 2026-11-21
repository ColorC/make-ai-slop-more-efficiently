# [OMNI] origin=claude-code domain=omnicompany/repair ts=2026-04-25T00:00:00Z type=router
# [OMNI] material_id="material:core.repair.rediagnose.failure_set_comparator.py"
"""RediagnoseWorker — Repair Team Worker (Router 修复分组 · #9).

**契约变更 #02 (2026-04-25)**: 去 health_grade 比较 · 改 failure sets 对称差.

核心改动:
- 旧: 比较前后 grade letter (A/B/C/D/F) · 二元 improved=bool
- 新: 比较前后 failures_by_severity (critical/major/minor) 集合的对称差 ·
      报告"resolved (前有现无)" + "new (前无现有)" + regressed/improved 逻辑

Worker 协议:
  FORMAT_IN  = diag.repair.pending
  FORMAT_OUT = diag.repair.result
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind

from ._shared import _DEFAULT_SOURCE_ROOT


def _diff_failure_sets(before: dict, after: dict) -> dict:
    """前后 failures_by_severity 对称差 · 返回每档 resolved/new 列表 + 汇总."""
    result = {}
    for sev in ("critical", "major", "minor"):
        before_set = set(before.get(sev, []))
        after_set = set(after.get(sev, []))
        resolved = sorted(before_set - after_set)
        newly = sorted(after_set - before_set)
        result[sev] = {
            "before_count": len(before_set),
            "after_count": len(after_set),
            "resolved": resolved,
            "new": newly,
        }
    return result


def _judge_improvement(diff: dict) -> dict:
    """根据 diff 综合判: improved / regressed / neutral."""
    new_critical = len(diff["critical"]["new"])
    new_major = len(diff["major"]["new"])
    resolved_critical = len(diff["critical"]["resolved"])
    resolved_major = len(diff["major"]["resolved"])
    resolved_minor = len(diff["minor"]["resolved"])

    regressed = new_critical > 0 or new_major > 0
    improved = (resolved_critical > 0 or resolved_major > 0 or resolved_minor > 0) and not regressed
    neutral = not improved and not regressed

    return {
        "improved": improved,
        "regressed": regressed,
        "neutral": neutral,
        "summary_numbers": {
            "critical_resolved": resolved_critical,
            "critical_new": new_critical,
            "major_resolved": resolved_major,
            "major_new": new_major,
            "minor_resolved": resolved_minor,
            "minor_new": len(diff["minor"]["new"]),
        },
    }


class RediagnoseWorker(Worker):
    """重跑 Doctor 确定性诊断链, 对比前后 failure sets (v2 · 无 grade)."""

    DESCRIPTION = (
        "重跑确定性诊断, 对比修复前后 failures_by_severity 集合的对称差 "
        "(resolved/new 各 severity 统计); 未应用 patch 时报告 pending 状态. "
        "v2 · 不用 health_grade 比较."
    )
    FORMAT_IN = "diag.repair.pending"
    FORMAT_OUT = "diag.repair.result"

    def run(self, input_data: Any) -> Verdict:
        router_class: str = input_data.get("router_class", "")
        source_file: str = input_data.get("source_file", "")
        source_root: str = input_data.get("source_root", str(_DEFAULT_SOURCE_ROOT))
        pending_path: str | None = input_data.get("pending_path")

        if pending_path and Path(pending_path).exists():
            return Verdict(
                kind=VerdictKind.PASS, confidence=1.0,
                output={**input_data,
                        "rediagnose_status": "pending",
                        "rediagnose_note": f"修复提案待审批: {pending_path}"},
                diagnosis=f"Rediagnose: {router_class} 等待人类审批",
            )

        from omnicompany.packages.services._diagnosis.doctor.routers import (
            RouterExtractorRouter, RouterSignatureRouter,
            RouterContextCollectorRouter, RouterDeterministicCheckRouter,
            RouterHealthWriterRouter,
        )

        def unpack(v):
            return v.output if hasattr(v, "output") else v

        try:
            r = unpack(RouterExtractorRouter().run({
                "router_class": router_class,
                "source_file": source_file,
                "source_root": source_root,
            }))
            r = unpack(RouterSignatureRouter().run(r))
            # 若上游尚无 v2 record, 跑完整检查链
            if not (isinstance(r, dict) and r.get("schema_version") == 2):
                r = unpack(RouterContextCollectorRouter().run(r))
                r = unpack(RouterDeterministicCheckRouter().run(r))
            health = unpack(RouterHealthWriterRouter().run(r))
        except Exception as e:
            return Verdict(
                kind=VerdictKind.FAIL, confidence=1.0,
                output={**input_data, "error": str(e)},
                diagnosis=f"Rediagnose: 重诊断失败 {e}",
            )

        # 新: 前后 failure sets 对称差 (input_data 里由上游 IssueLoader 传入 before_failures)
        before_failures = input_data.get("before_failures_by_severity") or {
            "critical": [], "major": [], "minor": [],
        }
        after_failures = health.get("failures_by_severity") or {
            "critical": [], "major": [], "minor": [],
        }
        diff = _diff_failure_sets(before_failures, after_failures)
        judgment = _judge_improvement(diff)

        before_passed = bool(input_data.get("before_passed", False))
        after_passed = bool(health.get("passed", False))

        return Verdict(
            kind=VerdictKind.PASS, confidence=1.0,
            output={
                **input_data,
                "rediagnose_status": "applied",
                "before_passed": before_passed,
                "after_passed": after_passed,
                "before_failures_by_severity": before_failures,
                "after_failures_by_severity": after_failures,
                "diff_by_severity": diff,
                "improved": judgment["improved"],
                "regressed": judgment["regressed"],
                "neutral": judgment["neutral"],
                "summary_numbers": judgment["summary_numbers"],
                "health_record": health,
            },
            diagnosis=(
                f"Rediagnose: {router_class} "
                f"passed {before_passed}→{after_passed} · "
                f"critical: -{judgment['summary_numbers']['critical_resolved']} "
                f"+{judgment['summary_numbers']['critical_new']} · "
                f"improved={judgment['improved']}"
            ),
        )
