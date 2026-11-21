# [OMNI] origin=claude-code domain=omnicompany/doctor ts=2026-04-22T00:00:00Z type=worker
# [OMNI] material_id="material:diagnosis.doctor.worker.team.cross_pipeline_lineage_extractor.py"
"""TeamLineageExtractor — B2 跨管线 format 产消图 (HARD, Stage 3 2026-04-22).

Worker 协议:
  FORMAT_IN  = diag.lineage.request
  FORMAT_OUT = diag.lineage.report

诊断目标: 扫 source_root 下所有注册管线, 提取每个节点的 format_in / format_out,
  构建跨管线 format 产消图, 识别跨管线 Format 交接点 (A 产出 → B 消费).

输入参数:
  - source_root: str        — 源码根目录 (默认 src/omnicompany)
  - material_id:   str|None   — 只展示涉及此 Format 的条目
  - pipeline_id: str|None   — 只展示指定管线

输出 (diag.lineage.report):
  - pipeline_count / format_count
  - pipelines[]          — 每管线的 format_flow
  - formats{}            — 每 Format 的 producers/consumers
  - cross_pipeline_handoffs[]  — 跨管线交接点
"""
from __future__ import annotations

from typing import Any

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind


class TeamLineageExtractor(Worker):
    """跨管线 format 产消图提取 (Lineage B2)."""

    DESCRIPTION = (
        "扫描 source_root 下所有注册管线, 提取节点-Format 映射边 (lineage). "
        "构建跨管线 format 产消图, 识别 A 产出 → B 消费的跨管线交接点. "
        "支持 material_id / pipeline_id 过滤."
    )
    FORMAT_IN = "diag.lineage.request"
    FORMAT_OUT = "diag.lineage.report"

    def run(self, input_data: Any) -> Verdict:
        from omnicompany.packages.services._diagnosis.doctor.pipeline_topology import (
            discover_all_pipelines,
            extract_pipeline_lineage,
            PipelineLineage,
        )

        source_root = input_data.get("source_root", "src/omnicompany")
        filter_format = input_data.get("material_id")
        filter_pip = input_data.get("pipeline_id")

        try:
            all_specs = discover_all_pipelines(source_root)
        except Exception as exc:
            return Verdict(
                kind=VerdictKind.FAIL, confidence=1.0,
                output={"error": f"discover_all_pipelines 失败: {exc}"},
                diagnosis=f"PipelineLineage: source_root={source_root} 扫描失败",
            )

        lineages: list[PipelineLineage] = []
        for src_file, spec in all_specs:
            if filter_pip and spec.id != filter_pip:
                continue
            lineages.append(extract_pipeline_lineage(spec, src_file))

        format_producers: dict[str, list[dict]] = {}
        format_consumers: dict[str, list[dict]] = {}

        for lin in lineages:
            for edge in lin.format_edges:
                ref = {
                    "pipeline_id": lin.pipeline_id, "node_id": edge.node_id,
                    "node_kind": edge.node_kind,
                }
                if edge.format_out and edge.format_out != "any":
                    format_producers.setdefault(edge.format_out, []).append(ref)
                if edge.format_in:
                    fins = edge.format_in if isinstance(edge.format_in, list) else [edge.format_in]
                    for fin in fins:
                        if fin and fin != "any":
                            format_consumers.setdefault(fin, []).append(ref)

        all_format_ids = sorted(set(format_producers) | set(format_consumers))
        if filter_format:
            all_format_ids = [f for f in all_format_ids if f == filter_format]

        cross_pipeline: list[dict] = []
        for fmt_id in all_format_ids:
            prod_pips = {p["pipeline_id"] for p in format_producers.get(fmt_id, [])}
            cons_pips = {c["pipeline_id"] for c in format_consumers.get(fmt_id, [])}
            if prod_pips and cons_pips and prod_pips != cons_pips:
                cross_pipeline.append({
                    "material_id": fmt_id,
                    "produced_by": sorted(prod_pips),
                    "consumed_by": sorted(cons_pips),
                })

        output = {
            "source_root": source_root,
            "pipeline_count": len(lineages),
            "format_count": len(all_format_ids),
            "pipelines": [
                {
                    "pipeline_id": lin.pipeline_id,
                    "pipeline_name": lin.pipeline_name,
                    "source_file": lin.source_file,
                    "node_count": len(lin.format_edges),
                    "format_flow": [
                        {
                            "node_id": e.node_id, "node_kind": e.node_kind,
                            "format_in": e.format_in, "format_out": e.format_out,
                        }
                        for e in lin.format_edges
                    ],
                }
                for lin in lineages
            ],
            "formats": {
                fmt_id: {
                    "producers": format_producers.get(fmt_id, []),
                    "consumers": format_consumers.get(fmt_id, []),
                }
                for fmt_id in all_format_ids
            },
            "cross_pipeline_handoffs": cross_pipeline,
        }

        return Verdict(
            kind=VerdictKind.PASS, confidence=1.0,
            output=output,
            diagnosis=(
                f"PipelineLineage: {len(lineages)} 管线, "
                f"{len(all_format_ids)} 个 Format, "
                f"{len(cross_pipeline)} 个跨管线交接点"
            ),
        )
