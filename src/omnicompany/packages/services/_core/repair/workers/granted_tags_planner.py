# [OMNI] origin=claude-code domain=omnicompany/repair ts=2026-04-20T00:00:00Z type=router
# [OMNI] material_id="material:core.repair.granted_tags_planner.llm.py"
"""GrantedTagsPlannerWorker — Repair Team Worker (Router 修复分组 · #5).

Worker 协议:
  FORMAT_IN  = diag.repair.fail-patch
  FORMAT_OUT = diag.repair.tags-patch

职责: R-07 专属 — 只生成 granted_tags 添加 diff, 不处理其他问题。
"""
from __future__ import annotations

import logging
from typing import Any

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind

from ._shared import _MODEL, extract_diff

logger = logging.getLogger(__name__)


_TAGS_PROMPT = """你是 omnicompany LAP 协议专家。任务：只为 PASS Verdict 的 output 添加 granted_tags 字段。

# 目标 Router: {router_class}

## FORMAT_OUT 定义（决定授予什么 tag）
{format_out_def}

## 当前 PASS Verdict 代码段
```python
{pass_verdict_code}
```

## granted_tags 规范
- 格式：`"granted_tags": ["tag1", "tag2", ...]`，加到 PASS Verdict 的 output dict 中
- 常用命名空间：domain.xxx / content.xxx / state.xxx / lap.output.xxx / lap.analyst.xxx
- domain.xxx: FORMAT_OUT 前缀（demogame→domain.demogame）
- content.xxx: FORMAT_OUT 描述的内容类型（schema/diff/report/script...）
- state.xxx: 数据经过本节点后的状态（classified/analyzed/validated/generated...）
- lap.output.xxx: 本节点输出给下一阶段什么（classifier/analyzer/validator/generator...）
- 不超过 5 个 tag

## 输出
只输出 unified diff，不输出任何解释：

```diff
--- a/{router_class}
+++ b/{router_class}
@@ ... @@
 ...（context）
-                output=...,
+                output={{...existing..., "granted_tags": [...]}},
```
"""


class GrantedTagsPlannerWorker(Worker):
    """R-07 专属: 只生成 granted_tags 添加 diff, 不处理其他问题。

    只提供 FORMAT_OUT 定义 + PASS Verdict 代码段, 任务极小, LLM 专注推断 tag 语义。
    """

    DESCRIPTION = (
        "R-07 专属 LLM 规划器：从 FORMAT_OUT 语义推断 granted_tags，"
        "只向 PASS Verdict output 添加 tags 字段（不改其他内容）"
    )
    FORMAT_IN = "diag.repair.fail-patch"
    FORMAT_OUT = "diag.repair.tags-patch"

    def __init__(self, model: str = _MODEL):
        self._model = model

    def run(self, input_data: Any) -> Verdict:
        issues = input_data.get("b_class_issues", [])
        r07_issues = [i for i in issues if "R-07" in i.get("check_id", "")
                      or "granted_tags" in i.get("observation", "").lower()]
        if not r07_issues:
            return Verdict(kind=VerdictKind.PASS, confidence=1.0,
                           output={**input_data, "tags_diff": None},
                           diagnosis="GrantedTagsPlanner: 无 R-07 问题，跳过")

        router_class: str = input_data["router_class"]
        format_out_def = input_data.get("format_out_def")
        run_source: str = input_data.get("run_source", "")

        pass_verdict_code = self._extract_pass_verdict(run_source)

        def _fmt_def(d) -> str:
            if not d:
                return "(未找到)"
            return f"id={d.get('id','?')} | description={d.get('description','')!r} | tags={d.get('tags',[])}"

        prompt = _TAGS_PROMPT.format(
            router_class=router_class,
            format_out_def=_fmt_def(format_out_def),
            pass_verdict_code=pass_verdict_code[:1000] if pass_verdict_code else "(未提取到 PASS Verdict)",
        )

        diff = self._call_llm(router_class, prompt)
        if not diff:
            return Verdict(kind=VerdictKind.FAIL, confidence=0.5,
                           output={**input_data, "tags_diff": None},
                           diagnosis=f"GrantedTagsPlanner: {router_class} LLM 未生成 diff")

        return Verdict(kind=VerdictKind.PASS, confidence=0.9,
                       output={**input_data, "tags_diff": diff},
                       diagnosis=f"GrantedTagsPlanner: {router_class} granted_tags diff 生成")

    @staticmethod
    def _extract_pass_verdict(run_source: str) -> str:
        """从 run() 源码中提取 PASS Verdict 的代码段 (约 ±5 行上下文)。"""
        lines = run_source.splitlines()
        for i, line in enumerate(lines):
            if "VerdictKind.PASS" in line:
                start = max(0, i - 3)
                end = min(len(lines), i + 8)
                return "\n".join(lines[start:end])
        return ""

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
            logger.warning("GrantedTagsPlanner LLM failed for %s: %s", router_class, e)
            return None
