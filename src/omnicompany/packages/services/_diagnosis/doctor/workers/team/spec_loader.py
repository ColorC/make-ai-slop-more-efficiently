# [OMNI] origin=claude-code domain=omnicompany/doctor ts=2026-04-22T00:00:00Z type=worker
# [OMNI] material_id="material:diagnosis.doctor.worker.team.pipeline_spec_loader.py"
"""TeamSpecLoader — Pipeline 加载 Anchor (HARD, Stage 3 Clean Migration 2026-04-22).

Worker 协议:
  FORMAT_IN  = diag.team.request
  FORMAT_OUT = diag.team.extracted

诊断目标: Anchor 加载 pipeline.py 的 TeamSpec, 失败则 FAIL EMIT 最小档案.

PASS: 文件存在 + 至少一个 build_*() 返回 TeamSpec
FAIL: 文件不存在 / 无有效 build_* / 加载异常 / filter_id 不存在

可选参数:
  - pipeline_file: pipeline.py 路径 (必填)
  - pipeline_id: 过滤指定管线 (可选, 默认加载所有)
"""
from __future__ import annotations

from typing import Any

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind


class TeamSpecLoader(Worker):
    """从 pipeline.py 文件加载所有 TeamSpec 对象 (ANCHOR, 可短路)."""

    DESCRIPTION = (
        "从 pipeline.py 文件加载所有 TeamSpec 对象 (调用所有无参数 build_*() 函数). "
        "加载成功 → PASS 进入 4 个并行拓扑检查器; "
        "文件不存在/无有效 TeamSpec/加载异常 → FAIL EMIT 最小健康档案."
    )
    FORMAT_IN = "diag.team.request"
    FORMAT_OUT = "diag.team.extracted"

    def run(self, input_data: Any) -> Verdict:
        from omnicompany.packages.services._diagnosis.doctor.pipeline_topology import load_pipeline_from_file

        pipeline_file = input_data.get("pipeline_file", "")
        if not pipeline_file:
            return Verdict(
                kind=VerdictKind.FAIL, confidence=1.0,
                output={"pipeline_file": "", "load_error": "pipeline_file 未提供", "specs_data": []},
                diagnosis="PipelineSpecLoader: pipeline_file 为空",
            )

        try:
            specs = load_pipeline_from_file(pipeline_file)
        except FileNotFoundError:
            return Verdict(
                kind=VerdictKind.FAIL, confidence=1.0,
                output={"pipeline_file": pipeline_file, "load_error": f"文件不存在: {pipeline_file}", "specs_data": []},
                diagnosis=f"PipelineSpecLoader: {pipeline_file} 不存在",
            )
        except Exception as exc:
            return Verdict(
                kind=VerdictKind.FAIL, confidence=1.0,
                output={"pipeline_file": pipeline_file, "load_error": str(exc), "specs_data": []},
                diagnosis=f"PipelineSpecLoader: 加载失败 — {exc}",
            )

        if not specs:
            return Verdict(
                kind=VerdictKind.FAIL, confidence=1.0,
                output={"pipeline_file": pipeline_file, "load_error": "文件中未找到 TeamSpec (无有效 build_*() 函数)", "specs_data": []},
                diagnosis=f"PipelineSpecLoader: {pipeline_file} 无 build_* 函数",
            )

        filter_id = input_data.get("pipeline_id")
        if filter_id:
            specs = [s for s in specs if s.id == filter_id]
            if not specs:
                return Verdict(
                    kind=VerdictKind.FAIL, confidence=1.0,
                    output={"pipeline_file": pipeline_file, "load_error": f"未找到 pipeline_id='{filter_id}'", "specs_data": []},
                    diagnosis=f"PipelineSpecLoader: {filter_id} 不在文件中",
                )

        return Verdict(
            kind=VerdictKind.PASS,
            confidence=1.0,
            output={
                "pipeline_file": pipeline_file,
                "specs_data": [s.model_dump() for s in specs],
                "pipeline_ids": [s.id for s in specs],
                "spec_count": len(specs),
                "load_error": None,
            },
            diagnosis=f"PipelineSpecLoader: 加载 {len(specs)} 个管线 ({pipeline_file})",
        )
