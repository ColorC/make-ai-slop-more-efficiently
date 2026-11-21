# [OMNI] origin=claude-code domain=omnicompany/doctor ts=2026-04-22T00:00:00Z type=worker
# [OMNI] material_id="material:diagnosis.doctor.worker.team.topology_one_shot_checker.py"
"""TeamTopologyCheck — 拓扑诊断整合入口 (HARD, Stage 3 Clean Migration 2026-04-22).

Worker 协议:
  FORMAT_IN  = diag.team.request
  FORMAT_OUT = diag.team.topology-report

诊断目标: 对 pipeline.py 文件执行静态拓扑分析 (一站式旧接口).
  - 检查注册表模式, 可按 ID 开关
  - 内置检查 (默认开): no_entry / isolated / dead_end / format_break / cycle /
    composite_missing / soft_hard_pairing / granted_tag_chain / maturity_consistency / duplicate_edge
  - 可选检查 (默认关): purpose_quality

与新版 5 个独立 check Worker 的区别:
  - 本 Worker 一站式产出 (输入 pipeline_file → 输出 topology-report)
  - 新版拆成 spec_loader → [5 个并行 check] → topo_health_writer
  - 两者保留以便: 旧接口兼容 vs 细粒度 fan-in 架构

输出: pipelines[] + total_findings + summary + checks_run + checks_skipped.
"""
from __future__ import annotations

from typing import Any

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind


class TeamTopologyCheck(Worker):
    """一站式拓扑诊断 (旧接口兼容)."""

    DESCRIPTION = (
        "对 pipeline.py 文件执行静态拓扑分析 (检查注册表模式, 可按 ID 开关). "
        "内置检查 (默认开): no_entry / isolated / dead_end / format_break / cycle / "
        "composite_missing / soft_hard_pairing / granted_tag_chain / "
        "maturity_consistency / duplicate_edge. "
        "可选检查 (默认关): purpose_quality. "
        "输入文件路径, 输出结构化 Finding 列表 + 每管线健康状态 (PASS/WARN/FAIL)."
    )
    FORMAT_IN = "diag.team.request"
    FORMAT_OUT = "diag.team.topology-report"

    def run(self, input_data: Any) -> Verdict:
        from omnicompany.packages.services._diagnosis.doctor.pipeline_topology import (
            load_pipeline_from_file,
            run_pipeline_checks,
            PIPELINE_CHECKS,
        )

        pipeline_file = input_data.get("pipeline_file", "")
        filter_id = input_data.get("pipeline_id")
        use_fmt_reg = input_data.get("use_format_registry", True)
        enabled_checks = input_data.get("enabled_checks")
        disabled_checks = input_data.get("disabled_checks", [])

        if not pipeline_file:
            return Verdict(
                kind=VerdictKind.FAIL, confidence=1.0,
                output={"error": "pipeline_file 未提供"},
                diagnosis="PipelineTopologyCheck: pipeline_file 为空",
            )

        format_registry = None
        if use_fmt_reg:
            try:
                from omnicompany.core.registry import discover
                from omnicompany.protocol.format import _default_registry  # type: ignore
                discover()
                format_registry = _default_registry
            except Exception:
                pass

        try:
            specs = load_pipeline_from_file(pipeline_file)
        except FileNotFoundError:
            return Verdict(
                kind=VerdictKind.FAIL, confidence=1.0,
                output={"error": f"文件不存在: {pipeline_file}"},
                diagnosis=f"PipelineTopologyCheck: {pipeline_file} 不存在",
            )
        except Exception as exc:
            return Verdict(
                kind=VerdictKind.FAIL, confidence=1.0,
                output={"error": str(exc)},
                diagnosis=f"PipelineTopologyCheck: 加载失败 — {exc}",
            )

        if not specs:
            return Verdict(
                kind=VerdictKind.FAIL, confidence=1.0,
                output={"error": "文件中未找到 TeamSpec"},
                diagnosis=f"PipelineTopologyCheck: {pipeline_file} 无 build_* 函数",
            )

        if filter_id:
            specs = [s for s in specs if s.id == filter_id]
            if not specs:
                return Verdict(
                    kind=VerdictKind.FAIL, confidence=1.0,
                    output={"error": f"未找到 pipeline_id='{filter_id}'"},
                    diagnosis=f"PipelineTopologyCheck: {filter_id} 不在文件中",
                )

        _LEVEL_ORDER = {"blocking": 0, "degrading": 1, "advisory": 2, "info": 3}
        pipeline_results = []
        total_findings = 0

        for spec in specs:
            findings = run_pipeline_checks(
                spec,
                enabled=enabled_checks,
                disabled=disabled_checks,
                format_registry=format_registry,
            )
            findings.sort(key=lambda x: _LEVEL_ORDER.get(x.level, 9))
            has_blocking = any(f.level == "blocking" for f in findings)
            has_degrading = any(f.level == "degrading" for f in findings)
            # 契约变更 #02 (2026-04-25): health_grade 废 · 改 topology_status 作语义标签
            topology_status = (
                "fail" if has_blocking else
                "warn" if has_degrading else
                "pass" if not findings else "info"
            )
            total_findings += len(findings)
            pipeline_results.append({
                "pipeline_id": spec.id,
                "pipeline_name": spec.name,
                "node_count": len(spec.nodes),
                "edge_count": len(spec.edges),
                "topology_status": topology_status,
                "finding_count": len(findings),
                "findings": [
                    {
                        "check_id": f.check_id,
                        "level": f.level,
                        "severity": f.severity,
                        "location": f.location,
                        "observation": f.observation,
                        "implication": f.implication,
                        "cross_refs": f.cross_refs,
                    }
                    for f in findings
                ],
            })

        any_fail = any(r["topology_status"] == "fail" for r in pipeline_results)
        any_warn = any(r["topology_status"] == "warn" for r in pipeline_results)
        summary = (
            f"检查 {len(specs)} 个管线, 共 {total_findings} 个 Finding. "
            f"{'有 blocking 问题 (FAIL)' if any_fail else '有 degrading 问题 (WARN)' if any_warn else '全部健康 (PASS)'}"
        )

        return Verdict(
            kind=VerdictKind.FAIL if any_fail else VerdictKind.PASS,
            confidence=1.0,
            output={
                "pipeline_file": pipeline_file,
                "pipelines": pipeline_results,
                "total_findings": total_findings,
                "summary": summary,
                "checks_run": enabled_checks or [c.id for c in PIPELINE_CHECKS if c.default_on],
                "checks_skipped": disabled_checks,
            },
            diagnosis=f"PipelineTopologyCheck: {summary}",
        )
