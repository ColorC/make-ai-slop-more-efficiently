# [OMNI] origin=claude-code domain=omnicompany/repair ts=2026-04-20T00:00:00Z type=router
# [OMNI] material_id="material:core.repair.router_context_extractor.deep_loader.py"
"""RouterSourceLoaderWorker — Repair Team Worker (Router 修复分组 · #2).

Worker 协议:
  FORMAT_IN  = diag.repair.issue-list
  FORMAT_OUT = diag.repair.source-context

职责: 深度提取 Router 补全所需的全量上下文 (class docstring / 直接访问 AST 分析 / pipeline 节点描述 / INPUT_KEYS)。
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind

from ._shared import (
    _DEFAULT_SOURCE_ROOT,
    analyze_direct_accesses,
    extract_class_docstring,
    extract_pipeline_node_desc,
)


class RouterSourceLoaderWorker(Worker):
    """深度提取 Router 补全所需的全量上下文:

    新增（相比简单的 class_source）:
      - class_docstring: 类 docstring (往往包含幂等原则/保护规则等设计意图)
      - direct_access_map: AST 分析 run() 中所有 input_data["key"] 直接访问
        每条含: key / line / context / usage_type / crash_if_missing / crash_if_empty
      - pipeline_node_desc: 该 Router 在 pipeline 中的 ValidatorSpec.description
      - input_keys_declared: 类声明的 INPUT_KEYS (若有)
    """

    DESCRIPTION = (
        "深度提取 Router 补全上下文：class docstring / 直接访问 AST 分析 / "
        "pipeline 节点 validator 描述 / INPUT_KEYS 声明"
    )
    FORMAT_IN = "diag.repair.issue-list"
    FORMAT_OUT = "diag.repair.source-context"

    def run(self, input_data: Any) -> Verdict:
        if not input_data.get("b_class_issues"):
            return Verdict(kind=VerdictKind.PASS, confidence=1.0, output=input_data,
                           diagnosis="RouterSourceLoader: 无 B 类问题，跳过")

        router_class: str = input_data["router_class"]
        source_file: str = input_data["source_file"]
        source_root_str: str = input_data.get("source_root", str(_DEFAULT_SOURCE_ROOT))
        source_root = Path(source_root_str)
        extracted: dict = input_data.get("extracted", {})
        context: dict = input_data.get("context", {})

        try:
            src_path = Path(source_file)
            full_source = src_path.read_text(encoding="utf-8", errors="ignore")
        except Exception as e:
            return Verdict(kind=VerdictKind.FAIL, confidence=1.0,
                           output={**input_data, "error": f"源文件读取失败: {e}"},
                           diagnosis=f"RouterSourceLoader: 读取失败 {e}")

        class_source = self._extract_class_source(full_source, router_class)
        run_source: str = extracted.get("run_source", "")
        run_start_line: int = extracted.get("run_start_line", 0)

        class_docstring = extract_class_docstring(class_source)
        direct_access_map = analyze_direct_accesses(run_source or class_source)

        input_keys_declared: list[str] = extracted.get("INPUT_KEYS") or []
        if isinstance(input_keys_declared, str):
            input_keys_declared = [input_keys_declared]

        pipeline_brief: dict | None = context.get("pipeline_brief")
        pipeline_node_desc = extract_pipeline_node_desc(pipeline_brief, source_root)

        pipeline_purpose: str = context.get("pipeline_purpose", "")
        upstream_routers: list = context.get("upstream_routers", [])
        downstream_routers: list = context.get("downstream_routers", [])
        format_in_def: dict | None = context.get("format_in_def")
        format_out_def: dict | None = context.get("format_out_def")

        return Verdict(
            kind=VerdictKind.PASS, confidence=1.0,
            output={
                **input_data,
                "full_source": full_source,
                "class_source": class_source or run_source,
                "class_docstring": class_docstring,
                "run_source": run_source,
                "run_start_line": run_start_line,
                "direct_access_map": direct_access_map,
                "input_keys_declared": input_keys_declared,
                "pipeline_node_desc": pipeline_node_desc,
                "format_in_def": format_in_def,
                "format_out_def": format_out_def,
                "pipeline_brief": pipeline_brief,
                "pipeline_purpose": pipeline_purpose,
                "upstream_routers": upstream_routers,
                "downstream_routers": downstream_routers,
            },
            diagnosis=(
                f"RouterSourceLoader: {router_class} "
                f"docstring={'有' if class_docstring else '无'} "
                f"direct_accesses={len(direct_access_map)} "
                f"node_desc={'有' if pipeline_node_desc else '无'}"
            ),
        )

    @staticmethod
    def _extract_class_source(full_source: str, class_name: str) -> str:
        try:
            tree = ast.parse(full_source)
            lines = full_source.splitlines(keepends=True)
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and node.name == class_name:
                    start = node.lineno - 1
                    end = node.end_lineno if hasattr(node, "end_lineno") else len(lines)
                    return "".join(lines[start:end])
        except Exception:
            pass
        return ""
