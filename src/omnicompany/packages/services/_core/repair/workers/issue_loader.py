# [OMNI] origin=claude-code domain=omnicompany/repair ts=2026-04-20T00:00:00Z type=router
# [OMNI] material_id="material:core.repair.issue_loader.diagnostic.py"
"""IssueLoaderWorker — Repair Team Worker (Router 修复分组 · #1).

Worker 协议:
  FORMAT_IN  = diag.repair.request
  FORMAT_OUT = diag.repair.issue-list

职责: 重跑 Doctor 确定性诊断链, 提取当前 Router 的 B 类问题清单。
"""
from __future__ import annotations

from typing import Any

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind

from ._shared import _DEFAULT_SOURCE_ROOT


class IssueLoaderWorker(Worker):
    """重跑 Doctor 确定性诊断链, 提取当前 Router 的 B 类问题清单。

    B 类 = 可 LLM 辅助补全+修复、需人类审批的问题:
      - R-01: DESCRIPTION 太短（< 50 字）
      - R-05: FAIL 路径缺失
      - R-07-signal: granted_tags 未授予

    A 类（FORMAT_IN 列表）和 C 类（async run）不纳入本管线。
    """

    DESCRIPTION = "重跑 Doctor 确定性诊断，提取 B 类问题（DESCRIPTION 短/FAIL 缺失/tags 缺失），排除 A/C 类"
    FORMAT_IN = "diag.repair.request"
    FORMAT_OUT = "diag.repair.issue-list"

    _ACTIONABLE_CHECKS = {"R-01", "R-05", "R-07", "R-07-signal"}

    def run(self, input_data: Any) -> Verdict:
        router_class: str = input_data["router_class"]
        source_file: str = input_data["source_file"]
        source_root: str = input_data.get("source_root", str(_DEFAULT_SOURCE_ROOT))

        # 私有类（下划线前缀）跳过：通常是脚本辅助类，不是管线 Router
        if router_class.startswith("_"):
            return Verdict(
                kind=VerdictKind.FAIL, confidence=1.0,
                output={**input_data, "skip_reason": f"私有类（{router_class}），不纳入自动修复"},
                diagnosis=f"IssueLoader: {router_class} 是私有类，跳过",
            )

        from omnicompany.packages.services._diagnosis.doctor.routers import (
            RouterExtractorRouter,
            RouterSignatureRouter,
            RouterContextCollectorRouter,
            RouterDeterministicCheckRouter,
        )

        def unpack(v):
            return v.output if hasattr(v, "output") else v

        req = {"router_class": router_class, "source_file": source_file, "source_root": source_root}

        try:
            r = unpack(RouterExtractorRouter().run(req))
            r = unpack(RouterSignatureRouter().run(r))
            # 契约变更 #02 (2026-04-25): 改用 v2 字段判早退 · sig_diff_ok=False 或 passed=False 已走完整记录
            if (r.get("schema_version") == 2 and not r.get("sig_ok", True)):
                return Verdict(
                    kind=VerdictKind.FAIL, confidence=1.0,
                    output={**input_data, "skip_reason": "签名缺失（C 类），不纳入自动修复"},
                    diagnosis=f"IssueLoader: {router_class} 签名缺失，跳过",
                )
            r = unpack(RouterContextCollectorRouter().run(r))
            r = unpack(RouterDeterministicCheckRouter().run(r))
        except Exception as e:
            return Verdict(
                kind=VerdictKind.FAIL, confidence=1.0,
                output={**input_data, "error": str(e)},
                diagnosis=f"IssueLoader: 诊断链执行异常 {e}",
            )

        checks: list[dict] = r.get("checks", [])
        b_class_issues: list[dict] = []
        for chk in checks:
            check_id = chk.get("check", "")
            if check_id not in self._ACTIONABLE_CHECKS:
                continue
            if chk.get("passed") is False:
                b_class_issues.append({
                    "check_id": check_id,
                    "severity": chk.get("severity"),
                    "observation": chk.get("observation", ""),
                    "detail": chk.get("detail"),
                })

        if not b_class_issues:
            return Verdict(
                kind=VerdictKind.PASS, confidence=1.0,
                output={**input_data, "b_class_issues": [], "extracted": r.get("extracted", {}),
                        "context": r.get("context", {}), "skip_reason": "无 B 类问题，无需修复"},
                diagnosis=f"IssueLoader: {router_class} 无 B 类问题",
            )

        return Verdict(
            kind=VerdictKind.PASS, confidence=1.0,
            output={**input_data, "b_class_issues": b_class_issues,
                    "extracted": r.get("extracted", {}), "context": r.get("context", {})},
            diagnosis=f"IssueLoader: {router_class} {len(b_class_issues)} 项 B 类问题",
        )
