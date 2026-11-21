# [OMNI] origin=claude-code domain=omnicompany/repair ts=2026-04-20T00:00:00Z type=router
# [OMNI] material_id="material:core.repair.description_planner.llm.py"
"""DescriptionPlannerWorker — Repair Team Worker (Router 修复分组 · #3).

Worker 协议:
  FORMAT_IN  = diag.repair.source-context
  FORMAT_OUT = diag.repair.desc-patch

职责: R-01 专属 — 只生成 DESCRIPTION 补全 diff, 不处理其他问题。
"""
from __future__ import annotations

import logging
from typing import Any

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind

from ._shared import _MODEL, extract_diff

logger = logging.getLogger(__name__)


_DESC_PROMPT = """你是 omnicompany LAP 协议专家。任务：只补全 Router 类的 DESCRIPTION 字段，不改任何其他内容。

# 目标 Router: {router_class}

## 当前 DESCRIPTION（太短，< 50 字）
{current_description}

## 类 Docstring（设计意图/算法原则，优先从这里提炼）
{class_docstring}

## Pipeline 业务目标
{pipeline_purpose}

## 该节点在 Pipeline 中的 Validator 描述
{pipeline_node_desc}

## FORMAT_IN 定义
{format_in_def}

## FORMAT_OUT 定义
{format_out_def}

## 上游 Router
{upstream_summary}

## 下游 Router
{downstream_summary}

## 补全规则（严格）
- DESCRIPTION 必须 ≥ 50 字（中文字符）
- 说明：做什么 / 从哪里来 / 到哪里去 / 核心算法或判断逻辑
- 如果 docstring 中有幂等性/保护规则/设计原则，必须压缩进 DESCRIPTION
- 禁止重复 FORMAT_IN/OUT 的 ID 字面量（用语义描述代替）
- 只改 DESCRIPTION = "..." 这一行，不改其他任何内容

## 输出
只输出 unified diff，不输出任何解释：

```diff
--- a/{router_class}
+++ b/{router_class}
@@ ... @@
-    DESCRIPTION = ...（当前值）
+    DESCRIPTION = ...（新值）
```
"""


class DescriptionPlannerWorker(Worker):
    """R-01 专属: 只生成 DESCRIPTION 补全 diff, 不处理其他问题。

    使用 class docstring + pipeline validator 描述 + FORMAT 语义三路信息,
    确保生成的 DESCRIPTION 不仅达标 (≥50字), 而且包含设计原则。
    """

    DESCRIPTION = (
        "R-01 专属 LLM 规划器：综合 class docstring / pipeline 节点描述 / FORMAT 语义，"
        "生成 DESCRIPTION 补全 diff（只改 DESCRIPTION，不动其他内容）"
    )
    FORMAT_IN = "diag.repair.source-context"
    FORMAT_OUT = "diag.repair.desc-patch"

    def __init__(self, model: str = _MODEL):
        self._model = model

    def run(self, input_data: Any) -> Verdict:
        issues = input_data.get("b_class_issues", [])
        r01_issues = [i for i in issues if i.get("check_id") == "R-01"]
        if not r01_issues:
            return Verdict(kind=VerdictKind.PASS, confidence=1.0,
                           output={**input_data, "desc_diff": None},
                           diagnosis="DescriptionPlanner: 无 R-01 问题，跳过")

        router_class: str = input_data["router_class"]
        extracted: dict = input_data.get("extracted", {})
        current_desc = extracted.get("description") or "(空)"
        class_docstring = input_data.get("class_docstring", "") or "(无 docstring)"
        pipeline_purpose = input_data.get("pipeline_purpose", "") or "(未记录)"
        pipeline_node_desc = input_data.get("pipeline_node_desc", "") or "(未找到)"
        upstream_routers = input_data.get("upstream_routers", [])
        downstream_routers = input_data.get("downstream_routers", [])
        format_in_def = input_data.get("format_in_def")
        format_out_def = input_data.get("format_out_def")

        def _fmt_def(d) -> str:
            if not d:
                return "(未找到)"
            return f"id={d.get('id','?')} | description={d.get('description','')!r}"

        def _fmt_routers(routers: list) -> str:
            if not routers:
                return "(无)"
            return "\n".join(f"- {r.get('class','?')}: {r.get('description','(无描述)')}" for r in routers)

        prompt = _DESC_PROMPT.format(
            router_class=router_class,
            current_description=current_desc,
            class_docstring=class_docstring[:1500],
            pipeline_purpose=pipeline_purpose,
            pipeline_node_desc=pipeline_node_desc,
            format_in_def=_fmt_def(format_in_def),
            format_out_def=_fmt_def(format_out_def),
            upstream_summary=_fmt_routers(upstream_routers),
            downstream_summary=_fmt_routers(downstream_routers),
        )

        diff = self._call_llm(router_class, prompt)
        if not diff:
            return Verdict(kind=VerdictKind.FAIL, confidence=0.5,
                           output={**input_data, "desc_diff": None},
                           diagnosis=f"DescriptionPlanner: {router_class} LLM 未生成 diff")

        return Verdict(kind=VerdictKind.PASS, confidence=0.9,
                       output={**input_data, "desc_diff": diff},
                       diagnosis=f"DescriptionPlanner: {router_class} DESCRIPTION diff 生成")

    def _call_llm(self, router_class: str, prompt: str) -> str | None:
        try:
            from omnicompany.runtime.llm.llm import LLMClient
            client = LLMClient(model=self._model)
            resp = client.call(
                messages=[{"role": "user", "content": prompt}],
                system="你是 omnicompany LAP 协议专家。只输出 unified diff，不输出任何解释。",
            )
            raw = resp.content[0].text if resp and resp.content else ""
            return extract_diff(raw)
        except Exception as e:
            logger.warning("DescriptionPlanner LLM failed for %s: %s", router_class, e)
            return None
