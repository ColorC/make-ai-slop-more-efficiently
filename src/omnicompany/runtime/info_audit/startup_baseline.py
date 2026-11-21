# [OMNI] origin=claude-code domain=runtime/info_audit/startup_baseline ts=2026-04-15T00:00:00Z
# [OMNI] material_id="material:runtime.info_audit.startup_baseline_runner.implementation.py"
"""startup_baseline — 启动期 probe 全节点体检.

M2 (2026-04-15): 在 pipeline 上线 / PR review / 人工触发时运行一次,
对每个 LLM/SOFT 节点跑独立 probe (无真实 prompt, 只看 FORMAT 描述),
沉淀到 data/<domain>/probe_baseline.json 作为"事前体检基线".

与 post_hoc 对照:
  probe (本模块):  无上下文, 早筛, 廉价, 适合上线前
  post_hoc:        真实上下文, 精准, 贵, 适合生产运行

用法:
  from omnicompany.runtime.info_audit.startup_baseline import run_pipeline_probe_baseline
  result = run_pipeline_probe_baseline(pipeline, output_dir="data/domains/absorption")
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def run_pipeline_probe_baseline(
    pipeline: Any,
    *,
    output_dir: str | Path | None = None,
    include_kinds: tuple[str, ...] = ("LLM", "SOFT"),
    node_filter: list[str] | None = None,
) -> dict[str, Any]:
    """对 pipeline 每个 LLM/SOFT 节点跑独立 probe, 聚合结果.

    Args:
        pipeline: TeamSpec 对象 (有 nodes 属性).
        output_dir: 若指定, 落盘到 <output_dir>/probe_baseline.json.
        include_kinds: 只跑哪些 NodeKind (字符串形式, 避免 enum 强耦合).
        node_filter: 若非 None, 只跑列表里的 node_id.

    Returns:
        {"ts": ..., "pipeline": ..., "per_node": {node_id: <report dict>}, "summary": {...}}
    """
    from omnicompany.runtime.info_audit.probe import run_info_audit_probe_strict

    out: dict[str, Any] = {
        "ts": time.time(),
        "pipeline": getattr(pipeline, "id", "?"),
        "per_node": {},
        "summary": {
            "total": 0,
            "sufficient": 0,
            "partial": 0,
            "insufficient": 0,
            "unknown": 0,
        },
    }

    nodes = getattr(pipeline, "nodes", []) or []
    for node in nodes:
        node_id = getattr(node, "id", "?")
        node_kind = getattr(node.kind, "name", str(node.kind)) if hasattr(node, "kind") else "?"
        if include_kinds and node_kind not in include_kinds:
            continue
        if node_filter and node_id not in node_filter:
            continue

        # 提取 format_in / format_out / description
        anchor = getattr(node, "anchor", None)
        transformer = getattr(node, "transformer", None)
        fmt_in = getattr(anchor, "format_in", "") if anchor else getattr(transformer, "from_format", "")
        fmt_out = getattr(anchor, "format_out", "") if anchor else getattr(transformer, "to_format", "")
        desc = ""
        if anchor and getattr(anchor, "validator", None):
            desc = getattr(anchor.validator, "description", "") or ""
        if not desc and transformer:
            desc = getattr(transformer, "description", "") or ""

        try:
            report = run_info_audit_probe_strict(
                format_in=str(fmt_in) if fmt_in else "",
                format_out=str(fmt_out) if fmt_out else "",
                description=str(desc)[:500],
            )
        except Exception as e:
            logger.warning("probe failed for %s: %s", node_id, e)
            continue

        suff = report.sufficiency.value
        out["per_node"][node_id] = {
            "sufficiency": suff,
            "confidence_self": report.confidence_self,
            "missing_info": [m.model_dump() for m in report.missing_info],
            "concerns": report.concerns,
            "attention_focus": report.attention_focus,
            "format_in": str(fmt_in) if fmt_in else "",
            "format_out": str(fmt_out) if fmt_out else "",
            "kind": node_kind,
        }
        out["summary"]["total"] += 1
        key = {"sufficient": "sufficient", "partial": "partial",
               "insufficient": "insufficient", "unknown": "unknown"}.get(suff, "unknown")
        out["summary"][key] += 1

    if output_dir:
        from omnicompany.core.config import resolve_domain_data_dir
        od = Path(output_dir) if not isinstance(output_dir, str) or "/" in output_dir or "\\" in output_dir else resolve_domain_data_dir(output_dir)
        od = Path(od)
        od.mkdir(parents=True, exist_ok=True)
        path = od / "probe_baseline.json"
        try:
            path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.info("probe_baseline written: %s", path)
        except Exception as e:
            logger.warning("probe_baseline write failed: %s", e)

    return out
