# [OMNI] origin=ai-ide domain=slidecast/routers ts=2026-06-20T00:00:00Z type=router status=active
# [OMNI] summary="LLM 节点: Outline(产讲解大纲) + AuthorIR(产会动的 deck IR)。失败降级出最小 IR, 不炸管线。"
# [OMNI] why="IR-first 的两个 LLM 节点。便宜档授内容, schema 约束, safe_json 降级。"
# [OMNI] tags=slidecast,router,llm,outline,author
"""slidecast LLM 节点: Outline + AuthorIR。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.routing.router import Router

from .. import prompts
from .._llm import safe_json


class Outline(Router):
    """文章 → 讲解大纲(plan)。失败降级为空 plan(AuthorIR 可只靠原文)。"""

    DESCRIPTION = "授大纲: 文章→讲解节奏 plan(便宜档, 失败降级)"
    FORMAT_IN = "slidecast.brief"
    FORMAT_OUT = "slidecast.outline"
    REQUIRED_CONTEXT = ["title", "run_dir"]

    def run(self, input_data: Any) -> Verdict:
        ctx = input_data if isinstance(input_data, dict) else {}
        run_dir = Path(ctx["run_dir"])
        body = ctx.get("body") or ""
        outline = None
        if body.strip():
            outline = safe_json(
                prompts.OUTLINE_SYSTEM,
                {"title": ctx.get("title", ""), "oneliner": ctx.get("oneliner", ""),
                 "article": body[:8000]},
                prompts.OUTLINE_SCHEMA, caller="slidecast.outline", max_tokens=2000, default=None,
            )
        (run_dir / "outline.json").write_text(
            json.dumps(outline or {}, ensure_ascii=False, indent=2), encoding="utf-8")
        out = dict(ctx)
        out["outline"] = outline
        return Verdict(
            kind=VerdictKind.PASS, output=out,
            diagnosis=f"大纲{'就绪 ' + str(len((outline or {}).get('beats') or [])) + ' 页' if outline else '降级(只靠原文)'}",
            granted_tags=["domain.slidecast", "stage.outline"],
        )


class AuthorIR(Router):
    """文章(+大纲)→ 会动的 deck IR。失败降级为最小 IR。"""

    DESCRIPTION = "授 slide IR: 文章→结构化会动 deck(主档, 失败降级最小 IR)"
    FORMAT_IN = "slidecast.outline"
    FORMAT_OUT = "slidecast.deck_ir"
    REQUIRED_CONTEXT = ["title", "run_dir"]

    def run(self, input_data: Any) -> Verdict:
        ctx = input_data if isinstance(input_data, dict) else {}
        run_dir = Path(ctx["run_dir"])
        title = ctx.get("title", "演示")
        body = ctx.get("body") or ""
        oneliner = ctx.get("oneliner", "")
        source_label = ctx.get("source_label", "")

        deck = None
        if body.strip():
            deck = safe_json(
                prompts.AUTHOR_SYSTEM,
                {"title": title, "oneliner": oneliner, "source": source_label,
                 "outline": ctx.get("outline"), "article": body[:12000]},
                prompts.AUTHOR_SCHEMA, model=prompts.MID_MODEL,
                caller="slidecast.author", max_tokens=6000, default=None,
            )

        degraded = False
        if not deck or not isinstance(deck, dict) or not deck.get("slides"):
            degraded = True
            deck = _fallback_ir(title, oneliner, body, source_label)

        (run_dir / "deck_ir.json").write_text(
            json.dumps(deck, ensure_ascii=False, indent=2), encoding="utf-8")
        out = dict(ctx)
        out["deck_ir"] = deck
        out["author_degraded"] = degraded
        n = len(deck.get("slides") or [])
        return Verdict(
            kind=VerdictKind.PASS, output=out,
            diagnosis=f"deck IR {'降级' if degraded else '就绪'}: {n} 页",
            granted_tags=["domain.slidecast", "stage.deck_ir"],
        )


def _fallback_ir(title: str, oneliner: str, body: str, source_label: str) -> dict:
    """LLM 不可用时的最小可渲染 IR: 封面 + 按 markdown 二级标题切几页 + 收尾。"""
    import re
    slides: list[dict] = [{"layout": "cover", "title": title, "subtitle": oneliner}]
    # 取正文里的 ## 段标题当分页
    heads = re.findall(r"^##\s+(.+)$", body, flags=re.MULTILINE)[:8]
    for h in heads:
        slides.append({"layout": "statement", "title": h.strip()})
    slides.append({"layout": "end", "title": "完",
                   "info": f"据 {source_label or 'colorc.cc 原文'} 自动生成(LLM 降级版)"})
    return {"meta": {"title": title, "subtitle": oneliner,
                     "info": f"slidecast · {source_label}"}, "slides": slides}
