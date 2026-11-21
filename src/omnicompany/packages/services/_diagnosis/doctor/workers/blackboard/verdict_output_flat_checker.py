# [OMNI] origin=claude-code domain=omnicompany/doctor ts=2026-04-20T00:00:00Z type=worker
# [OMNI] material_id="material:diagnosis.doctor.worker.blackboard.verdict_output_flatness_checker.py"
"""VerdictOutputFlatCheckerWorker — 黑板诊断子域 Worker #3.

Worker 协议:
  FORMAT_IN  = doctor.blackboard.audit_request
  FORMAT_OUT = doctor.blackboard.output_flat_report

职责: 扫 Worker.run 源码 · 检测嵌套 output={"<material_id>": {...}} 反模式 (R-23).

实现: 粗扫文本匹配. 对每个 Worker.run 源码 · 找 `output={"<id>":` 形式:
- 若 "<id>" 恰好是 Worker 自己的 FORMAT_OUT (自嵌套) → 高疑
- 否则若 "<id>" 是任何 Material id → 中疑
- 无法 100% 精确但足以捕获典型违规

(完整 AST 版见 Stage 3 清洁工作)
"""
from __future__ import annotations

import re
from typing import Any

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind

from ._shared import build_subscription_graph


# 匹配 output={"..."  或 output={'...'
_NESTED_OUTPUT_RE = re.compile(
    r"""output\s*=\s*\{\s*["']([a-zA-Z][\w.\-]*)["']\s*:""",
    re.MULTILINE,
)


class VerdictOutputFlatCheckerWorker(Worker):
    """诊断 Verdict.output 平铺合规 (R-23 · 禁嵌套 {<material_id>: ...})."""

    DESCRIPTION = (
        "订阅 doctor.blackboard.audit_request · 动态 import Team · "
        "对每个 Worker.run 源码正则扫 `output={\"<material_id>\":` 嵌套反模式, "
        "若嵌套键是该 Worker 自己的 FORMAT_OUT 或 Team 任一 Material id → 疑似违规. "
        "产出 doctor.blackboard.output_flat_report (sink)."
    )
    FORMAT_IN = "doctor.blackboard.audit_request"
    FORMAT_OUT = "doctor.blackboard.output_flat_report"

    def run(self, input_data: dict[str, Any]) -> Verdict:
        req = input_data.get("doctor.blackboard.audit_request", input_data)
        team_path = req.get("team_module_path", "")
        if not team_path:
            return Verdict(kind=VerdictKind.FAIL, diagnosis="team_module_path 缺失")

        try:
            g = build_subscription_graph(team_path)
        except Exception as exc:
            return Verdict(
                kind=VerdictKind.FAIL,
                diagnosis=f"加载 Team 失败: {type(exc).__name__}: {exc}",
            )

        material_ids = {m.id for m in g.materials}

        findings: list[dict] = []
        for w in g.workers:
            if not w.run_source:
                continue
            for match in _NESTED_OUTPUT_RE.finditer(w.run_source):
                suspect_key = match.group(1)
                # 判严重度
                is_self_out = suspect_key == w.format_out
                is_material = suspect_key in material_ids
                if not (is_self_out or is_material):
                    continue  # 非 Material id 字符串 (可能是普通数据字段) 跳过
                findings.append({
                    "worker_class": w.name,
                    "file": w.source_file,
                    "suspect_key": suspect_key,
                    "is_self_format_out": is_self_out,
                    "violation": (
                        f"R-23 · output 疑似嵌套 {{'{suspect_key}': ...}}"
                        + (" (本 Worker FORMAT_OUT 自嵌套)" if is_self_out else " (其他 Material id)")
                    ),
                    "severity": "HIGH" if is_self_out else "MEDIUM",
                })

        return Verdict(
            kind=VerdictKind.PASS,
            diagnosis=f"output 平铺扫描完成: {len(g.workers)} Worker, {len(findings)} 违规",
            output={
                "team_module_path": team_path,
                "findings": findings,
                "scanned_count": len(g.workers),
                "violation_count": len(findings),
            },
        )
